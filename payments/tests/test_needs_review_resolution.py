"""Audit #1b: super-admin resolution of needs_review payments."""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.admin.models import LogEntry
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from jobs.choices import ManagedJobPaymentStatus
from jobs.models import ManagedJob
from mpesa_payments.models import MpesaPayment, MpesaPaymentStatus
from mpesa_payments.services import process_callback
from mpesa_payments.test_audit_callback_and_amounts import DARAJA, _Fixtures, stk_body
from payments.models import MpesaSTKRequest, Payment
from payments.services import handle_stk_callback, initiate_stk_push
from quotes.acceptance import accept_quote_for_payment

LIST_URL = "/api/admin/payments/needs-review/"


def resolve_url(payment_id):
    return f"/api/admin/payments/{payment_id}/resolve-review/"


def resolve_mpesa_url(payment_id):
    return f"/api/admin/mpesa-payments/{payment_id}/resolve-review/"


@override_settings(**{**DARAJA, "MPESA_ENVIRONMENT": "test"})
class NeedsReviewResolutionTests(_Fixtures, TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.build()
        self.admin = User.objects.create_superuser(email="root@audit.test", password="pw")
        self.staff = User.objects.create_user(email="staff@audit.test", password="pw", is_staff=True)
        _quote, self.payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.buyer)
        self.stk = initiate_stk_push(payment=self.payment, phone_number="+254700000000")
        # Daraja reports KES 1 for a KES 5000 payment -> needs_review.
        handle_stk_callback(callback_payload=stk_body(self.stk.checkout_request_id, amount="1", receipt="QREVIEW1"))
        self.payment.refresh_from_db()
        assert self.payment.status == Payment.STATUS_NEEDS_REVIEW

    def _resolve(self, user, **body):
        self.api.force_authenticate(user)
        return self.api.post(resolve_url(self.payment.id), body, format="json")

    def test_non_admin_gets_403_and_nothing_changes(self):
        for user in (self.buyer, self.broker, self.staff):
            with self.subTest(user=user.email):
                self.api.force_authenticate(user)
                self.assertEqual(self.api.get(LIST_URL).status_code, 403)
                response = self._resolve(
                    user, resolution="confirm_paid", reason="x", receipt_number="QREVIEW1", amount="5000"
                )
                self.assertEqual(response.status_code, 403)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_NEEDS_REVIEW)
        self.assertFalse(ManagedJob.objects.filter(source_quote=self.quote).exists())

    def test_admin_lists_needs_review_payments(self):
        self.api.force_authenticate(self.admin)
        response = self.api.get(LIST_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([(r["kind"], r["id"]) for r in response.data["results"]], [("payment", self.payment.id)])

    def test_confirm_paid_runs_the_canonical_paid_path_and_is_audited(self):
        response = self._resolve(
            self.admin,
            resolution="confirm_paid",
            reason="Safaricom statement shows KES 5000 for QREVIEW1",
            receipt_number="qreview1",
            amount="5000.00",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.payment.refresh_from_db()
        self.stk.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)
        self.assertEqual(self.payment.received_amount, Decimal("5000.00"))
        self.assertEqual(self.stk.status, MpesaSTKRequest.STATUS_SUCCESS)
        job = ManagedJob.objects.get(source_quote=self.quote)
        self.assertEqual(job.payment_status, ManagedJobPaymentStatus.CONFIRMED)
        self.assertEqual(self.payment.managed_job_id, job.id)

        entry = LogEntry.objects.filter(object_id=str(self.payment.id)).latest("action_time")
        payload = json.loads(entry.change_message)
        self.assertEqual(entry.user_id, self.admin.id)
        self.assertEqual(payload["action"], "needs_review_confirm_paid")
        self.assertEqual(payload["reason"], "Safaricom statement shows KES 5000 for QREVIEW1")
        self.assertEqual(payload["receipt_number"], "qreview1")
        self.assertTrue(payload["resolved_at"])

    def test_confirm_paid_amount_mismatch_needs_an_override_reason(self):
        response = self._resolve(
            self.admin, resolution="confirm_paid", reason="checked", receipt_number="QREVIEW1", amount="1"
        )
        self.assertEqual(response.status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_NEEDS_REVIEW)

        response = self._resolve(
            self.admin,
            resolution="confirm_paid",
            reason="checked",
            receipt_number="QREVIEW1",
            amount="1",
            override_reason="Balance of 4999 collected in cash at the shop, receipt C-778",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)
        self.assertEqual(self.payment.received_amount, Decimal("1.00"))
        payload = json.loads(LogEntry.objects.filter(object_id=str(self.payment.id)).latest("action_time").change_message)
        self.assertIn("cash", payload["amount_override_reason"])

    def test_confirm_paid_refuses_wrong_receipt_and_missing_reason(self):
        self.assertEqual(
            self._resolve(self.admin, resolution="confirm_paid", reason="r", receipt_number="OTHER", amount="5000").status_code,
            400,
        )
        self.assertEqual(
            self._resolve(self.admin, resolution="confirm_paid", receipt_number="QREVIEW1", amount="5000").status_code,
            400,
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_NEEDS_REVIEW)

    def test_mark_failed_re_enables_a_fresh_stk_push(self):
        # Blocked while under review.
        self.api.force_authenticate(self.buyer)
        blocked = self.api.post(
            "/api/payments/mpesa/stk-push/", {"phone_number": "0712345678", "quote_id": self.quote.id}, format="json"
        )
        self.assertEqual(blocked.status_code, 400)

        response = self._resolve(self.admin, resolution="mark_failed", reason="Customer was refunded by Safaricom")
        self.assertEqual(response.status_code, 200, response.data)
        self.payment.refresh_from_db()
        self.stk.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_FAILED)
        self.assertEqual(self.stk.status, MpesaSTKRequest.STATUS_FAILED)

        self.api.force_authenticate(self.buyer)
        retry = self.api.post(
            "/api/payments/mpesa/stk-push/", {"phone_number": "0712345678", "quote_id": self.quote.id}, format="json"
        )
        self.assertEqual(retry.status_code, 201, retry.data)
        fresh = Payment.objects.get(pk=retry.data["payment_id"])
        self.assertNotEqual(fresh.id, self.payment.id)
        self.assertEqual(fresh.amount, Decimal("5000.00"))
        self.assertEqual(fresh.mpesa_stk_requests.filter(status=MpesaSTKRequest.STATUS_SENT).count(), 1)

    def test_resolving_a_non_review_payment_is_refused(self):
        self._resolve(self.admin, resolution="mark_failed", reason="r")
        again = self._resolve(self.admin, resolution="mark_failed", reason="r")
        self.assertEqual(again.status_code, 400)


