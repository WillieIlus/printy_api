"""Audit #1c: recovery of lost M-Pesa STK confirmations (mocked Daraja).

Daraja is never contacted: the recovery service gets a fake client, and the
client's own STK Query method is tested against a mocked ``requests.post``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.contrib.admin.models import LogEntry
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from jobs.models import ManagedJob
from mpesa_payments.test_audit_callback_and_amounts import DARAJA, _Fixtures, stk_body
from payments import recovery
from payments.models import MpesaSTKRequest, Payment
from payments.services import (
    MpesaDarajaClient,
    MpesaDarajaError,
    create_payment_for_quote,
    handle_stk_callback,
    initiate_stk_push,
)
from pricing.models import PlatformFeePolicy
from quotes.acceptance import accept_quote_for_payment

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE = {**DARAJA, "MPESA_FORCE_STUB": False, "MPESA_ENVIRONMENT": "sandbox", "MPESA_STK_IN_FLIGHT_SECONDS": 180}
STUB = {**DARAJA, "MPESA_FORCE_STUB": False, "MPESA_ENVIRONMENT": "test"}


def recover_url(payment_id):
    return f"/api/admin/payments/{payment_id}/recover/"


def final(result_code, desc="", *, amount=None, receipt=None):
    """A Daraja STK Query answer for a finished prompt (documented shape)."""
    data = {
        "ResponseCode": "0",
        "ResponseDescription": "The service request has been accepted successsfully",
        "MerchantRequestID": "mr-1",
        "CheckoutRequestID": "ws_CO_LOST_1",
        "ResultCode": str(result_code),
        "ResultDesc": desc or ("The service request is processed successfully." if result_code == 0 else "failed"),
    }
    items = []
    if amount is not None:
        items.append({"Name": "Amount", "Value": amount})
    if receipt is not None:
        items.append({"Name": "MpesaReceiptNumber", "Value": receipt})
    if items:
        data["CallbackMetadata"] = {"Item": items}
    return {"state": "final", "data": data}


PROCESSING = {"state": "processing", "data": {"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}}


class FakeClient:
    """Stands in for MpesaDarajaClient; answers (or raises) from a script."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def query_stk_status(self, *, checkout_request_id):
        self.calls.append(checkout_request_id)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def daraja_error(http_status=500):
    error = MpesaDarajaError("Daraja exploded")
    error.http_status = http_status
    return error


class _RecoveryFixtures(_Fixtures):
    def make_lost_payment(self, *, age=timedelta(minutes=10), checkout="ws_CO_LOST_1", quote=None):
        if quote is None:
            _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.buyer)
        else:
            _quote, payment = accept_quote_for_payment(quote=quote, accepted_by=self.buyer)
        stk = MpesaSTKRequest.objects.create(
            payment=payment,
            phone_number="254700000000",
            amount=payment.expected_amount,
            account_reference=payment.account_reference,
            status=MpesaSTKRequest.STATUS_SENT,
            checkout_request_id=checkout,
            merchant_request_id="mr-1",
        )
        MpesaSTKRequest.objects.filter(pk=stk.pk).update(requested_at=timezone.now() - age)
        payment.status = Payment.STATUS_PROCESSING
        payment.checkout_request_id = checkout
        payment.save(update_fields=["status", "checkout_request_id", "updated_at"])
        stk.refresh_from_db()
        return payment, stk

    def snapshot(self, payment, stk):
        payment.refresh_from_db()
        stk.refresh_from_db()
        return (
            payment.status,
            payment.received_amount,
            payment.mpesa_receipt_number,
            payment.updated_at,
            stk.status,
            stk.callback_received_at,
            stk.updated_at,
        )


