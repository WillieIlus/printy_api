"""Law 10.7 — a sent STK push is "Awaiting confirmation", never "Paid".

These tests pin the canonical state vocabulary and prove that the only path to
``PAID`` is a verified provider confirmation. They are written against the
public behaviour (payment rows, the client link payload, the payment detail
API) so they keep holding if the internals are refactored.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import UserProfile  # noqa: F401  (kept for parity with flow tests)
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import ManagedJob
from payments.models import MpesaSTKRequest, Payment
from payments.services import handle_stk_callback, initiate_stk_push
from payments.states import (
    PaymentState,
    canonical_job_payment_state,
    is_paid,
    is_verified_paid_state,
    job_payment_state,
    mpesa_state_for_status,
    payment_state_for_status,
    payment_state_label,
)
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.choices import QuoteOfferStatus
from quotes.models import ProductionOption, Quote, QuoteRequest
from quotes.acceptance import accept_quote_for_payment
from django.contrib.auth import get_user_model

User = get_user_model()


class CanonicalStateMappingTests(TestCase):
    """The single translation point: no vocabulary may map to PAID by accident."""

    def test_payment_status_never_maps_to_paid_while_unverified(self):
        self.assertEqual(payment_state_for_status("pending"), PaymentState.NOT_STARTED)
        self.assertEqual(payment_state_for_status("processing"), PaymentState.AWAITING_CONFIRMATION)
        self.assertEqual(payment_state_for_status("paid"), PaymentState.PAID)
        self.assertEqual(payment_state_for_status("failed"), PaymentState.FAILED)
        self.assertEqual(payment_state_for_status("cancelled"), PaymentState.CANCELLED)
        self.assertEqual(payment_state_for_status("expired"), PaymentState.TIMED_OUT)

    def test_mpesa_status_maps_pending_and_review_to_awaiting_not_paid(self):
        self.assertEqual(mpesa_state_for_status("initiated"), PaymentState.NOT_STARTED)
        self.assertEqual(mpesa_state_for_status("pending"), PaymentState.AWAITING_CONFIRMATION)
        self.assertEqual(mpesa_state_for_status("needs_review"), PaymentState.AWAITING_CONFIRMATION)
        self.assertEqual(mpesa_state_for_status("paid"), PaymentState.PAID)

    def test_job_payment_status_maps_every_paid_like_state_to_paid(self):
        self.assertEqual(job_payment_state("pending"), PaymentState.NOT_STARTED)
        self.assertEqual(
            job_payment_state(ManagedJobPaymentStatus.CONFIRMATION_PENDING),
            PaymentState.AWAITING_CONFIRMATION,
        )
        for confirmed in ("confirmed", "release_ready", "payout_on_hold", "released"):
            self.assertEqual(job_payment_state(confirmed), PaymentState.PAID)
        self.assertEqual(job_payment_state("refunded"), PaymentState.CANCELLED)

    def test_unknown_status_is_not_paid(self):
        for unknown in ("", None, "garbage", "SUCCESSFUL"):
            state = payment_state_for_status(unknown)
            self.assertEqual(state, PaymentState.NOT_STARTED)
            self.assertFalse(is_verified_paid_state(state))

    def test_only_paid_state_is_verified(self):
        self.assertTrue(is_verified_paid_state(PaymentState.PAID))
        for state in (
            PaymentState.NOT_STARTED,
            PaymentState.AWAITING_CONFIRMATION,
            PaymentState.FAILED,
            PaymentState.CANCELLED,
            PaymentState.TIMED_OUT,
        ):
            self.assertFalse(is_verified_paid_state(state))

    def test_labels_are_human_readable(self):
        self.assertEqual(payment_state_label("awaiting_confirmation"), "Awaiting confirmation")
        self.assertEqual(payment_state_label("paid"), "Paid")
        self.assertEqual(payment_state_label("timed_out"), "Timed out")


class _StkFixture(TestCase):
    """Shared quote/payment fixture. No tests here; subclasses add them."""

    def setUp(self):
        self.client_user = User.objects.create_user(
            email="client@example.com", password="pass", role=User.Role.CLIENT
        )
        self.broker = User.objects.create_user(
            email="broker@example.com", password="pass", role=User.Role.PARTNER
        )
        self.shop_owner = User.objects.create_user(
            email="shop@example.com", password="pass", role=User.Role.PRODUCTION
        )
        from shops.models import Shop

        self.shop = Shop.objects.create(name="State Shop", owner=self.shop_owner, is_active=True)
        self.policy = PlatformFeePolicy.objects.create(
            name="state-policy",
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
        option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=self.broker,
            status=ProductionOption.SELECTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_option=option,
            created_by=self.broker,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1750.00"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.split = create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1750.00"),
            production_option=option,
            policy=self.policy,
        )

    def _accept(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        return payment

    def _success_callback(self, stk, receipt="QGSTATE1"):
        return {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": stk.checkout_request_id,
                    "MerchantRequestID": stk.merchant_request_id,
                    "ResultCode": 0,
                    "ResultDesc": "Success",
                    "CallbackMetadata": {
                        "Items": [
                            {"Name": "Amount", "Value": float(stk.amount)},
                            {"Name": "MpesaReceiptNumber", "Value": receipt},
                        ]
                    },
                }
            }
        }


class StkPushIsNotPaidTests(_StkFixture):
    """Item 1/2/5: pushing money never marks money as received."""

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_stk_push_does_not_mark_the_payment_paid(self):
        payment = self._accept()
        self.assertFalse(is_paid(payment))

        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")

        payment.refresh_from_db()
        self.assertNotEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)
        self.assertEqual(payment.state, PaymentState.AWAITING_CONFIRMATION)
        self.assertFalse(is_paid(payment))
        self.assertIsNone(payment.confirmed_at)
        self.assertIsNone(payment.mpesa_receipt_number)
        self.assertEqual(stk.status, MpesaSTKRequest.STATUS_SENT)
        self.assertFalse(ManagedJob.objects.filter(source_quote=self.quote).exists())

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_push_with_no_callback_stays_awaiting_confirmation(self):
        payment = self._accept()
        initiate_stk_push(payment=payment, phone_number="+254700000000")

        # No callback arrives. Re-reading state must never promote to paid.
        payment.refresh_from_db()
        self.assertEqual(payment.state, PaymentState.AWAITING_CONFIRMATION)
        self.assertFalse(is_paid(payment))

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_only_a_verified_callback_reaches_paid(self):
        payment = self._accept()
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")

        handle_stk_callback(callback_payload=self._success_callback(stk))

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(payment.state, PaymentState.PAID)
        self.assertTrue(is_paid(payment))
        self.assertIsNotNone(payment.confirmed_at)
        managed_job = ManagedJob.objects.get(source_quote=self.quote)
        self.assertEqual(managed_job.payment_status, ManagedJobPaymentStatus.CONFIRMED)
        self.assertEqual(canonical_job_payment_state(managed_job), PaymentState.PAID)

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_stub_push_is_labelled_simulated(self):
        payment = self._accept()
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")

        self.assertTrue(stk.checkout_request_id.startswith("TEST-CR-"))
        self.assertTrue(stk.raw_response.get("Simulated"))
        self.assertIn("SIMULATED", stk.response_description.upper())
        payment.refresh_from_db()
        self.assertEqual(payment.state, PaymentState.AWAITING_CONFIRMATION)

    def test_stub_mode_is_impossible_in_production(self):
        from payments.services import _is_stub_mode

        with override_settings(MPESA_ENV="production", MPESA_ENVIRONMENT="production", MPESA_FORCE_STUB=False):
            self.assertFalse(_is_stub_mode())

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_payment_detail_api_exposes_canonical_state(self):
        payment = self._accept()
        initiate_stk_push(payment=payment, phone_number="+254700000000")

        api_client = APIClient()
        api_client.force_authenticate(self.client_user)
        response = api_client.get(f"/api/payments/{payment.id}/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["state"], PaymentState.AWAITING_CONFIRMATION)
        self.assertEqual(payload["state_label"], "Awaiting confirmation")
        self.assertFalse(payload["is_paid"])


class ClientLinkCanonicalStateTests(_StkFixture):
    """Item 4: the buyer-facing link speaks the canonical vocabulary."""

    @override_settings(MPESA_ENV="sandbox", MPESA_ENVIRONMENT="test")
    def test_link_shows_awaiting_confirmation_after_push_not_paid(self):
        from clients.serializers import build_link_payload

        payment = self._accept()
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")
        managed_job = ManagedJob.objects.create(
            title="State job",
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.client_user,
            broker=self.broker,
            assigned_shop=self.shop,
            created_by=self.client_user,
            status=ManagedJobStatus.AWAITING_PAYMENT,
            payment_status=ManagedJobPaymentStatus.PENDING,
            client_total=payment.amount,
        )
        payment.managed_job = managed_job
        payment.save(update_fields=["managed_job", "updated_at"])

        payload = build_link_payload(self.quote_request)
        self.assertEqual(payload["payment_state"], PaymentState.AWAITING_CONFIRMATION)
        self.assertEqual(payload["payment_state_label"], "Awaiting confirmation")

        handle_stk_callback(callback_payload=self._success_callback(stk))
        payload = build_link_payload(self.quote_request)
        self.assertEqual(payload["payment_state"], PaymentState.PAID)
        self.assertEqual(payload["payment_state_label"], "Paid")