@override_settings(**DARAJA)
class CashierNeedsReviewResolutionTests(_Fixtures, TestCase):
    def setUp(self):
        self.api = APIClient()
        self.build()
        self.build_job()
        self.admin = User.objects.create_superuser(email="root2@audit.test", password="pw")
        self.cashier = MpesaPayment.objects.create(
            user=self.buyer, phone_number="254712345678", amount=Decimal("5000.00"), payable=self.job
        )
        self.cashier.mark_push_sent(merchant_request_id="mr", checkout_request_id="ws_CO_REVIEW")
        process_callback(stk_body("ws_CO_REVIEW", amount="4000", receipt="QCASH1"))
        self.cashier.refresh_from_db()
        assert self.cashier.status == MpesaPaymentStatus.NEEDS_REVIEW

    def test_non_admin_gets_403(self):
        self.api.force_authenticate(self.buyer)
        response = self.api.post(
            resolve_mpesa_url(self.cashier.id), {"resolution": "mark_failed", "reason": "x"}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_confirm_paid_settles_the_managed_job(self):
        self.api.force_authenticate(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(
                resolve_mpesa_url(self.cashier.id),
                {"resolution": "confirm_paid", "reason": "Second 1000 debit found", "receipt_number": "QCASH1", "amount": "5000"},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.cashier.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(self.cashier.status, MpesaPaymentStatus.PAID)
        self.assertEqual(self.job.payment_status, ManagedJobPaymentStatus.CONFIRMED)
