"""Transaction Status Query: request shape, async Result handling, endpoints."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from mpesa_payments.models import MpesaCallbackLog, MpesaPayment, MpesaPaymentStatus
from mpesa_payments.services import (
    MpesaConfigError,
    MpesaError,
    process_transaction_status_result,
    query_transaction_status,
)

STATUS_URL = "/api/payments/mpesa/result/"

DARAAJA_STATUS_SETTINGS = {
    "MPESA_ENV": "sandbox",
    "MPESA_SHORTCODE_TYPE": "paybill",
    "MPESA_SHORTCODE": "174379",
    "MPESA_PASSKEY": "status-passkey",
    "MPESA_CALLBACK_URL": "https://api.printy.ke/api/payments/mpesa/callback/",
}

RESULT_SUCCESS = {
    "Result": {
        "ResultType": "Transaction Status",
        "ResultCode": "0",
        "ResultDesc": "The transaction is successful.",
        "TransactionID": "QG1234XY",
        "TransactionDate": "2026092905231017",
        "TransactionAmount": 2500,
        "InitiatorTransactionType": "CustomerPayBillOnline",
    }
}


def make_payment(**kwargs) -> MpesaPayment:
    defaults = {
        "phone_number": "254705482738",
        "amount": Decimal("2500.00"),
        "mpesa_receipt_number": "QG1234XY",
        "checkout_request_id": "ws_CO_123",
        "status": MpesaPaymentStatus.PENDING,
    }
    defaults.update(kwargs)
    payment = MpesaPayment.objects.create(**defaults)
    payment.mark_push_sent(
        merchant_request_id="mr_123", checkout_request_id=defaults["checkout_request_id"]
    )
    payment.refresh_from_db()
    return payment


@override_settings(**DARAAJA_STATUS_SETTINGS)
class QueryTransactionStatusTests(TestCase):
    @patch("mpesa_payments.services.get_access_token", return_value="tok")
    @patch("mpesa_payments.services.requests.post")
    def test_sends_documented_payload(self, post, _token):
        post.return_value = _resp(200, {"ResponseCode": "0", "ResponseDescription": "Notification sent successfully"})

        query_transaction_status(transaction_id="QG1234XY", party_a="0712345678")

        url, = post.call_args[0]
        self.assertEqual(url, "https://sandbox.safaricom.co.ke/mpesa/transactionstatus/v1/query")
        payload = post.call_args[1]["json"]
        self.assertEqual(payload["TransactionType"], "TransactionStatusQuery")
        self.assertEqual(payload["TransactionID"], "QG1234XY")
        self.assertEqual(payload["PartyA"], "254712345678")  # normalized from 0712…
        self.assertEqual(payload["IdentifierType"], "4")
        self.assertEqual(payload["BusinessShortCode"], "174379")
        self.assertEqual(payload["PartyB"], "174379")
        self.assertTrue(payload["Password"])
        self.assertEqual(payload["ResultURL"], "https://api.printy.ke/api/payments/mpesa/callback/")
        self.assertEqual(payload["QueueTimeOutURL"], "https://api.printy.ke/api/payments/mpesa/callback/")
        self.assertEqual(post.call_args[1]["headers"]["Authorization"], "Bearer tok")

    @patch("mpesa_payments.services.get_access_token", return_value="tok")
    @patch("mpesa_payments.services.requests.post")
    def test_blank_result_url_falls_back_to_callback(self, post, _token):
        post.return_value = _resp(200, {"ResponseCode": "0"})

        with override_settings(MPESA_RESULT_URL="", MPESA_TIMEOUT_URL=""):
            query_transaction_status(transaction_id="QG1234XY", party_a="0712345678")

        payload = post.call_args[1]["json"]
        self.assertTrue(payload["ResultURL"])
        self.assertTrue(payload["QueueTimeOutURL"])

    @patch("mpesa_payments.services.get_access_token", return_value="tok")
    @patch("mpesa_payments.services.requests.post")
    def test_rejection_raises_with_daraja_message(self, post, _token):
        post.return_value = _resp(
            400, {"errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid TransactionID"}
        )

        with self.assertRaises(MpesaError) as ctx:
            query_transaction_status(transaction_id="NOPE", party_a="0712345678")
        self.assertIn("Invalid TransactionID", str(ctx.exception))

    @patch("mpesa_payments.services.requests.post", side_effect=__import__("requests").ConnectionError("boom"))
    def test_network_failure_is_typed(self, _post):
        with self.assertRaises(MpesaError) as ctx:
            query_transaction_status(transaction_id="QG1234XY", party_a="0712345678")
        self.assertIn("could not reach", str(ctx.exception).lower())

    def test_receipt_is_required(self):
        with self.assertRaises(MpesaError):
            query_transaction_status(transaction_id="  ", party_a="0712345678")

    def test_invalid_phone_rejected(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            query_transaction_status(transaction_id="QG1234XY", party_a="12345")

    @override_settings(
        MPESA_ENV="sandbox",
        MPESA_SHORTCODE_TYPE="buygoodsonline",
        MPESA_SHORTCODE="",
        MPESA_PASSKEY="",
        MPESA_CALLBACK_URL="https://api.printy.ke/api/payments/mpesa/callback/",
    )
    def test_unavailable_for_buygoods_app(self):
        with self.assertRaises(MpesaConfigError) as ctx:
            query_transaction_status(transaction_id="QG1234XY", party_a="0712345678")
        self.assertIn("buygoodsonline", str(ctx.exception))


class ProcessTransactionStatusResultTests(TestCase):
    def test_success_confirms_pending_payment(self):
        payment = make_payment()
        # STK callback already stored the receipt but never landed the success.
        entry = process_transaction_status_result(RESULT_SUCCESS)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(payment.paid_amount, Decimal("2500"))
        self.assertEqual(payment.reconciliation_status, "confirmed")
        self.assertIsNotNone(payment.transaction_date)
        self.assertTrue(entry.processed)
        self.assertEqual(entry.result_code, "0")

    def test_amount_mismatch_goes_to_needs_review(self):
        payment = make_payment(amount=Decimal("3000.00"))
        entry = process_transaction_status_result(RESULT_SUCCESS)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.NEEDS_REVIEW)
        self.assertEqual(payment.reconciliation_status, "amount_mismatch")
        self.assertEqual(payment.paid_amount, Decimal("2500"))
        self.assertTrue(entry.processed)

    def test_does_not_overwrite_a_paid_payment(self):
        payment = make_payment()
        payment.mark_confirmed(receipt_number="QG1234XY", paid_amount=Decimal("2500.00"))
        entry = process_transaction_status_result(RESULT_SUCCESS)

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertTrue(entry.duplicate)
        self.assertFalse(entry.processed)

    def test_failure_code_marks_failed(self):
        payment = make_payment()
        process_transaction_status_result(
            {"Result": {"ResultCode": "1032", "ResultDesc": "Request cancelled by user",
                        "TransactionID": "QG1234XY"}}
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.FAILED)
        self.assertEqual(payment.result_code, "1032")

    def test_success_without_amount_needs_review(self):
        payment = make_payment()
        entry = process_transaction_status_result(
            {"Result": {"ResultCode": "0", "ResultDesc": "ok", "TransactionID": "QG1234XY"}}
        )

        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.NEEDS_REVIEW)
        self.assertTrue(entry.processed)

    def test_unknown_receipt_is_logged_not_applied(self):
        entry = process_transaction_status_result(RESULT_SUCCESS)

        self.assertFalse(entry.processed)
        self.assertEqual(MpesaCallbackLog.objects.count(), 1)
        self.assertEqual(MpesaPayment.objects.count(), 0)

    def test_missing_result_block_is_tolerated(self):
        entry = process_transaction_status_result({"nonsense": True})

        self.assertFalse(entry.processed)
        self.assertEqual(MpesaCallbackLog.objects.count(), 1)


class TransactionStatusEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_result_url_is_public_and_always_200(self):
        response = self.client.post(STATUS_URL, RESULT_SUCCESS, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["ResultCode"], 0)

    def test_result_url_never_500s_on_garbage(self):
        response = self.client.post(STATUS_URL, {"garbage": True}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_main_callback_routes_result_payloads(self):
        """A misconfigured MPESA_RESULT_URL must not be read as an STK callback."""
        # Imported inside the view, so patch the source module.
        with patch("payments.services.handle_stk_callback") as canonical:
            response = self.client.post(
                "/api/payments/mpesa/callback/", RESULT_SUCCESS, format="json"
            )

        self.assertEqual(response.status_code, 200)
        canonical.assert_not_called()
        self.assertEqual(MpesaCallbackLog.objects.count(), 1)

    def test_main_callback_still_handles_stk_payloads(self):
        body = {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": "ws_CO_123",
                    "MerchantRequestID": "mr_123",
                    "ResultCode": "0",
                    "ResultDesc": "Success",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "MpesaReceiptNumber", "Value": "QG1234XY"},
                            {"Name": "Amount", "Value": 2500},
                        ]
                    },
                }
            }
        }
        payment = make_payment()

        response = self.client.post("/api/payments/mpesa/callback/", body, format="json")

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)


def _resp(status_code: int, payload: dict):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response
