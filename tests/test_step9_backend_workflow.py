from datetime import time, timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import NoReverseMatch
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from jobs.choices import JobAssignmentStatus, ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob
from jobs.services.dispatch import dispatch_job_to_shop
from payments.models import Payment
from payments.services import handle_stk_callback, initiate_stk_push
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import calculate_financial_split, create_quote_financial_split
from quotes.choices import (
    CalculatorDraftContext,
    CalculatorDraftIntent,
    CalculatorDraftStatus,
    QuoteOfferStatus,
    QuoteStatus,
)
from quotes.financial_split_actor_serializers import (
    QuoteFinancialSplitBrokerSerializer,
    QuoteFinancialSplitClientSerializer,
    QuoteFinancialSplitShopSerializer,
)
from quotes.models import CalculatorDraft, ProductionOption, Quote, QuoteFinancialSplit, QuoteRequest
from quotes.services_workflow import (
    create_production_option_from_calculator,
    create_quote_response,
    save_calculator_draft,
    send_calculator_draft_to_shops,
)
from quotes.acceptance import accept_quote_for_payment
from services.pricing.calculator_preview import build_public_calculator_preview
from shops.models import Shop


User = get_user_model()


class Step9BackendWorkflowTestCase(TestCase):
    def setUp(self):
        self.client_user = User.objects.create_user(
            email="step9-client@example.com",
            password="pass",
            role=User.Role.CLIENT,
        )
        self.broker = User.objects.create_user(
            email="step9-broker@example.com",
            password="pass",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.shop_owner = User.objects.create_user(
            email="step9-shop@example.com",
            password="pass",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            name="Step 9 Print Shop",
            owner=self.shop_owner,
            is_active=True,
            opening_time=time(8, 0),
            closing_time=time(18, 0),
        )
        self.policy = PlatformFeePolicy.objects.create(
            name="Step 9 policy",
            is_active=True,
            printer_fee_rate=Decimal("0.0500"),
            broker_margin_fee_rate=Decimal("0.1500"),
            add_platform_fee_on_top=False,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            assigned_manager=self.broker,
            customer_name="Step Client",
            customer_email="step9-client@example.com",
            customer_phone="+254700000000",
            status=QuoteStatus.SUBMITTED,
        )
        self.production_option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=self.broker,
            status=ProductionOption.SELECTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_option=self.production_option,
            created_by=self.broker,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1500.00"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )

    def _split(self):
        return create_quote_financial_split(
            quote=self.quote,
            production_option=self.production_option,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            policy=self.policy,
        )

    def _success_callback(self, stk, *, amount="1500.00", receipt="STEP9RCPT"):
        return {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": stk.checkout_request_id,
                    "MerchantRequestID": stk.merchant_request_id,
                    "ResultCode": 0,
                    "ResultDesc": "Success",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": amount},
                            {"Name": "MpesaReceiptNumber", "Value": receipt},
                        ]
                    },
                }
            }
        }

    def _assert_no_payload_keys(self, payload, forbidden):
        if isinstance(payload, dict):
            for key, value in payload.items():
                self.assertNotIn(key, forbidden)
                self._assert_no_payload_keys(value, forbidden)
        elif isinstance(payload, list):
            for item in payload:
                self._assert_no_payload_keys(item, forbidden)

    def test_platform_fee_policy_calculates_and_enforces_markup_cap(self):
        split = calculate_financial_split(
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            policy=self.policy,
        )

        self.assertEqual(split["printer_side_fee"], Decimal("50.00"))
        self.assertEqual(split["broker_margin_fee"], Decimal("75.00"))
        self.assertEqual(split["printy_fee"], Decimal("125.00"))
        self.assertEqual(split["shop_payout"], Decimal("1000.00"))
        self.assertEqual(split["broker_payout"], Decimal("375.00"))
        self.assertEqual(split["client_total"], Decimal("1500.00"))

        with self.assertRaises(ValidationError):
            calculate_financial_split(
                production_cost=Decimal("1000.00"),
                broker_client_price=Decimal("5000.00"),
                policy=self.policy,
            )

    def test_quote_split_is_immutable_snapshot_and_actor_visibility_is_partitioned(self):
        split = self._split()

        self.assertEqual(split.policy_used, self.policy)
        self.assertEqual(split.production_option, self.production_option)
        self.assertEqual(split.max_allowed_client_price, Decimal("4000.00"))
        self.assertEqual(QuoteFinancialSplitClientSerializer(split).data, {})
        self.assertEqual(set(QuoteFinancialSplitShopSerializer(split).data.keys()), {"id", "shop_payout"})

        broker_payload = QuoteFinancialSplitBrokerSerializer(split).data
        for key in ("production_cost", "printy_fee", "shop_payout", "broker_payout", "client_total"):
            self.assertIn(key, broker_payload)

    def test_client_draft_send_cannot_route_directly_to_shops(self):
        draft = CalculatorDraft.objects.create(
            user=self.client_user,
            title="Client managed draft",
            status=CalculatorDraftStatus.DRAFT,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.CLIENT_QUOTE_REQUEST,
            calculator_inputs_snapshot={"product_type": "flyer", "quantity": 100},
        )

        with self.assertRaises(PermissionDenied):
            send_calculator_draft_to_shops(
                draft=draft,
                shops=[self.shop],
                request_details_snapshot={"customer_name": "Step Client", "selected_shop_ids": [self.shop.id]},
            )

        requests = send_calculator_draft_to_shops(
            draft=draft,
            shops=[],
            request_details_snapshot={"customer_name": "Step Client"},
        )
        self.assertEqual(len(requests), 1)
        self.assertIsNone(requests[0].shop)
        self.assertEqual(requests[0].status, QuoteStatus.SUBMITTED)

    def test_calculator_routing_api_endpoints_do_not_allow_client_shop_routing(self):
        api_client = APIClient()
        preview_count = CalculatorDraft.objects.count()
        preview_response = api_client.post(
            reverse("calculator-public-preview"),
            {
                "product_type": "flyer",
                "quantity": 100,
                "finished_size": "A5",
                "print_sides": "SIMPLEX",
                "requested_gsm": 150,
            },
            format="json",
        )
        self.assertEqual(preview_response.status_code, 200)
        self.assertEqual(CalculatorDraft.objects.count(), preview_count)
        preview_payload = preview_response.json()
        for key in ("shop_id", "shop_name", "production_cost", "shop_payout", "broker_payout", "printy_fee"):
            self.assertNotIn(key, preview_payload)

        api_client.force_authenticate(user=self.client_user)
        rejected_draft_response = api_client.post(
            reverse("calculator-drafts"),
            {
                "title": "Client API draft",
                "calculator_inputs_snapshot": {
                    "product_type": "flyer",
                    "quantity": 100,
                    "finished_size": "A5",
                    "print_sides": "SIMPLEX",
                    "requested_gsm": 150,
                },
                "request_details_snapshot": {"shop_id": self.shop.id},
            },
            format="json",
        )
        self.assertEqual(rejected_draft_response.status_code, 400)

        draft_response = api_client.post(
            reverse("calculator-drafts"),
            {
                "title": "Client API draft",
                "calculator_inputs_snapshot": {
                    "product_type": "flyer",
                    "quantity": 100,
                    "finished_size": "A5",
                    "print_sides": "SIMPLEX",
                    "requested_gsm": 150,
                },
                "request_details_snapshot": {"customer_name": "API Client"},
            },
            format="json",
        )
        self.assertEqual(draft_response.status_code, 201)
        draft = CalculatorDraft.objects.get(pk=draft_response.json()["id"])
        self.assertFalse(hasattr(draft, "shop"))

        intake_response = api_client.post(
            reverse("intake-submit"),
            {
                "draft_id": draft.id,
                "selected_manager_id": self.broker.id,
                "request_details_snapshot": {
                    "customer_name": "API Client",
                    "customer_email": self.client_user.email,
                    "shop_id": self.shop.id,
                    "selected_shop_ids": [self.shop.id],
                },
            },
            format="json",
        )
        self.assertEqual(intake_response.status_code, 201)
        routed_request = QuoteRequest.objects.get(pk=intake_response.json()["intake_id"])
        self.assertIsNone(routed_request.shop)

        api_client.force_authenticate(user=self.broker)
        source_response = api_client.post(
            reverse("partner-production-options"),
            {
                "quote_request_id": routed_request.id,
                "shop_id": self.shop.id,
                "calculator_context": CalculatorDraftContext.MANAGER_DASHBOARD,
                "intent": CalculatorDraftIntent.SOURCE_PRODUCTION,
                "production_cost": "1200.00",
                "estimated_turnaround_hours": 24,
                "capacity_status": "available",
                "pricing_snapshot": {"source": "api-test"},
            },
            format="json",
        )
        self.assertEqual(source_response.status_code, 201)
        sourced_option = ProductionOption.objects.get(pk=source_response.json()["id"])
        self.assertEqual(sourced_option.shop, self.shop)
        self.assertEqual(sourced_option.quote_request, routed_request)

    def test_manager_can_source_production_option_from_calculator_context(self):
        option = create_production_option_from_calculator(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.broker,
            production_cost=Decimal("1200.00"),
            calculator_context=CalculatorDraftContext.MANAGER_DASHBOARD,
            intent=CalculatorDraftIntent.SOURCE_PRODUCTION,
            estimated_turnaround_hours=24,
            capacity_status="available",
            pricing_snapshot={"method": "calculator"},
        )

        self.assertEqual(option.shop, self.shop)
        self.assertEqual(option.production_cost, Decimal("1200.00"))

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_acceptance_payment_callback_and_dispatch_follow_canonical_workflow(self):
        self._split()
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        self.assertEqual(payment.amount, Decimal("1500.00"))
        self.assertEqual(payment.status, Payment.STATUS_PENDING)

        with self.assertRaises(ValidationError):
            dispatch_job_to_shop(
                managed_job=ManagedJob.objects.create(
                    source_quote_request=self.quote_request,
                    source_quote=self.quote,
                    client=self.client_user,
                    broker=self.broker,
                    assigned_shop=self.shop,
                ),
                dispatched_by=self.broker,
            )

        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")
        handle_stk_callback(callback_payload=self._success_callback(stk))

        payment.refresh_from_db()
        managed_job = ManagedJob.objects.get(source_quote=self.quote)
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(payment.managed_job, managed_job)
        self.assertEqual(managed_job.status, ManagedJobStatus.PAYMENT_CONFIRMED)
        self.assertEqual(managed_job.payment_status, ManagedJobPaymentStatus.CONFIRMED)

        assignment = dispatch_job_to_shop(managed_job=managed_job, dispatched_by=self.broker)
        self.assertEqual(assignment.assigned_shop, self.shop)
        self.assertEqual(assignment.shop_payout, self.quote.financial_split.shop_payout)
        self.assertEqual(JobAssignment.objects.count(), 1)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_client_cannot_dispatch_job_through_api(self):
        self._split()
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")
        handle_stk_callback(callback_payload=self._success_callback(stk))
        managed_job = ManagedJob.objects.get(source_quote=self.quote)

        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse("dashboard-partner-job-dispatch", kwargs={"pk": managed_job.pk}),
            {},
            content_type="application/json",
        )

        self.assertIn(response.status_code, {401, 403})
        self.assertEqual(JobAssignment.objects.count(), 0)

    def test_admin_registers_canonical_workflow_models(self):
        for model in (CalculatorDraft, QuoteRequest, ProductionOption, Quote, QuoteFinancialSplit, Payment, ManagedJob):
            self.assertIn(model, admin.site._registry)

    def test_admin_changelists_use_canonical_models_and_hide_sensitive_snapshots(self):
        removed_model_names = {"JobPayment", "JobSettlementSplit", "PartnerClient", "ProductionPaperSize"}
        registered_model_names = {model.__name__ for model in admin.site._registry}
        self.assertTrue(removed_model_names.isdisjoint(registered_model_names))

        sensitive_fields = {"request_snapshot", "response_snapshot", "raw_callback", "raw_response", "pricing_snapshot"}
        for model in (CalculatorDraft, QuoteRequest, ProductionOption, Quote, QuoteFinancialSplit, Payment, ManagedJob):
            model_admin = admin.site._registry[model]
            self.assertTrue(sensitive_fields.isdisjoint(set(getattr(model_admin, "list_display", []))))

        superuser = User.objects.create_superuser(email="admin-step9@example.com", password="pass")
        admin_client = APIClient()
        self.assertTrue(admin_client.login(email=superuser.email, password="pass"))

        for model in (CalculatorDraft, QuoteRequest, ProductionOption, Quote, QuoteFinancialSplit, Payment, ManagedJob):
            try:
                url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
            except NoReverseMatch:
                self.fail(f"Missing admin changelist for {model.__name__}")
            response = admin_client.get(url)
            self.assertNotEqual(response.status_code, 500, f"{model.__name__} admin returned 500")

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_full_guest_to_client_to_production_tracking_workflow(self):
        preview_payload = {
            "product_type": "flyer",
            "quantity": 250,
            "finished_size": "A5",
            "print_sides": "SIMPLEX",
            "requested_gsm": 150,
        }
        preview = build_public_calculator_preview(preview_payload)
        self.assertEqual(preview["mode"], "calculator_public_preview")
        self.assertEqual(CalculatorDraft.objects.count(), 0)

        session_key = "guest-step9-session"
        guest_draft = save_calculator_draft(
            user=None,
            guest_session_key=session_key,
            title="Guest flyer draft",
            calculator_context=CalculatorDraftContext.PUBLIC_GUEST,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=preview_payload,
            request_details_snapshot={"customer_name": "Guest Client", "notes": "Use bright stock"},
        )
        self.assertIsNone(guest_draft.user)
        self.assertEqual(guest_draft.guest_session_key, session_key)

        signed_up_client = User.objects.create_user(
            email="claimed-step9-client@example.com",
            password="pass",
            role=User.Role.CLIENT,
            name="Claimed Client",
        )
        api_client = APIClient()
        api_client.force_authenticate(user=signed_up_client)
        claim_response = api_client.post(
            reverse("calculator-draft-claim"),
            {"session_key": session_key},
            format="json",
        )
        self.assertEqual(claim_response.status_code, 200)
        guest_draft.refresh_from_db()
        self.assertEqual(guest_draft.user, signed_up_client)
        self.assertEqual(guest_draft.guest_session_key, "")

        with self.assertRaises(PermissionDenied):
            send_calculator_draft_to_shops(
                draft=guest_draft,
                shops=[self.shop],
                request_details_snapshot={"selected_shop_ids": [self.shop.id]},
            )

        requests = send_calculator_draft_to_shops(
            draft=guest_draft,
            shops=[],
            request_details_snapshot={
                "customer_name": "Claimed Client",
                "customer_email": signed_up_client.email,
                "customer_phone": "+254711111111",
                "assigned_manager_id": self.broker.id,
            },
        )
        quote_request = requests[0]
        self.assertIsNone(quote_request.shop)
        self.assertEqual(quote_request.created_by, signed_up_client)
        self.assertEqual(quote_request.assigned_manager, self.broker)
        self.assertEqual(quote_request.status, QuoteStatus.SUBMITTED)

        production_option = create_production_option_from_calculator(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.broker,
            production_cost=Decimal("1000.00"),
            calculator_context=CalculatorDraftContext.MANAGER_DASHBOARD,
            intent=CalculatorDraftIntent.SOURCE_PRODUCTION,
            estimated_turnaround_hours=24,
            capacity_status="available",
            pricing_snapshot={"preview": preview},
        )
        quote = create_quote_response(
            quote_request=quote_request,
            shop=self.shop,
            user=self.broker,
            status=QuoteOfferStatus.SENT,
            response_snapshot={"customer_pricing": {"estimated_total": "1500.00"}},
            revised_pricing_snapshot={"production_cost": "1000.00", "broker_client_price": "1500.00"},
            total=Decimal("1500.00"),
            note="Prepared by broker",
            turnaround_hours=24,
        )
        quote.production_option = production_option
        quote.save(update_fields=["production_option", "updated_at"])
        split = create_quote_financial_split(
            quote=quote,
            production_option=production_option,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            policy=self.policy,
        )
        self.assertEqual(split.shop_payout, Decimal("1000.00"))
        self.assertEqual(split.client_total, Decimal("1500.00"))

        accepted_quote, payment = accept_quote_for_payment(quote=quote, accepted_by=signed_up_client)
        self.assertEqual(accepted_quote.status, QuoteOfferStatus.ACCEPTED)
        self.assertEqual(payment.amount, split.client_total)
        self.assertEqual(payment.expected_amount, split.client_total)

        stk_response = api_client.post(
            reverse("payment-stk-push"),
            {"payment_id": payment.id, "phone_number": "+254711111111"},
            format="json",
        )
        self.assertEqual(stk_response.status_code, 201)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)

        callback_response = api_client.post(
            reverse("payment-mpesa-callback"),
            self._success_callback(payment.mpesa_stk_requests.first()),
            format="json",
        )
        self.assertEqual(callback_response.status_code, 200)
        payment.refresh_from_db()
        managed_job = ManagedJob.objects.get(source_quote=quote)
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(payment.managed_job, managed_job)
        self.assertEqual(managed_job.client, signed_up_client)
        self.assertEqual(managed_job.assigned_shop, self.shop)

        assignment = dispatch_job_to_shop(managed_job=managed_job, dispatched_by=self.broker)
        self.assertEqual(assignment.shop_payout, split.shop_payout)

        api_client.force_authenticate(user=self.shop_owner)
        assignments_response = api_client.get(reverse("shop-assignments"))
        self.assertEqual(assignments_response.status_code, 200)
        assignment_payload = assignments_response.json()[0]
        self.assertEqual(assignment_payload["id"], assignment.id)
        self.assertEqual(assignment_payload["payout_amount"], "1000.00")
        self.assertNotIn("broker_payout", assignment_payload)
        self.assertNotIn("printy_fee", assignment_payload)
        self.assertNotIn("client_total", assignment_payload)

        accept_response = api_client.post(
            reverse("job-assignment-accept", kwargs={"pk": assignment.id}),
            {"note": "Accepted by shop"},
            format="json",
        )
        self.assertEqual(accept_response.status_code, 200)
        in_production_response = api_client.post(
            reverse("job-assignment-in-production", kwargs={"pk": assignment.id}),
            {"note": "Printing"},
            format="json",
        )
        self.assertEqual(in_production_response.status_code, 200)
        ready_response = api_client.post(
            reverse("job-assignment-ready", kwargs={"pk": assignment.id}),
            {"note": "Ready for pickup"},
            format="json",
        )
        self.assertEqual(ready_response.status_code, 200)
        assignment.refresh_from_db()
        managed_job.refresh_from_db()
        self.assertEqual(assignment.status, JobAssignmentStatus.READY)
        self.assertEqual(managed_job.status, ManagedJobStatus.READY)

        api_client.force_authenticate(user=None)
        tracking_response = api_client.get(
            reverse("public-managed-job-track", kwargs={"token": managed_job.tracking_token})
        )
        self.assertEqual(tracking_response.status_code, 200)
        tracking_payload = tracking_response.json()
        self.assertIn("job_status", tracking_payload)
        for forbidden_key in (
            "assigned_shop",
            "shop_payout",
            "production_cost",
            "printy_fee",
            "broker_payout",
            "client_total",
            "raw_callback",
        ):
            self.assertNotIn(forbidden_key, tracking_payload)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_visibility_api_responses_partition_financial_data_by_actor(self):
        self._split()
        accepted_quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")
        handle_stk_callback(callback_payload=self._success_callback(stk))
        managed_job = ManagedJob.objects.get(source_quote=self.quote)
        assignment = dispatch_job_to_shop(managed_job=managed_job, dispatched_by=self.broker)

        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        client_response = api_client.get(reverse("client-response-list"))
        self.assertEqual(client_response.status_code, 200)
        client_payload = client_response.json()[0]
        for key in ("production_cost", "shop_payout", "broker_payout", "printy_fee", "shop_id", "shop_name"):
            self.assertNotIn(key, str(client_payload))

        api_client.force_authenticate(user=self.shop_owner)
        shop_response = api_client.get(reverse("shop-assignments"))
        self.assertEqual(shop_response.status_code, 200)
        shop_payload = shop_response.json()[0]
        self.assertEqual(shop_payload["payout_amount"], "1000.00")
        for key in ("client_total", "broker_margin", "printy_fee"):
            self.assertNotIn(key, shop_payload)

        broker_payload = QuoteFinancialSplitBrokerSerializer(accepted_quote.financial_split).data
        for key in ("production_cost", "gross_margin", "printy_fee", "broker_payout", "client_total"):
            self.assertIn(key, broker_payload)

        api_client.force_authenticate(user=None)
        public_preview = api_client.post(
            reverse("calculator-public-preview"),
            {
                "product_type": "flyer",
                "quantity": 100,
                "finished_size": "A5",
                "print_sides": "SIMPLEX",
                "requested_gsm": 150,
            },
            format="json",
        )
        self.assertEqual(public_preview.status_code, 200)
        self._assert_no_payload_keys(
            public_preview.json(),
            {"shop_id", "shop_name", "production_cost", "shop_payout", "broker_payout", "printy_fee"},
        )

        self.assertFalse(hasattr(__import__("jobs.models", fromlist=["JobPayment"]), "JobPayment"))
        self.assertTrue(QuoteFinancialSplit.objects.filter(quote=accepted_quote).exists())
