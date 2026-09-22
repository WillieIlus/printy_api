"""The M-Pesa module is cashier-only; settlement lives in the receivers.

`on_mpesa_payment_confirmed` is the codebase reaction to a confirmed payment:
it advances the payable ManagedJob to payment_confirmed, notifies the client
and broker, and records the audit event. These tests prove that wiring holds
end-to-end through the real callback handler and that it is idempotent.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings

from jobs import audit_services
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobStatusEvent, ManagedJob
from mpesa_payments.models import MpesaPayment, MpesaPaymentStatus, MpesaReconciliationStatus
from mpesa_payments.services import process_callback
from notifications.models import Notification

DARAJA_SETTINGS = dict(
    MPESA_ENV="sandbox",
    MPESA_CONSUMER_KEY="test-key",
    MPESA_CONSUMER_SECRET="test-secret",
    MPESA_SHORTCODE="174379",
    MPESA_PASSKEY="test-passkey",
    MPESA_CALLBACK_URL="https://example.com/api/payments/mpesa/callback/",
)


def success_callback(checkout_request_id: str, amount: str = "2500.00", receipt: str = "SJK4Q1X8YR") -> dict:
    return {
        "Body": {
            "stkCallback": {
                "MerchantRequestID": "29115-34620561-1",
                "CheckoutRequestID": checkout_request_id,
                "ResultCode": 0,
                "ResultDesc": "The service request is processed successfully.",
                "CallbackMetadata": {
                    "Item": [
                        {"Name": "Amount", "Value": amount},
                        {"Name": "MpesaReceiptNumber", "Value": receipt},
                        {"Name": "Balance"},
                        {"Name": "TransactionDate", "Value": 20251114114700},
                        {"Name": "PhoneNumber", "Value": 254712345678},
                    ]
                },
            }
        }
    }


@override_settings(**DARAJA_SETTINGS)
class MpesaPaymentConfirmedReceiverTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.client_user = User.objects.create_user(email="client@test.com", password="pw")
        self.broker = User.objects.create_user(email="broker@test.com", password="pw")
        self.job = ManagedJob.objects.create(
            title="Business cards 90x55",
            client=self.client_user,
            broker=self.broker,
            status=ManagedJobStatus.AWAITING_PAYMENT,
            payment_status=ManagedJobPaymentStatus.PENDING,
            client_total=Decimal("2500.00"),
        )

    def _payment(self):
        return MpesaPayment.objects.create(
            user=self.client_user,
            phone_number="254712345678",
            amount=Decimal("2500.00"),
            checkout_request_id="ws_CO_RECEIVER_TEST",
            status=MpesaPaymentStatus.PENDING,
            content_type=ContentType.objects.get_for_model(ManagedJob),
            object_id=self.job.id,
        )

    def test_confirmed_payment_settles_managed_job_and_notifies(self):
        payment = self._payment()

        with self.captureOnCommitCallbacks(execute=True):
            process_callback(success_callback(payment.checkout_request_id, "2500.00", "SJK4Q1X8YR"))

        payment.refresh_from_db()
        self.assert_payment_confirmed(payment)

        self.job.refresh_from_db()
        self.assertEqual(self.job.payment_status, ManagedJobPaymentStatus.CONFIRMED)
        self.assertEqual(self.job.status, ManagedJobStatus.PAYMENT_CONFIRMED)
        self.assertIsNotNone(self.job.payment_confirmed_at)

        client_note = Notification.objects.filter(
            user=self.client_user, notification_type=Notification.PAYMENT_CONFIRMED, object_id=self.job.id
        )
        self.assertTrue(client_note.exists())
        self.assertIn("2500.00", client_note.first().message)

        broker_note = Notification.objects.filter(
            user=self.broker, notification_type=Notification.JOB_READY_TO_START, object_id=self.job.id
        )
        self.assertTrue(broker_note.exists())

        self.assertTrue(
            JobStatusEvent.objects.filter(
                managed_job=self.job, event_type=audit_services.EVENT_PAYMENT_CONFIRMED
            ).exists()
        )

    def assert_payment_confirmed(self, payment: MpesaPayment) -> None:
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(payment.reconciliation_status, MpesaReconciliationStatus.CONFIRMED)
        self.assertEqual(payment.mpesa_receipt_number, "SJK4Q1X8YR")

    def test_duplicate_callback_does_not_double_settle_or_notify(self):
        payment = self._payment()

        with self.captureOnCommitCallbacks(execute=True):
            process_callback(success_callback(payment.checkout_request_id, "2500.00", "SJK4Q1X8YR"))

        notification_count = Notification.objects.filter(
            user=self.client_user, notification_type=Notification.PAYMENT_CONFIRMED, object_id=self.job.id
        ).count()
        event_count = JobStatusEvent.objects.filter(
            managed_job=self.job, event_type=audit_services.EVENT_PAYMENT_CONFIRMED
        ).count()
        confirmed_at = MpesaPayment.objects.get(pk=payment.pk).confirmed_at

        with self.captureOnCommitCallbacks(execute=True):
            process_callback(success_callback(payment.checkout_request_id, "2500.00", "SJK4Q1X8YR"))

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(payment.confirmed_at, confirmed_at)
        self.assertEqual(
            Notification.objects.filter(
                user=self.client_user, notification_type=Notification.PAYMENT_CONFIRMED, object_id=self.job.id
            ).count(),
            notification_count,
        )
        self.assertEqual(
            JobStatusEvent.objects.filter(
                managed_job=self.job, event_type=audit_services.EVENT_PAYMENT_CONFIRMED
            ).count(),
            event_count,
        )

    def test_draft_job_advances_to_payment_confirmed(self):
        self.job.status = ManagedJobStatus.DRAFT
        self.job.save(update_fields=["status"])
        payment = self._payment()

        with self.captureOnCommitCallbacks(execute=True):
            process_callback(success_callback(payment.checkout_request_id, "2500.00", "SJK4Q1X8YR"))

        self.job.refresh_from_db()
        self.assertEqual(self.job.status, ManagedJobStatus.PAYMENT_CONFIRMED)
        self.assertEqual(self.job.payment_status, ManagedJobPaymentStatus.CONFIRMED)

    def test_unhandled_payable_is_logged_not_crashed(self):
        unrelated = MpesaPayment.objects.create(
            user=self.client_user,
            phone_number="254712345678",
            amount=Decimal("1.00"),
            checkout_request_id="ws_CO_OTHER_PAYABLE",
            status=MpesaPaymentStatus.PENDING,
            content_type=ContentType.objects.get_for_model(self.client_user),
            object_id=self.client_user.id,
        )

        with self.captureOnCommitCallbacks(execute=True):
            process_callback(success_callback(unrelated.checkout_request_id, "1.00", "SJK4Q1X8YR"))

        unrelated.refresh_from_db()
        self.assertEqual(unrelated.status, MpesaPaymentStatus.PAID)