"""Phase 7.5a — the buyer checkout's poll loop over the real endpoints.

MpesaCheckout.vue now posts to the real stk-push endpoint and polls the detail
endpoint until the async Safaricom callback lands. These tests pin the exact
API sequence that flow depends on, and prove the Phase 3 simulate command
flips the same rows the UI polls:

1. POST /api/payments/mpesa/stk-push/ creates and initiates a payment.
2. GET  /api/payments/mpesa/{id}/ returns the live state the UI maps.
3. Running `simulate_mpesa_callback --outcome=success` against that payment
   turns the same detail response paid (the live sandbox manual-test path).
4. The detail endpoint is owner-scoped (404 for anyone else's payment).
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from unittest.mock import patch

from mpesa_payments.models import MpesaPayment, MpesaPaymentStatus

User = get_user_model()

DARAJA_SETTINGS = dict(
    MPESA_ENV="sandbox",
    MPESA_CONSUMER_KEY="test-key",
    MPESA_CONSUMER_SECRET="test-secret",
    MPESA_SHORTCODE="174379",
    MPESA_PASSKEY="test-passkey",
    MPESA_CALLBACK_URL="https://example.com/api/payments/mpesa/callback/",
)


@override_settings(**DARAJA_SETTINGS)
class CheckoutPollLoopTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(email="buyer@test.com", password="pw")
        self.other = User.objects.create_user(email="other@test.com", password="pw")
        self.client.force_authenticate(user=self.buyer)

    @patch("mpesa_payments.views.initiate_stk_push")
    def test_stk_push_creates_a_payment_and_initiates_it(self, mock_initiate):
        response = self.client.post(
            "/api/payments/mpesa/stk-push/",
            {"phone_number": "0712 345 678", "amount": "1250.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["phone_number"], "254712345678")
        self.assertEqual(Decimal(str(data["amount"])), Decimal("1250.00"))
        self.assertEqual(data["status"], "initiated")
        mock_initiate.assert_called_once()

    def test_simulate_command_flips_the_payment_the_ui_polls_to_paid(self):
        payment = MpesaPayment.objects.create(
            user=self.buyer,
            phone_number="254712345678",
            amount=Decimal("1250.00"),
            status=MpesaPaymentStatus.INITIATED,
        )
        payment.mark_push_sent(
            merchant_request_id="MR-123",
            checkout_request_id="CR-456",
            customer_message="Request accepted.",
        )

        pending = self.client.get(f"/api/payments/mpesa/{payment.id}/")
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json()["status"], "pending")
        self.assertFalse(pending.json()["is_paid"])

        call_command("simulate_mpesa_callback", outcome="success", payment=payment.id)

        paid = self.client.get(f"/api/payments/mpesa/{payment.id}/")
        self.assertEqual(paid.status_code, 200)
        self.assertEqual(paid.json()["status"], "paid")
        self.assertTrue(paid.json()["is_paid"])
        self.assertNotEqual(paid.json()["mpesa_receipt_number"], "")

    def test_payment_detail_is_owner_scoped(self):
        payment = MpesaPayment.objects.create(
            user=self.buyer,
            phone_number="254712345678",
            amount=Decimal("1250.00"),
            status=MpesaPaymentStatus.PENDING,
        )
        self.client.force_authenticate(user=self.other)

        response = self.client.get(f"/api/payments/mpesa/{payment.id}/")

        self.assertEqual(response.status_code, 404)