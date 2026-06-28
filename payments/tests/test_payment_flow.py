from decimal import Decimal
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob
from jobs.services.dispatch import dispatch_job_to_shop, ensure_job_assignment_for_paid_job
from notifications.models import Notification
from payments.admin import PaymentAdmin
from payments.models import MpesaSTKRequest, Payment
from payments.payment_actor_serializers import PaymentClientSerializer
from payments.services import create_payment_for_quote, handle_stk_callback, initiate_stk_push, mark_payment_paid
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.choices import QuoteOfferStatus
from quotes.models import ProductionOption, Quote, QuoteRequest
from quotes.acceptance import accept_quote_for_payment
from shops.models import Shop


User = get_user_model()


class CanonicalPaymentFlowTestCase(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(email="admin@example.com", password="pass")
        self.client_user = User.objects.create_user(email="client@example.com", password="pass", role=User.Role.CLIENT)
        self.broker = User.objects.create_user(email="broker@example.com", password="pass", role=User.Role.PARTNER)
        self.shop_owner = User.objects.create_user(email="shop@example.com", password="pass", role=User.Role.PRODUCTION)
        self.shop = Shop.objects.create(name="Payment Flow Shop", owner=self.shop_owner, is_active=True)
        self.policy = PlatformFeePolicy.objects.create(
            name="Payment flow policy",
            is_active=True,
            printer_fee_rate=Decimal("0.10"),
            broker_margin_fee_rate=Decimal("0.50"),
            add_platform_fee_on_top=False,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            customer_name="Client",
            customer_email="client@example.com",
            customer_phone="+254700000000",
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
            total=Decimal("4000.00"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.split = create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("4000.00"),
            production_option=self.production_option,
            policy=self.policy,
        )

    def _success_callback(self, stk, amount="4000.00", receipt="QGH7XXX"):
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

    def _run_payment_admin_action(self, payment):
        self.client.force_login(self.admin_user)
        return self.client.post(
            reverse("admin:payments_payment_changelist"),
            {
                "action": "simulate_sandbox_payment_confirmation",
                "_selected_action": [payment.id],
            },
            follow=True,
        )

    def _message_texts(self, response):
        return [str(message) for message in list(response.context["messages"])]

    def _admin_action_request(self):
        request = RequestFactory().post(reverse("admin:payments_payment_changelist"))
        request.user = self.admin_user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_accepting_quote_creates_pending_payment_for_split_client_total(self):
        quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        self.assertEqual(quote.status, QuoteOfferStatus.ACCEPTED)
        self.assertEqual(payment.status, Payment.STATUS_PENDING)
        self.assertEqual(payment.amount, self.split.client_total)
        self.assertEqual(payment.expected_amount, self.split.client_total)
        self.assertEqual(payment.quote, quote)
        self.assertIsNone(payment.managed_job)

    def test_accepting_quote_creates_split_from_selected_production_option(self):
        other_quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_option=self.production_option,
            created_by=self.broker,
            status=QuoteOfferStatus.SENT,
            total=Decimal("4000.00"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )

        _quote, payment = accept_quote_for_payment(quote=other_quote, accepted_by=self.client_user)

        other_quote.refresh_from_db()
        self.assertEqual(other_quote.financial_split.production_cost, self.production_option.production_cost)
        self.assertEqual(payment.amount, other_quote.financial_split.client_total)

    def test_pending_payment_is_reused_and_paid_payment_blocks_duplicate(self):
        _quote, first = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        second = create_payment_for_quote(quote=self.quote, payer=self.client_user)
        self.assertEqual(first.id, second.id)

        first.status = Payment.STATUS_PAID
        first.save(update_fields=["status", "updated_at"])
        with self.assertRaises(ValidationError):
            create_payment_for_quote(quote=self.quote, payer=self.client_user)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_successful_callback_creates_managed_job_once(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")

        first = handle_stk_callback(callback_payload=self._success_callback(stk))
        second = handle_stk_callback(callback_payload=self._success_callback(stk))

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)
        managed_job = ManagedJob.objects.get(source_quote=self.quote)
        self.assertEqual(managed_job.client_total, self.split.client_total)
        self.assertEqual(managed_job.payment_status, ManagedJobPaymentStatus.CONFIRMED)
        self.assertEqual(managed_job.status, ManagedJobStatus.PAYMENT_CONFIRMED)
        self.assertEqual(managed_job.assigned_shop, self.shop)
        self.assertEqual(managed_job.operational_snapshot["production_option_id"], self.production_option.id)
        self.assertEqual(managed_job.workflow_metadata["payment_id"], payment.id)
        payment.refresh_from_db()
        self.assertEqual(payment.managed_job, managed_job)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_mark_payment_paid_repairs_already_paid_payment_missing_managed_job(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        payment.status = Payment.STATUS_PAID
        payment.received_amount = payment.expected_amount
        payment.confirmed_at = timezone.now()
        payment.save(update_fields=["status", "received_amount", "confirmed_at", "updated_at"])

        mark_payment_paid(payment)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertIsNotNone(payment.managed_job_id)
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_mark_payment_paid_already_paid_with_managed_job_is_idempotent(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        payment.status = Payment.STATUS_PAID
        payment.received_amount = payment.expected_amount
        payment.confirmed_at = timezone.now()
        payment.save(update_fields=["status", "received_amount", "confirmed_at", "updated_at"])
        mark_payment_paid(payment)
        payment.refresh_from_db()
        managed_job_id = payment.managed_job_id

        mark_payment_paid(payment)

        payment.refresh_from_db()
        self.assertEqual(payment.managed_job_id, managed_job_id)
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_ensure_job_assignment_for_paid_job_repairs_missing_assignment_once(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        mark_payment_paid(payment)
        managed_job = ManagedJob.objects.get(source_quote=self.quote)
        self.assertEqual(JobAssignment.objects.filter(managed_job=managed_job, reassigned_from__isnull=True).count(), 0)

        first = ensure_job_assignment_for_paid_job(managed_job=managed_job, dispatched_by=self.broker)
        second = ensure_job_assignment_for_paid_job(managed_job=managed_job, dispatched_by=self.broker)

        self.assertIsNotNone(first)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.assigned_shop_id, self.shop.id)
        self.assertEqual(JobAssignment.objects.filter(managed_job=managed_job, reassigned_from__isnull=True).count(), 1)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_failed_callback_does_not_create_managed_job(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")
        result = handle_stk_callback(
            callback_payload={
                "Body": {
                    "stkCallback": {
                        "CheckoutRequestID": stk.checkout_request_id,
                        "MerchantRequestID": stk.merchant_request_id,
                        "ResultCode": 1032,
                        "ResultDesc": "Request cancelled by user",
                    }
                }
            }
        )

        self.assertEqual(result["status"], "failed")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_CANCELLED)
        self.assertFalse(ManagedJob.objects.filter(source_quote=self.quote).exists())

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_sandbox_mpesa_env_initiates_test_stk_request(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")

        self.assertEqual(stk.status, "sent")
        self.assertEqual(stk.response_code, "0")
        self.assertTrue(stk.checkout_request_id.startswith("TEST-CR-"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)
        self.assertEqual(payment.checkout_request_id, stk.checkout_request_id)

    @override_settings(MPESA_ENVIRONMENT="sandbox")
    def test_initiate_stk_push_calls_daraja_client_in_sandbox_mode(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        with patch("payments.services.MpesaDarajaClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            mock_instance.initiate_stk_push.return_value = {
                "CheckoutRequestID": "ws_CO_REAL_456",
                "MerchantRequestID": "REAL-MR-456",
                "ResponseCode": "0",
                "ResponseDescription": "Success. Request accepted for processing",
                "CustomerMessage": "Success. Request accepted for processing",
            }

            stk_request = initiate_stk_push(payment=payment, phone_number="+254700000000")

        MockClient.assert_called_once_with()
        mock_instance.initiate_stk_push.assert_called_once_with(
            phone_number="+254700000000",
            amount=4000,
            account_reference=payment.account_reference,
            transaction_desc="Printy payment",
        )
        self.assertEqual(stk_request.checkout_request_id, "ws_CO_REAL_456")
        self.assertEqual(stk_request.status, MpesaSTKRequest.STATUS_SENT)

    @override_settings(MPESA_ENVIRONMENT="sandbox")
    def test_initiate_stk_push_handles_daraja_failure(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        failed_requests = []
        original_save = MpesaSTKRequest.save

        def capture_failed_save(stk_request, *args, **kwargs):
            if stk_request.status == MpesaSTKRequest.STATUS_FAILED:
                failed_requests.append(stk_request)
            return original_save(stk_request, *args, **kwargs)

        with patch("payments.services.MpesaDarajaClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            mock_instance.initiate_stk_push.side_effect = ValueError("Invalid phone number")

            with patch.object(MpesaSTKRequest, "save", autospec=True, side_effect=capture_failed_save):
                with self.assertRaises(ValueError):
                    initiate_stk_push(payment=payment, phone_number="+254700000000")

        MockClient.assert_called_once_with()
        self.assertEqual(len(failed_requests), 1)
        self.assertEqual(failed_requests[0].status, MpesaSTKRequest.STATUS_FAILED)
        self.assertEqual(failed_requests[0].response_description, "Invalid phone number")

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_manager_dispatch_creates_assignment_and_client_cannot_dispatch(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")
        handle_stk_callback(callback_payload=self._success_callback(stk))
        managed_job = ManagedJob.objects.get(source_quote=self.quote)

        assignment = dispatch_job_to_shop(managed_job=managed_job, dispatched_by=self.broker)
        self.assertEqual(assignment.assigned_shop, self.shop)
        self.assertEqual(assignment.shop_payout, self.split.shop_payout)
        self.assertTrue(
            Notification.objects.filter(
                user=self.shop_owner,
                notification_type=Notification.JOB_STATUS_UPDATED,
                object_type="managed_job",
                object_id=managed_job.id,
                read_at__isnull=True,
            ).exists()
        )

        self.client.force_login(self.client_user)
        response = self.client.post(
            reverse("dashboard-partner-job-dispatch", kwargs={"pk": managed_job.id}),
            {},
            content_type="application/json",
        )
        self.assertIn(response.status_code, {401, 403})

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_admin_action_simulates_sandbox_payment_confirmation(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")

        response = self._run_payment_admin_action(payment)

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        stk.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(stk.status, "success")
        self.assertIsNotNone(payment.confirmed_at)
        self.assertTrue(ManagedJob.objects.filter(source_quote=self.quote).exists())
        self.assertEqual(stk.raw_callback["SandboxSimulation"]["admin_user_id"], self.admin_user.id)

    @override_settings(MPESA_ENV="production", MPESA_ENVIRONMENT="production")
    def test_admin_action_refuses_in_production_mode(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        payment.status = Payment.STATUS_PROCESSING
        payment.checkout_request_id = "TEST-CR-PROD-GUARD"
        payment.merchant_request_id = "TEST-MR-PROD-GUARD"
        payment.save(update_fields=["status", "checkout_request_id", "merchant_request_id", "updated_at"])
        MpesaSTKRequest.objects.create(
            payment=payment,
            phone_number="+254700000000",
            amount=payment.amount,
            account_reference=payment.account_reference,
            checkout_request_id=payment.checkout_request_id,
            merchant_request_id=payment.merchant_request_id,
            status=MpesaSTKRequest.STATUS_SENT,
        )

        response = self._run_payment_admin_action(payment)

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)
        self.assertFalse(ManagedJob.objects.filter(source_quote=self.quote).exists())

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_admin_action_refuses_already_paid_payment(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")
        handle_stk_callback(callback_payload=self._success_callback(stk))
        self.assertTrue(ManagedJob.objects.filter(source_quote=self.quote).exists())

        response = self._run_payment_admin_action(payment)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_admin_action_repairs_already_paid_payment_missing_managed_job(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        payment.status = Payment.STATUS_PAID
        payment.received_amount = payment.expected_amount
        payment.confirmed_at = timezone.now()
        payment.save(update_fields=["status", "received_amount", "confirmed_at", "updated_at"])

        response = self._run_payment_admin_action(payment)

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertIsNotNone(payment.managed_job_id)
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)
        self.assertTrue(
            any("Repaired 1 already-paid payment(s) by creating missing ManagedJob" in text for text in self._message_texts(response))
        )

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_admin_action_empty_queryset_shows_nothing_processed_message(self):
        request = self._admin_action_request()
        payment_admin = PaymentAdmin(Payment, AdminSite())

        payment_admin.simulate_sandbox_payment_confirmation(request, Payment.objects.none())

        message_texts = [str(message) for message in get_messages(request)]
        self.assertIn(
            "No payments were processed. Selection may be empty or all payments are already complete.",
            message_texts,
        )

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_admin_action_handles_unexpected_exception_per_payment(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        initiate_stk_push(payment=payment, phone_number="+254700000000")

        with patch("payments.admin.handle_stk_callback", side_effect=RuntimeError("callback exploded")):
            response = self._run_payment_admin_action(payment)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ManagedJob.objects.filter(source_quote=self.quote).exists())
        self.assertTrue(
            any(f"Error processing payment {payment.id}: callback exploded" in text for text in self._message_texts(response))
        )

    def test_payment_client_serializer_hides_split_fields(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        payload = PaymentClientSerializer(payment).data
        forbidden = {"shop_payout", "broker_payout", "printy_fee", "production_cost", "raw_callback"}

        self.assertTrue(forbidden.isdisjoint(payload.keys()))
        self.assertEqual(payload["amount"], str(self.split.client_total))

    def test_payment_routes_do_not_import_deleted_payment_apps(self):
        import api.payment_views as payment_views

        self.assertFalse(hasattr(payment_views, "BillingPaymentTransaction"))
