"""Tests for the M-Pesa flow.

The critical cases come straight from docs/DARAJA_PRODUCTION_CHECKLIST.md:
never mark paid from STK initiation, handle duplicates without
double-processing, and route amount mismatches to needs_review.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .models import MpesaCallbackLog, MpesaPayment, MpesaPaymentStatus, MpesaReconciliationStatus
from .services import MpesaConfigError, normalize_msisdn, process_callback

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
                        {"Name": "Amount", "Value": 1},
                        {"Name": "MpesaReceiptNumber", "Value": receipt},
                        {"Name": "Balance"},
                        {"Name": "TransactionDate", "Value": 20251114114700},
                        {"Name": "PhoneNumber", "Value": 254712345678},
                    ]
                },
            }
        }
    }


class PhoneNormalizationTests(APITestCase):
    def test_accepts_all_documented_formats(self):
        self.assertEqual(normalize_msisdn("254712345678"), "254712345678")
        self.assertEqual(normalize_msisdn("0712345678"), "254712345678")
        self.assertEqual(normalize_msisdn("712345678"), "254712345678")
        self.assertEqual(normalize_msisdn("+254 712 345 678"), "254712345678")

    def test_rejects_invalid_numbers(self):
        from django.core.exceptions import ValidationError

        for bad in ["", "12345", "254712345678901", "254800000000"]:
            with self.assertRaises(ValidationError):
                normalize_msisdn(bad)


@override_settings(**DARAJA_SETTINGS)
class StkPushApiTests(APITestCase):
    def _push(self, phone="0712345678", amount="2500.00"):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(email="buyer@example.com", password="pw")
        self.client.force_authenticate(user=user)

        with patch("mpesa_payments.services.requests.post") as post, patch(
            "mpesa_payments.services.get_access_token", return_value="tok"
        ):
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                "MerchantRequestID": "29115-34620561-1",
                "CheckoutRequestID": "ws_CO_191220191020363925",
                "ResponseCode": "0",
                "CustomerMessage": "Success. Request accepted for processing",
            }
            response = self.client.post(
                "/api/payments/mpesa/stk-push/",
                {"phone_number": phone, "amount": amount},
                format="json",
            )
        return response

    def test_stk_push_returns_pending_not_paid(self):
        response = self._push()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # The single most important rule: initiating a push is never success.
        self.assertEqual(response.data["status"], MpesaPaymentStatus.PENDING)
        self.assertFalse(response.data["is_paid"])
        self.assertTrue(response.data["checkout_request_id"])

    def test_stk_push_normalizes_phone_before_sending(self):
        with patch("mpesa_payments.services.requests.post") as post, patch(
            "mpesa_payments.services.get_access_token", return_value="tok"
        ):
            post.return_value.status_code = 200
            post.return_value.json.return_value = {
                "MerchantRequestID": "m",
                "CheckoutRequestID": "c",
                "CustomerMessage": "ok",
            }
            self.client.force_authenticate(
                user=__import__("django.contrib.auth", fromlist=["get_user_model"]).get_user_model().objects.create_user(
                    email="b2@example.com", password="pw"
                )
            )
            self.client.post(
                "/api/payments/mpesa/stk-push/",
                {"phone_number": "0712345678", "amount": "100"},
                format="json",
            )
            sent = post.call_args.kwargs["json"]
            self.assertEqual(sent["PhoneNumber"], "254712345678")

    def test_rejects_missing_credentials(self):
        with override_settings(MPESA_CONSUMER_KEY=""):
            response = self._push()
            self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)

    def test_callback_requires_no_auth(self):
        self._push()
        payment = MpesaPayment.objects.first()
        response = self.client.post(
            "/api/payments/mpesa/callback/",
            success_callback(payment.checkout_request_id),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


@override_settings(**DARAJA_SETTINGS)
class CallbackTests(APITestCase):
    def make_pending(self, amount="2500.00") -> MpesaPayment:
        return MpesaPayment.objects.create(
            phone_number="254712345678",
            amount=Decimal(amount),
            checkout_request_id="ws_CO_TEST_1",
            merchant_request_id="m1",
            status=MpesaPaymentStatus.PENDING,
        )

    def test_success_callback_marks_paid(self):
        payment = self.make_pending("1.00")
        process_callback(success_callback(payment.checkout_request_id, amount="1"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(payment.reconciliation_status, MpesaReconciliationStatus.CONFIRMED)
        self.assertEqual(payment.mpesa_receipt_number, "SJK4Q1X8YR")
        self.assertIsNotNone(payment.transaction_date)

    def test_amount_mismatch_goes_to_needs_review(self):
        payment = self.make_pending("5000.00")
        process_callback(success_callback(payment.checkout_request_id, amount="1"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.NEEDS_REVIEW)
        self.assertEqual(payment.reconciliation_status, MpesaReconciliationStatus.AMOUNT_MISMATCH)

    def test_duplicate_callback_does_not_double_process(self):
        payment = self.make_pending("1.00")
        first = process_callback(success_callback(payment.checkout_request_id, amount="1"))
        second = process_callback(success_callback(payment.checkout_request_id, amount="1"))

        self.assertTrue(first.processed)
        self.assertTrue(second.duplicate)
        self.assertFalse(second.processed)
        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)
        self.assertEqual(MpesaCallbackLog.objects.count(), 2)
        # confirmed_at never moves on a replay
        self.assertEqual(MpesaPayment.objects.filter(confirmed_at__isnull=False).count(), 1)

    def test_user_cancel_result_code_marks_cancelled(self):
        payment = self.make_pending()
        payload = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "m",
                    "CheckoutRequestID": payment.checkout_request_id,
                    "ResultCode": 1032,
                    "ResultDesc": "Request cancelled by user",
                }
            }
        }
        process_callback(payload)
        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.CANCELLED)

    def test_failure_result_code_marks_failed(self):
        payment = self.make_pending()
        payload = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "m",
                    "CheckoutRequestID": payment.checkout_request_id,
                    "ResultCode": 1,
                    "ResultDesc": "The balance is insufficient for this transaction",
                }
            }
        }
        process_callback(payload)
        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.FAILED)

    def test_unknown_checkout_id_is_logged_not_crashed(self):
        entry = process_callback(success_callback("ws_CO_UNKNOWN"))
        self.assertFalse(entry.processed)
        self.assertEqual(MpesaCallbackLog.objects.count(), 1)

    def test_late_callback_cannot_overwrite_terminal_state(self):
        payment = self.make_pending("1.00")
        process_callback(success_callback(payment.checkout_request_id, amount="1"))
        payment.refresh_from_db()
        # a late failure callback arrives afterwards
        process_callback(
            {
                "Body": {
                    "stkCallback": {
                        "CheckoutRequestID": payment.checkout_request_id,
                        "ResultCode": 1032,
                        "ResultDesc": "Request cancelled by user",
                    }
                }
            }
        )
        payment.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.PAID)


class ConfigGuardTests(APITestCase):
    def test_production_rejects_localhost_callback(self):
        from mpesa_payments.services import validate_production_config

        with override_settings(
            MPESA_ENV="production",
            MPESA_CONSUMER_KEY="k",
            MPESA_CONSUMER_SECRET="s",
            MPESA_SHORTCODE="1",
            MPESA_PASSKEY="p",
            MPESA_CALLBACK_URL="http://localhost:8000/api/payments/mpesa/callback/",
        ):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()

    def test_production_requires_https(self):
        from mpesa_payments.services import validate_production_config

        with override_settings(
            MPESA_ENV="production",
            MPESA_CONSUMER_KEY="k",
            MPESA_CONSUMER_SECRET="s",
            MPESA_SHORTCODE="1",
            MPESA_PASSKEY="p",
            MPESA_CALLBACK_URL="http://api.printy.ke/api/payments/mpesa/callback/",
        ):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()