@override_settings(**LIVE)
class RecoverLostConfirmationTests(_RecoveryFixtures, TestCase):
    def setUp(self):
        cache.clear()
        self.build()
        self.payment, self.stk = self.make_lost_payment()

    # -- the five required behaviours ------------------------------------
    def test_lost_success_with_receipt_and_amount_is_recovered_to_paid(self):
        client = FakeClient(final(0, amount=5000, receipt="QLOST001"))
        result = recovery.recover_payment(self.payment, client=client)

        self.assertEqual(result.outcome, recovery.OUTCOME_PAID)
        self.assertEqual(client.calls, ["ws_CO_LOST_1"])
        self.payment.refresh_from_db()
        self.stk.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)
        self.assertEqual(self.payment.mpesa_receipt_number, "QLOST001")
        self.assertEqual(self.payment.received_amount, Decimal("5000.00"))
        self.assertEqual(self.stk.status, MpesaSTKRequest.STATUS_SUCCESS)
        self.assertIsNotNone(self.stk.callback_received_at)
        self.assertEqual(self.stk.raw_callback["RecoverySource"], "stk_query")
        self.assertTrue(ManagedJob.objects.filter(source_quote=self.quote).exists())

    def test_bare_query_success_is_never_paid_but_reaches_needs_review(self):
        # Daraja's documented STK Query answer: ResultCode 0, no receipt/amount.
        result = recovery.recover_payment(self.payment, client=FakeClient(final(0)))

        self.assertEqual(result.outcome, recovery.OUTCOME_NEEDS_REVIEW)
        self.payment.refresh_from_db()
        self.stk.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_NEEDS_REVIEW)
        self.assertEqual(self.stk.status, MpesaSTKRequest.STATUS_NEEDS_REVIEW)
        self.assertIn("STK Query", self.stk.response_description)
        self.assertIsNone(self.payment.confirmed_at)
        self.assertFalse(ManagedJob.objects.filter(source_quote=self.quote).exists())

    def test_bare_query_success_then_admin_resolution_recovers_to_paid(self):
        # The lost success, end to end, with the answer Daraja really gives.
        recovery.recover_payment(self.payment, client=FakeClient(final(0)))
        admin = User.objects.create_superuser(email="root@recovery.test", password="pw")
        api = APIClient()
        api.force_authenticate(admin)
        response = api.post(
            f"/api/admin/payments/{self.payment.id}/resolve-review/",
            {
                "resolution": "confirm_paid",
                "reason": "Statement shows QLOST002 for KES 5000",
                "receipt_number": "QLOST002",
                "amount": "5000",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)
        self.assertTrue(ManagedJob.objects.filter(source_quote=self.quote).exists())

    def test_wrong_amount_goes_to_needs_review(self):
        result = recovery.recover_payment(self.payment, client=FakeClient(final(0, amount=1, receipt="QLOST003")))

        self.assertEqual(result.outcome, recovery.OUTCOME_NEEDS_REVIEW)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_NEEDS_REVIEW)
        self.assertEqual(self.payment.received_amount, Decimal("1.00"))
        self.assertFalse(ManagedJob.objects.filter(source_quote=self.quote).exists())

    def test_daraja_error_leaves_state_untouched(self):
        before = self.snapshot(self.payment, self.stk)
        for error in (daraja_error(500), daraja_error(None), daraja_error(429), ImproperlyConfigured("no keys")):
            with self.subTest(error=repr(error)):
                result = recovery.recover_payment(self.payment, client=FakeClient(error), respect_cooldown=False)
                self.assertIn(result.outcome, {recovery.OUTCOME_ERROR, recovery.OUTCOME_RATE_LIMITED})
                self.assertEqual(self.snapshot(self.payment, self.stk), before)

    def test_malformed_final_answer_changes_nothing(self):
        before = self.snapshot(self.payment, self.stk)
        answer = {"state": "final", "data": {"ResultCode": "not-a-number"}}
        result = recovery.recover_payment(self.payment, client=FakeClient(answer))
        self.assertEqual(result.outcome, recovery.OUTCOME_ERROR)
        self.assertEqual(self.snapshot(self.payment, self.stk), before)

    def test_already_final_payments_are_untouched_and_not_queried(self):
        for status in (
            Payment.STATUS_PAID,
            Payment.STATUS_FAILED,
            Payment.STATUS_CANCELLED,
            Payment.STATUS_NEEDS_REVIEW,
        ):
            with self.subTest(status=status):
                Payment.objects.filter(pk=self.payment.pk).update(status=status)
                before = self.snapshot(self.payment, self.stk)
                client = FakeClient(final(0, amount=5000, receipt="QFINAL01"))
                result = recovery.recover_payment(self.payment, client=client, respect_cooldown=False)
                run = recovery.recover_stale_payments(client=client, sleep=lambda s: None)
                self.assertEqual(result.outcome, recovery.OUTCOME_SKIPPED_FINAL)
                self.assertEqual(run.results, [])
                self.assertEqual(client.calls, [])
                self.assertEqual(self.snapshot(self.payment, self.stk), before)

    def test_running_twice_is_idempotent(self):
        client = FakeClient(final(0, amount=5000, receipt="QTWICE01"))
        first = recovery.recover_stale_payments(client=client, sleep=lambda s: None)
        cache.clear()  # even with the cooldown gone, nothing is re-applied
        second = recovery.recover_stale_payments(client=client, sleep=lambda s: None)
        third = recovery.recover_payment(self.payment, client=client, respect_cooldown=False)

        self.assertEqual(first.counts(), {recovery.OUTCOME_PAID: 1})
        self.assertEqual(second.results, [])
        self.assertEqual(third.outcome, recovery.OUTCOME_SKIPPED_FINAL)
        self.assertEqual(client.calls, ["ws_CO_LOST_1"])
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)

    def test_real_callback_after_recovery_is_a_duplicate(self):
        recovery.recover_payment(self.payment, client=FakeClient(final(0, amount=5000, receipt="QDUP0001")))
        handled = handle_stk_callback(callback_payload=stk_body("ws_CO_LOST_1", amount="5000", receipt="QDUP0001"))
        self.assertEqual(handled.get("duplicate"), "true")
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)

    # -- failures, expiry, late success ----------------------------------
    def test_failure_answer_uses_the_callback_failure_branch(self):
        result = recovery.recover_payment(self.payment, client=FakeClient(final(1032, "Request cancelled by user")))
        self.assertEqual(result.outcome, recovery.OUTCOME_FAILED)
        self.payment.refresh_from_db()
        self.stk.refresh_from_db()
        self.assertEqual(self.stk.status, MpesaSTKRequest.STATUS_CANCELLED)
        # Same as a real 1032 callback: the payment becomes cancelled.
        self.assertEqual(self.payment.status, Payment.STATUS_CANCELLED)

    def test_still_processing_past_the_window_expires_prompt_and_payment(self):
        result = recovery.recover_payment(self.payment, client=FakeClient(PROCESSING))
        self.assertEqual(result.outcome, recovery.OUTCOME_EXPIRED)
        self.payment.refresh_from_db()
        self.stk.refresh_from_db()
        self.assertEqual(self.stk.status, MpesaSTKRequest.STATUS_EXPIRED)
        self.assertEqual(self.payment.status, Payment.STATUS_EXPIRED)
        self.assertIsNone(self.stk.callback_received_at)

    def test_still_processing_inside_the_window_changes_nothing(self):
        MpesaSTKRequest.objects.filter(pk=self.stk.pk).update(requested_at=timezone.now() - timedelta(seconds=30))
        before = self.snapshot(self.payment, self.stk)
        result = recovery.recover_payment(self.payment, client=FakeClient(PROCESSING), older_than=timedelta(0))
        self.assertEqual(result.outcome, recovery.OUTCOME_STILL_PROCESSING)
        self.assertEqual(self.snapshot(self.payment, self.stk), before)

    def test_recent_prompts_are_not_queried_by_the_batch(self):
        MpesaSTKRequest.objects.filter(pk=self.stk.pk).update(requested_at=timezone.now() - timedelta(seconds=30))
        client = FakeClient(PROCESSING)
        self.assertEqual(recovery.recover_stale_payments(client=client, sleep=lambda s: None).results, [])
        self.assertEqual(client.calls, [])

    def test_late_success_for_an_expired_prompt_is_still_applied_by_recovery(self):
        recovery.recover_payment(self.payment, client=FakeClient(PROCESSING))
        cache.clear()
        result = recovery.recover_payment(self.payment, client=FakeClient(final(0, amount=5000, receipt="QLATE001")))
        self.assertEqual(result.outcome, recovery.OUTCOME_PAID)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)

    def test_late_success_callback_for_an_expired_payment_is_applied(self):
        recovery.recover_payment(self.payment, client=FakeClient(PROCESSING))
        handle_stk_callback(callback_payload=stk_body("ws_CO_LOST_1", amount="5000", receipt="QLATE002"))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)
        self.assertTrue(ManagedJob.objects.filter(source_quote=self.quote).exists())

    def test_late_failure_for_an_expired_prompt_leaves_payment_expired(self):
        recovery.recover_payment(self.payment, client=FakeClient(PROCESSING))
        cache.clear()
        recovery.recover_payment(self.payment, client=FakeClient(final(1037, "DS timeout")))
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_EXPIRED)

    def test_prompts_older_than_the_max_age_are_not_queried(self):
        MpesaSTKRequest.objects.filter(pk=self.stk.pk).update(requested_at=timezone.now() - timedelta(hours=30))
        client = FakeClient(final(0, amount=5000, receipt="QOLD0001"))
        self.assertEqual(recovery.recover_payment(self.payment, client=client).outcome, recovery.OUTCOME_NO_PROMPT)
        self.assertEqual(client.calls, [])

    # -- rate limiting ----------------------------------------------------
    def test_queries_are_spaced_and_a_429_stops_the_batch(self):
        self.build_second_quote()
        p2, _stk2 = self.make_lost_payment(checkout="ws_CO_LOST_2", quote=self.quote2)
        sleeps = []
        client = FakeClient(PROCESSING, PROCESSING)
        run = recovery.recover_stale_payments(client=client, sleep=sleeps.append, interval_seconds=2.5)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(sleeps, [2.5])

        cache.clear()
        MpesaSTKRequest.objects.update(status=MpesaSTKRequest.STATUS_SENT)
        Payment.objects.update(status=Payment.STATUS_PROCESSING)
        client = FakeClient(daraja_error(429), PROCESSING)
        run = recovery.recover_stale_payments(client=client, sleep=lambda s: None)
        self.assertEqual(run.stopped_reason, "rate_limited")
        self.assertEqual(len(client.calls), 1)
        p2.refresh_from_db()
        self.assertEqual(p2.status, Payment.STATUS_PROCESSING)

    def test_cooldown_stops_requerying_the_same_prompt(self):
        MpesaSTKRequest.objects.filter(pk=self.stk.pk).update(requested_at=timezone.now() - timedelta(seconds=90))
        client = FakeClient(PROCESSING)
        first = recovery.recover_payment(self.payment, client=client, older_than=timedelta(0))
        second = recovery.recover_payment(self.payment, client=client, older_than=timedelta(0))
        self.assertEqual(first.outcome, recovery.OUTCOME_STILL_PROCESSING)
        self.assertEqual(second.outcome, recovery.OUTCOME_SKIPPED_COOLDOWN)
        self.assertEqual(len(client.calls), 1)

    def test_batch_limit_is_respected(self):
        self.build_second_quote()
        self.make_lost_payment(checkout="ws_CO_LOST_2", quote=self.quote2)
        client = FakeClient(PROCESSING)
        run = recovery.recover_stale_payments(client=client, limit=1, sleep=lambda s: None)
        self.assertEqual(len(run.results), 1)
        self.assertEqual(len(client.calls), 1)

    def build_second_quote(self):
        from quotes.models import Quote, QuoteFinancialSplit

        split = QuoteFinancialSplit.objects.get(quote=self.quote)
        self.quote2 = Quote.objects.get(pk=self.quote.pk)
        self.quote2.pk = None
        self.quote2.id = None
        for attr in ("quote_number", "public_id", "reference"):
            if hasattr(self.quote2, attr) and getattr(self.quote2, attr):
                setattr(self.quote2, attr, f"{getattr(self.quote2, attr)}-2")
        self.quote2.save()
        split.pk = None
        split.id = None
        split.quote = self.quote2
        split.save()

    # -- retry after expiry stays on one Payment row ----------------------
    def test_retry_after_expiry_reuses_the_same_payment_row(self):
        recovery.recover_payment(self.payment, client=FakeClient(PROCESSING))
        again = create_payment_for_quote(quote=self.quote, payer=self.buyer)
        self.assertEqual(again.pk, self.payment.pk)
        self.assertEqual(Payment.objects.filter(quote=self.quote).count(), 1)


