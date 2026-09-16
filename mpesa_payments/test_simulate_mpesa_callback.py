"""Phase 3 — the sandbox-only simulate_mpesa_callback management command.

The buyer dashboard used to fake M-Pesa outcomes in the browser without ever
touching a payment record. The real callback handler is
``mpesa_payments.services.process_callback``; this command feeds a synthetic
Safaricom body through the same handler, so each outcome drives the exact same
downstream logic a real callback does.

These tests prove: (1) all four outcomes route through process_callback to the
right terminal state, (2) the command refuses to run outside the sandbox
environment, and (3) it never moves an already-terminal payment.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from mpesa_payments.models import (
    MpesaCallbackLog,
    MpesaPayment,
    MpesaPaymentStatus,
    MpesaReconciliationStatus,
)

DARAJA_SETTINGS = dict(
    MPESA_ENV="sandbox",
    MPESA_CONSUMER_KEY="test-key",
    MPESA_CONSUMER_SECRET="test-secret",
    MPESA_SHORTCODE="174379",
    MPESA_PASSKEY="test-passkey",
    MPESA_CALLBACK_URL="https://example.com/api/payments/mpesa/callback/",
)


@override_settings(**DARAJA_SETTINGS)
class SimulateMpesaCallbackCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(email="sim-buyer@test.com", password="pw")

    def _pending(self, amount="2500.00", *, payable=None):
        kwargs = {
            "phone_number": "254712345678",
            "amount": Decimal(amount),
            "status": MpesaPaymentStatus.PENDING,
        }
        if payable is not None:
            kwargs["content_type"] = ContentType.objects.get_for_model(payable)
            kwargs["object_id"] = payable.id
        return MpesaPayment.objects.create(**kwargs)

    def test_success_outcome_marks_paid_and_confirms_reconciliation(self):
        payment = self._pending()

        call_command("simulate_mpesa_callback", outcome="success", payment=payment.id)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(payment.reconciliation_status, MpesaReconciliationStatus.CONFIRMED)
        self.assertTrue(payment.mpesa_receipt_number.startswith("SANDBOX-"))
        self.assertTrue(payment.paid_amount == payment.amount)
        entry = MpesaCallbackLog.objects.get(checkout_request_id=payment.checkout_request_id)
        self.assertTrue(entry.processed)
        self.assertEqual(entry.payload["SandboxSimulation"]["outcome"], "success")

    def test_insufficient_outcome_marks_failed(self):
        payment = self._pending()

        call_command("simulate_mpesa_callback", outcome="insufficient", payment=payment.id)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.FAILED)
        self.assertEqual(payment.result_code, "2001")
        self.assertIn("insufficient", payment.result_desc.lower())

    def test_cancelled_outcome_marks_cancelled(self):
        payment = self._pending()

        call_command("simulate_mpesa_callback", outcome="cancelled", payment=payment.id)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.CANCELLED)
        self.assertEqual(payment.result_code, "1032")

    def test_amount_mismatch_outcome_marks_needs_review(self):
        payment = self._pending("5000.00")

        call_command("simulate_mpesa_callback", outcome="amount-mismatch", payment=payment.id)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.NEEDS_REVIEW)
        self.assertEqual(payment.reconciliation_status, MpesaReconciliationStatus.AMOUNT_MISMATCH)
        self.assertNotEqual(payment.paid_amount, payment.amount)
        self.assertTrue(payment.mpesa_receipt_number)

    def test_order_target_resolves_payable_payment(self):
        payment = self._pending(payable=self.user)

        call_command("simulate_mpesa_callback", outcome="success", order=self.user.id)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)

    def test_refuses_outside_sandbox(self):
        payment = self._pending()

        with override_settings(MPESA_ENV="production"):
            with self.assertRaises(CommandError):
                call_command("simulate_mpesa_callback", outcome="success", payment=payment.id)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PENDING)

    def test_refuses_terminal_payment(self):
        payment = self._pending()
        payment.status = MpesaPaymentStatus.PAID
        payment.save(update_fields=["status"])

        with self.assertRaises(CommandError):
            call_command("simulate_mpesa_callback", outcome="success", payment=payment.id)

    def test_replay_does_not_repay_an_already_paid_payment(self):
        payment = self._pending("1.00")
        call_command("simulate_mpesa_callback", outcome="success", payment=payment.id)
        confirmed_at = MpesaPayment.objects.get(pk=payment.pk).confirmed_at

        with self.assertRaises(CommandError):
            call_command("simulate_mpesa_callback", outcome="success", payment=payment.id)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(payment.confirmed_at, confirmed_at)
        self.assertEqual(MpesaCallbackLog.objects.filter(processed=True).count(), 1)

    def test_unknown_outcome_rejected(self):
        payment = self._pending()
        with self.assertRaises(CommandError):
            call_command("simulate_mpesa_callback", outcome="exploded", payment=payment.id)

    def test_target_argument_required(self):
        with self.assertRaises(CommandError):
            call_command("simulate_mpesa_callback", outcome="success")
        with self.assertRaises(CommandError):
            call_command("simulate_mpesa_callback", outcome="success", payment=1, order=1)

    def test_missing_payment_id_rejected(self):
        with self.assertRaises(CommandError):
            call_command("simulate_mpesa_callback", outcome="success", payment=999999)