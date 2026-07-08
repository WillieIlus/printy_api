from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.services.system_accounts import ensure_printy_manager_user
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob
from notifications.models import Notification
from payments.models import MpesaSTKRequest, Payment
from payments.services import initiate_stk_push
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.acceptance import accept_quote_for_payment
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import CalculatorDraft, ProductionOption, Quote, QuoteRequest
from shops.models import Shop


User = get_user_model()


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ManagerSelectionRegressionTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client_user = User.objects.create_user(
            email="routing-client@example.com",
            password="pass",
            role=User.Role.CLIENT,
            name="Routing Client",
        )
        self.manager = User.objects.create_user(
            email="routing-manager@example.com",
            password="pass",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
            name="Routing Manager",
        )

    def _draft(self):
        return CalculatorDraft.objects.create(
            user=self.client_user,
            title="Routing draft",
            calculator_inputs_snapshot={
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x50mm",
            },
            request_details_snapshot={"customer_name": "Routing Client"},
        )

    def test_auto_choose_assigns_printy_manager_and_mode(self):
        printy_manager, _profile, _created = ensure_printy_manager_user(email="ops-routing@example.com")
        self.client.force_authenticate(user=self.client_user)

        response = self.client.post(
            "/api/intake/submit/",
            {
                "draft_id": self._draft().id,
                "manager_selection_mode": "printy_auto",
                "artwork_reference": "uploaded",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        quote_request = QuoteRequest.objects.get(pk=response.json()["intake_id"])
        self.assertEqual(quote_request.assigned_manager_id, printy_manager.id)
        self.assertEqual(quote_request.manager_selection_mode, QuoteRequest.MANAGER_SELECTION_PRINTY_AUTO)

    def test_client_selected_requires_and_persists_manager(self):
        self.client.force_authenticate(user=self.client_user)

        missing = self.client.post(
            "/api/intake/submit/",
            {"draft_id": self._draft().id, "manager_selection_mode": "client_selected"},
            format="json",
        )
        self.assertEqual(missing.status_code, 400)

        response = self.client.post(
            "/api/intake/submit/",
            {
                "draft_id": self._draft().id,
                "manager_selection_mode": "client_selected",
                "selected_manager_id": self.manager.id,
                "artwork_reference": "uploaded",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        quote_request = QuoteRequest.objects.get(pk=response.json()["intake_id"])
        self.assertEqual(quote_request.assigned_manager_id, self.manager.id)
        self.assertEqual(quote_request.manager_selection_mode, QuoteRequest.MANAGER_SELECTION_CLIENT_SELECTED)


@override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test", PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AdminPaymentConfirmationRegressionTestCase(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.admin_user = User.objects.create_superuser(email="admin-routing@example.com", password="pass")
        self.client_user = User.objects.create_user(email="pay-client@example.com", password="pass", role=User.Role.CLIENT)
        self.manager = User.objects.create_user(
            email="pay-manager@example.com",
            password="pass",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.shop_owner = User.objects.create_user(email="pay-shop@example.com", password="pass", role=User.Role.PRODUCTION)
        self.shop = Shop.objects.create(name="Pay Shop", slug="pay-shop", owner=self.shop_owner, is_active=True)
        self.policy = PlatformFeePolicy.objects.create(
            name="Payment regression policy",
            is_active=True,
            printer_fee_rate=Decimal("0.10"),
            broker_margin_fee_rate=Decimal("0.50"),
            add_platform_fee_on_top=False,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="Pay Client",
            customer_email=self.client_user.email,
        )
        self.production_option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=self.manager,
            status=ProductionOption.SELECTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_option=self.production_option,
            created_by=self.manager,
            status=QuoteOfferStatus.SENT,
            total=Decimal("2500.00"),
            sent_at=timezone.now(),
        )
        create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("2500.00"),
            production_option=self.production_option,
            policy=self.policy,
        )

    def _payment_and_stk(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk_request = initiate_stk_push(payment=payment, phone_number="+254700000000")
        return payment, stk_request

    def test_admin_payment_simulation_calls_confirmation_service_and_creates_job_once(self):
        payment, _stk_request = self._payment_and_stk()
        self.client.force_login(self.admin_user)

        with patch("payments.services.confirm_successful_stk_request", wraps=__import__("payments.services", fromlist=["confirm_successful_stk_request"]).confirm_successful_stk_request) as confirm:
            response = self.client.post(
                reverse("admin:payments_payment_changelist"),
                {"action": "simulate_sandbox_payment_confirmation", "_selected_action": [payment.id]},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(confirm.called)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        managed_job = ManagedJob.objects.get(source_quote=self.quote)
        self.assertEqual(payment.managed_job_id, managed_job.id)
        self.assertEqual(managed_job.broker_id, self.manager.id)
        self.assertEqual(managed_job.assigned_shop_id, self.shop.id)

        self.client.post(
            reverse("admin:payments_payment_changelist"),
            {"action": "simulate_sandbox_payment_confirmation", "_selected_action": [payment.id]},
            follow=True,
        )
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)

    def test_admin_stk_success_action_uses_confirmation_pipeline(self):
        payment, stk_request = self._payment_and_stk()
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("admin:payments_mpesastkrequest_changelist"),
            {"action": "simulate_sandbox_stk_success", "_selected_action": [stk_request.id]},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        stk_request.refresh_from_db()
        self.assertEqual(stk_request.status, MpesaSTKRequest.STATUS_SUCCESS)
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertTrue(ManagedJob.objects.filter(source_quote=self.quote, broker=self.manager).exists())

    def test_manager_counts_include_paid_job_awaiting_dispatch(self):
        payment, stk_request = self._payment_and_stk()
        self.client.force_login(self.admin_user)
        self.client.post(
            reverse("admin:payments_mpesastkrequest_changelist"),
            {"action": "simulate_sandbox_stk_success", "_selected_action": [stk_request.id]},
            follow=True,
        )

        self.api_client.force_authenticate(user=self.manager)
        response = self.api_client.get("/api/dashboard/counts/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["counts"]["paid_jobs_awaiting_dispatch"], 1)
        self.assertTrue(
            ManagedJob.objects.filter(
                broker=self.manager,
                payment_status=ManagedJobPaymentStatus.CONFIRMED,
                status=ManagedJobStatus.PAYMENT_CONFIRMED,
            ).exists()
        )
        managed_job = ManagedJob.objects.get(source_quote=self.quote)
        self.assertTrue(
            Notification.objects.filter(
                user=self.manager,
                notification_type=Notification.JOB_STATUS_UPDATED,
                object_type="managed_job",
                object_id=managed_job.id,
                read_at__isnull=True,
            ).exists()
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.client_user,
                notification_type=Notification.JOB_STATUS_UPDATED,
                object_type="managed_job",
                object_id=managed_job.id,
                read_at__isnull=True,
            ).exists()
        )

    def test_shop_counts_include_new_assignment(self):
        managed_job = ManagedJob.objects.create(
            title="Assigned count job",
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            status=ManagedJobStatus.ASSIGNED,
        )
        JobAssignment.objects.create(managed_job=managed_job, assigned_shop=self.shop)

        self.api_client.force_authenticate(user=self.shop_owner)
        response = self.api_client.get("/api/dashboard/counts/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["counts"]["new_assignments"], 1)

    def test_partner_send_to_client_creates_client_notification(self):
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.manager,
            assigned_manager=self.manager,
            on_behalf_of=self.client_user,
            customer_name="Pay Client",
            customer_email=self.client_user.email,
            status=QuoteStatus.DRAFT,
            request_snapshot={"source": "partner_quote_builder"},
        )
        Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.manager,
            status=QuoteOfferStatus.PENDING,
            total=Decimal("1000.00"),
            response_snapshot={"pricing": {"grand_total": "1000.00"}},
        )
        self.api_client.force_authenticate(user=self.manager)

        response = self.api_client.post(
            f"/api/dashboard/partner/quotes/{quote_request.id}/send-to-client/",
            {"broker_margin_type": "fixed", "broker_margin_value": "300.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Notification.objects.filter(
                user=self.client_user,
                notification_type=Notification.SHOP_QUOTE_SENT,
                object_type="quote_request",
                object_id=quote_request.id,
                read_at__isnull=True,
            ).exists()
        )