@override_settings(**LIVE)
class AdminRecoverEndpointTests(_RecoveryFixtures, TestCase):
    def setUp(self):
        cache.clear()
        self.build()
        self.payment, self.stk = self.make_lost_payment()
        self.admin = User.objects.create_superuser(email="root@recovery.test", password="pw")
        self.staff = User.objects.create_user(email="staff@recovery.test", password="pw", is_staff=True)
        self.api = APIClient()

    def test_only_super_admin_can_trigger_recovery(self):
        fake = FakeClient(final(0, amount=5000, receipt="QADMIN01"))
        with patch.object(recovery, "MpesaDarajaClient", return_value=fake):
            for user in (self.buyer, self.broker, self.staff):
                with self.subTest(user=user.email):
                    self.api.force_authenticate(user)
                    self.assertEqual(self.api.post(recover_url(self.payment.id)).status_code, 403)
            self.api.force_authenticate(None)
            self.assertIn(self.api.post(recover_url(self.payment.id)).status_code, {401, 403})
        self.assertEqual(fake.calls, [])
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PROCESSING)

    def test_super_admin_recovers_one_payment_and_it_is_audited(self):
        # A prompt younger than the batch threshold: the admin asks anyway.
        MpesaSTKRequest.objects.filter(pk=self.stk.pk).update(requested_at=timezone.now() - timedelta(seconds=20))
        fake = FakeClient(final(0, amount=5000, receipt="QADMIN02"))
        self.api.force_authenticate(self.admin)
        with patch.object(recovery, "MpesaDarajaClient", return_value=fake):
            response = self.api.post(recover_url(self.payment.id), {"reason": "customer says they paid"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["outcome"], "paid")
        self.assertEqual(response.data["payment_status"], Payment.STATUS_PAID)
        entry = LogEntry.objects.filter(object_id=str(self.payment.id)).latest("action_time")
        payload = json.loads(entry.change_message)
        self.assertEqual(payload["action"], "payment_recovery_query")
        self.assertEqual(payload["outcome"], "paid")
        self.assertEqual(entry.user_id, self.admin.id)

    def test_daraja_error_returns_502_and_changes_nothing(self):
        self.api.force_authenticate(self.admin)
        with patch.object(recovery, "MpesaDarajaClient", return_value=FakeClient(daraja_error(503))):
            response = self.api.post(recover_url(self.payment.id))
        self.assertEqual(response.status_code, 502)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PROCESSING)

    def test_final_payment_is_reported_and_untouched(self):
        Payment.objects.filter(pk=self.payment.pk).update(status=Payment.STATUS_PAID)
        self.api.force_authenticate(self.admin)
        response = self.api.post(recover_url(self.payment.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["outcome"], "skipped_final")

    def test_unknown_payment_is_404(self):
        self.api.force_authenticate(self.admin)
        self.assertEqual(self.api.post(recover_url(999999)).status_code, 404)


@override_settings(**LIVE)
class RecoverCommandTests(_RecoveryFixtures, TestCase):
    def setUp(self):
        cache.clear()
        self.build()
        self.payment, self.stk = self.make_lost_payment()

    def test_command_recovers_and_reports_counts(self):
        out = StringIO()
        fake = FakeClient(final(0, amount=5000, receipt="QCMD0001"))
        with patch.object(recovery, "MpesaDarajaClient", return_value=fake):
            call_command("mpesa_recover_payments", "--limit", "10", stdout=out)
        self.assertIn("paid=1", out.getvalue())
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PAID)

    def test_dry_run_queries_nothing(self):
        out = StringIO()
        fake = FakeClient(final(0, amount=5000, receipt="QCMD0002"))
        with patch.object(recovery, "MpesaDarajaClient", return_value=fake):
            call_command("mpesa_recover_payments", "--dry-run", stdout=out)
        self.assertIn(f"would query payment {self.payment.id}", out.getvalue())
        self.assertEqual(fake.calls, [])
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, Payment.STATUS_PROCESSING)

    def test_single_payment_option(self):
        out = StringIO()
        with patch.object(recovery, "MpesaDarajaClient", return_value=FakeClient(final(0))):
            call_command("mpesa_recover_payments", "--payment-id", str(self.payment.id), stdout=out)
        self.assertIn("outcome=needs_review", out.getvalue())


@override_settings(**STUB)
class StubModeRecoveryTests(_RecoveryFixtures, TestCase):
    def setUp(self):
        cache.clear()
        self.build()

    def test_stub_mode_never_queries_daraja(self):
        payment, stk = self.make_lost_payment()
        client = FakeClient(final(0, amount=5000, receipt="QSTUB001"))
        self.assertEqual(recovery.recover_payment(payment, client=client).outcome, recovery.OUTCOME_SKIPPED_STUB)
        self.assertEqual(recovery.recover_stale_payments(client=client).stopped_reason, "stub_mode")
        self.assertEqual(client.calls, [])
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)

    def test_expired_payment_can_push_again_on_the_same_row(self):
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.buyer)
        first = initiate_stk_push(payment=payment, phone_number="+254700000000")
        MpesaSTKRequest.objects.filter(pk=first.pk).update(
            status=MpesaSTKRequest.STATUS_EXPIRED, requested_at=timezone.now() - timedelta(minutes=10)
        )
        Payment.objects.filter(pk=payment.pk).update(status=Payment.STATUS_EXPIRED)
        second = initiate_stk_push(payment=payment, phone_number="+254700000000")
        self.assertNotEqual(first.pk, second.pk)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)


@override_settings(**LIVE)
class StkQueryClientTests(SimpleTestCase):
    def _post(self, status_code, body=None, exc=None):
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = body if body is not None else {}
        response.text = json.dumps(body or {})
        target = "payments.services.requests.post"
        if exc is not None:
            return patch(target, side_effect=exc)
        return patch(target, return_value=response)

    def _query(self):
        with patch.object(MpesaDarajaClient, "get_access_token", return_value="tok"):
            return MpesaDarajaClient().query_stk_status(checkout_request_id="ws_CO_LOST_1")

    def test_final_answer(self):
        body = {"ResponseCode": "0", "ResultCode": "1032", "ResultDesc": "Request cancelled by user"}
        with self._post(200, body) as post:
            answer = self._query()
        self.assertEqual(answer, {"state": "final", "data": body})
        url = post.call_args.args[0]
        self.assertTrue(url.endswith("/mpesa/stkpushquery/v1/query"))
        self.assertEqual(post.call_args.kwargs["json"]["CheckoutRequestID"], "ws_CO_LOST_1")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tok")

    def test_still_processing_answer(self):
        body = {"requestId": "x", "errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}
        with self._post(500, body):
            self.assertEqual(self._query()["state"], "processing")

    def test_errors_raise_with_http_status(self):
        for status_code, body in ((429, {"errorCode": "429", "errorMessage": "Too many"}), (401, {}), (200, {"foo": 1})):
            with self.subTest(status=status_code), self._post(status_code, body):
                with self.assertRaises(MpesaDarajaError) as ctx:
                    self._query()
                self.assertEqual(ctx.exception.http_status, status_code)

    def test_network_error_raises(self):
        with self._post(0, exc=requests.exceptions.ConnectTimeout("slow")):
            with self.assertRaises(MpesaDarajaError) as ctx:
                self._query()
        self.assertIsNone(ctx.exception.http_status)


class FeePolicyCleanupTests(SimpleTestCase):
    def test_unused_three_x_max_client_price_helper_is_gone(self):
        # It returned P + P x 2.00 = 3x while the enforced partner cap is 2x.
        self.assertFalse(hasattr(PlatformFeePolicy, "get_max_client_price"))


_BOOT = textwrap.dedent(
    """
    import sys
    try:
        import django
        django.setup()
    except Exception as exc:
        print("BOOT_FAILED:" + type(exc).__name__ + ":" + str(exc))
        sys.exit(0)
    from django.conf import settings
    print("INTERVAL=" + str(settings.MPESA_RECOVERY_QUERY_INTERVAL_SECONDS))
    print("COOLDOWN=" + str(settings.MPESA_RECOVERY_QUERY_COOLDOWN_SECONDS))
    print("MAXAGE=" + str(settings.MPESA_RECOVERY_MAX_AGE_HOURS))
    """
)


def _boot(extra: dict) -> str:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/tmp", "DJANGO_SETTINGS_MODULE": "config.test_settings", **extra}
    result = subprocess.run([sys.executable, "-c", _BOOT], capture_output=True, text=True, timeout=180, env=env, cwd=str(REPO_ROOT))
    return (result.stdout or "") + (result.stderr or "")


class RecoverySettingsTests(SimpleTestCase):
    def test_defaults_and_overrides(self):
        out = _boot({})
        self.assertIn("INTERVAL=1.0", out)
        self.assertIn("COOLDOWN=60", out)
        self.assertIn("MAXAGE=24", out)
        out = _boot({"MPESA_RECOVERY_QUERY_INTERVAL_SECONDS": "0.5", "MPESA_RECOVERY_MAX_AGE_HOURS": "6"})
        self.assertIn("INTERVAL=0.5", out)
        self.assertIn("MAXAGE=6", out)

    def test_invalid_values_are_refused_at_startup(self):
        self.assertIn(
            "BOOT_FAILED:ImproperlyConfigured:MPESA_RECOVERY_QUERY_COOLDOWN_SECONDS",
            _boot({"MPESA_RECOVERY_QUERY_COOLDOWN_SECONDS": "soon"}),
        )
        self.assertIn(
            "BOOT_FAILED:ImproperlyConfigured:MPESA_RECOVERY_MAX_AGE_HOURS", _boot({"MPESA_RECOVERY_MAX_AGE_HOURS": "0"})
        )
