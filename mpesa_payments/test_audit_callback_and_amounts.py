"""Regression tests for audit #1 (callback authenticity) and #2 (amount/ownership).

Every test here failed on the pre-fix code (baseline a468380) and passes after.
No real Daraja credentials are needed: settings are overridden and every
outbound Daraja call is mocked (or the canonical stub mode is used).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from jobs.choices import JobFileType, ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobFile, ManagedJob
from mpesa_payments.models import MpesaPayment, MpesaPaymentStatus
from payments.callback_security import resolve_client_ip, signed_callback_url
from payments.models import MpesaSTKRequest, Payment
from payments.services import initiate_stk_push as canonical_initiate_stk_push
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.acceptance import accept_quote_for_payment
from quotes.artwork_review import approve_artwork, mark_pending
from quotes.choices import QuoteOfferStatus
from quotes.models import ProductionOption, Quote, QuoteRequest, QuoteRequestAttachment
from shops.models import Shop

SECRET = "unit-test-callback-secret-0123456789abcdefABCDEF"
CALLBACK = "/api/payments/mpesa/callback/"
RESULT = "/api/payments/mpesa/result/"
LEGACY = "/api/payments/mpesa-callback/"
STK_PUSH = "/api/payments/mpesa/stk-push/"

DARAJA = dict(
    MPESA_ENV="sandbox",
    MPESA_ENVIRONMENT="sandbox",
    MPESA_CONSUMER_KEY="test-key",
    MPESA_CONSUMER_SECRET="test-secret",
    MPESA_SHORTCODE_TYPE="paybill",
    MPESA_SHORTCODE="174379",
    MPESA_PASSKEY="test-passkey",
    MPESA_CALLBACK_URL="https://api.printy.ke/api/payments/mpesa/callback/",
    MPESA_CALLBACK_SECRET="",
    MPESA_CALLBACK_SECRET_REQUIRED=False,
    MPESA_CALLBACK_ALLOWED_IPS=[],
    MPESA_TRUSTED_PROXIES=["127.0.0.1", "::1"],
)


def stk_body(checkout_id, *, result_code=0, amount=None, receipt=None, desc="Success"):
    stk = {
        "MerchantRequestID": "mr-1",
        "CheckoutRequestID": checkout_id,
        "ResultCode": result_code,
        "ResultDesc": desc,
    }
    items = []
    if amount is not None:
        items.append({"Name": "Amount", "Value": amount})
    if receipt is not None:
        items.append({"Name": "MpesaReceiptNumber", "Value": receipt})
    if items:
        stk["CallbackMetadata"] = {"Item": items}
    return {"Body": {"stkCallback": stk}}


def daraja_ok(checkout_id="ws_CO_OK_1"):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "MerchantRequestID": "mr-ok",
        "CheckoutRequestID": checkout_id,
        "ResponseCode": "0",
        "CustomerMessage": "Success. Request accepted for processing",
    }
    return response


def daraja_rejected():
    response = MagicMock()
    response.status_code = 500
    response.json.return_value = {"errorMessage": "System busy"}
    return response


class _Fixtures:
    """A KES 5000 quote (split), its request with approved artwork, and a job."""

    def build(self, *, client_total=Decimal("5000.00"), approve=True):
        self.buyer = User.objects.create_user(email="buyer@audit.test", password="pw", role=User.Role.CLIENT)
        self.other = User.objects.create_user(email="other@audit.test", password="pw", role=User.Role.CLIENT)
        self.broker = User.objects.create_user(email="broker@audit.test", password="pw", role=User.Role.PARTNER)
        owner = User.objects.create_user(email="shop@audit.test", password="pw", role=User.Role.PRODUCTION)
        self.shop = Shop.objects.create(name="Audit Shop", owner=owner, is_active=True)
        self.policy = PlatformFeePolicy.objects.create(
            name="Audit policy",
            is_active=True,
            printer_fee_rate=Decimal("0.10"),
            broker_margin_fee_rate=Decimal("0.50"),
            add_platform_fee_on_top=False,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="Buyer",
            customer_email="buyer@audit.test",
            customer_phone="+254700000000",
        )
        option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=Decimal("3000.00"),
            created_by=self.broker,
            status=ProductionOption.SELECTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_option=option,
            created_by=self.broker,
            status=QuoteOfferStatus.SENT,
            total=client_total,
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.split = create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("3000.00"),
            broker_client_price=client_total,
            production_option=option,
            policy=self.policy,
        )
        assert self.split.client_total == client_total, self.split.client_total
        if approve:
            attachment = QuoteRequestAttachment.objects.create(
                quote_request=self.quote_request,
                file=SimpleUploadedFile("art.pdf", b"%PDF art", content_type="application/pdf"),
                name="art.pdf",
            )
            mark_pending(self.quote_request, attachment)
            approve_artwork(self.quote_request, actor=self.broker, attachment_id=attachment.id)

    def build_job(self):
        self.job = ManagedJob.objects.create(
            title="Audit job",
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.buyer,
            broker=self.broker,
            assigned_shop=self.shop,
            created_by=self.buyer,
            status=ManagedJobStatus.AWAITING_PAYMENT,
            payment_status=ManagedJobPaymentStatus.PENDING,
            client_total=self.split.client_total,
        )
        JobFile.objects.create(
            managed_job=self.job,
            uploaded_by=self.buyer,
            file_type=JobFileType.ARTWORK,
            original_filename="art.pdf",
        )
        return self.job


def _pending_mpesa_payment(*, amount="5000.00", checkout_id="ws_CO_PENDING_1", user=None):
    payment = MpesaPayment.objects.create(user=user, phone_number="254712345678", amount=Decimal(amount))
    payment.mark_push_sent(merchant_request_id="mr-1", checkout_request_id=checkout_id)
    return payment


# ─────────────────────────────────────────────────────────────────────────────
# #1 callback authenticity
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(**DARAJA)
class CallbackSecretTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.payment = _pending_mpesa_payment()

    def _assert_unchanged(self):
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, MpesaPaymentStatus.PENDING)
        self.assertEqual(self.payment.mpesa_receipt_number, "")

    @override_settings(MPESA_CALLBACK_SECRET=SECRET)
    def test_forged_callback_without_secret_is_403_and_changes_nothing(self):
        body = stk_body("ws_CO_PENDING_1", amount="5000", receipt="FORGED1")
        for url in (CALLBACK, LEGACY, f"{CALLBACK}wrong-secret/", f"{CALLBACK}?token=wrong", f"{LEGACY}wrong/"):
            with self.subTest(url=url):
                response = self.api.post(url, body, format="json")
                self.assertEqual(response.status_code, 403)
                self._assert_unchanged()
        result_body = {"Result": {"ResultCode": "0", "TransactionID": "FORGED1", "TransactionAmount": 5000}}
        for url in (RESULT, f"{RESULT}nope/"):
            with self.subTest(url=url):
                self.assertEqual(self.api.post(url, result_body, format="json").status_code, 403)
                self._assert_unchanged()

    @override_settings(MPESA_CALLBACK_SECRET=SECRET)
    def test_callback_with_secret_in_path_or_query_is_applied(self):
        response = self.api.post(f"{CALLBACK}{SECRET}/", stk_body("ws_CO_PENDING_1", amount="5000", receipt="RCPT1"), format="json")
        self.assertEqual(response.status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, MpesaPaymentStatus.PAID)

        other = _pending_mpesa_payment(checkout_id="ws_CO_PENDING_2")
        response = self.api.post(f"{CALLBACK}?token={SECRET}", stk_body("ws_CO_PENDING_2", amount="5000", receipt="RCPT2"), format="json")
        self.assertEqual(response.status_code, 200)
        other.refresh_from_db()
        self.assertEqual(other.status, MpesaPaymentStatus.PAID)

    @override_settings(MPESA_CALLBACK_SECRET="", MPESA_CALLBACK_SECRET_REQUIRED=True, MPESA_CALLBACK_ALLOWED_IPS=["127.0.0.1"])
    def test_production_without_configured_secret_fails_closed(self):
        response = self.api.post(CALLBACK, stk_body("ws_CO_PENDING_1", amount="5000", receipt="X1"), format="json")
        self.assertEqual(response.status_code, 403)
        self._assert_unchanged()

    @override_settings(MPESA_CALLBACK_SECRET=SECRET)
    def test_outbound_callback_urls_carry_the_secret(self):
        self.assertEqual(
            signed_callback_url("https://api.printy.ke/api/payments/mpesa/callback/"),
            f"https://api.printy.ke/api/payments/mpesa/callback/{SECRET}/",
        )
        with patch("mpesa_payments.services.get_access_token", return_value="tok"), patch(
            "mpesa_payments.services.requests.post", return_value=daraja_ok("ws_CO_SIGNED")
        ) as post:
            from mpesa_payments.services import initiate_stk_push

            payment = MpesaPayment.objects.create(phone_number="254712345678", amount=Decimal("10"))
            initiate_stk_push(payment)
        self.assertTrue(post.call_args.kwargs["json"]["CallBackURL"].endswith(f"/callback/{SECRET}/"))


@override_settings(**{**DARAJA, "MPESA_CALLBACK_SECRET": SECRET})
class CallbackMatchingTests(_Fixtures, TestCase):
    def setUp(self):
        self.api = APIClient()
        self.url = f"{CALLBACK}?token={SECRET}"

    def test_unknown_checkout_request_id_is_rejected_and_nothing_changes(self):
        pending = _pending_mpesa_payment()
        for url in (self.url, f"{LEGACY}?token={SECRET}"):
            with self.subTest(url=url):
                response = self.api.post(url, stk_body("ws_CO_NOT_OURS", amount="5000", receipt="R9"), format="json")
                self.assertEqual(response.status_code, 404)
        pending.refresh_from_db()
        self.assertEqual(pending.status, MpesaPaymentStatus.PENDING)

    def test_callback_without_checkout_id_or_result_code_is_rejected(self):
        pending = _pending_mpesa_payment()
        no_id = {"Body": {"stkCallback": {"ResultCode": 0}}}
        self.assertEqual(self.api.post(self.url, no_id, format="json").status_code, 400)
        bad_code = {"Body": {"stkCallback": {"CheckoutRequestID": "ws_CO_PENDING_1"}}}
        self.assertEqual(self.api.post(self.url, bad_code, format="json").status_code, 400)
        pending.refresh_from_db()
        self.assertEqual(pending.status, MpesaPaymentStatus.PENDING)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_wrong_amount_goes_to_needs_review_never_paid(self):
        self.build()
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.buyer)
        stk = canonical_initiate_stk_push(payment=payment, phone_number="+254700000000")

        response = self.api.post(self.url, stk_body(stk.checkout_request_id, amount="1", receipt="UNDERPAID1"), format="json")

        self.assertEqual(response.status_code, 200)
        payment.refresh_from_db()
        stk.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_NEEDS_REVIEW)
        self.assertEqual(payment.received_amount, Decimal("1.00"))
        self.assertEqual(stk.status, MpesaSTKRequest.STATUS_NEEDS_REVIEW)
        self.assertFalse(ManagedJob.objects.filter(source_quote=self.quote).exists())
        self.assertEqual(payment.state, "awaiting_confirmation")

        # The cashier module too: KES 1 against a KES 5000 expectation.
        legacy = _pending_mpesa_payment(checkout_id="ws_CO_CASHIER")
        self.api.post(self.url, stk_body("ws_CO_CASHIER", amount="1", receipt="UNDERPAID2"), format="json")
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, MpesaPaymentStatus.NEEDS_REVIEW)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_success_without_receipt_or_amount_is_never_paid(self):
        self.build()
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.buyer)
        stk = canonical_initiate_stk_push(payment=payment, phone_number="+254700000000")

        # No Amount at all: used to default to "expected" and mark PAID.
        self.api.post(self.url, stk_body(stk.checkout_request_id, receipt="NOAMOUNT"), format="json")

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_NEEDS_REVIEW)
        self.assertFalse(ManagedJob.objects.filter(source_quote=self.quote).exists())

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_duplicate_and_late_callbacks_are_idempotent(self):
        self.build()
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.buyer)
        stk = canonical_initiate_stk_push(payment=payment, phone_number="+254700000000")
        good = stk_body(stk.checkout_request_id, amount="5000", receipt="GOOD1")

        first = self.api.post(self.url, good, format="json")
        second = self.api.post(self.url, good, format="json")
        late_failure = self.api.post(self.url, stk_body(stk.checkout_request_id, result_code=1032, desc="Cancelled"), format="json")

        self.assertEqual([first.status_code, second.status_code, late_failure.status_code], [200, 200, 200])
        payment.refresh_from_db()
        stk.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(payment.mpesa_receipt_number, "GOOD1")
        self.assertEqual(stk.status, MpesaSTKRequest.STATUS_SUCCESS)
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_replayed_failure_for_old_prompt_does_not_touch_newer_pending_prompt(self):
        self.build()
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.buyer)
        old = canonical_initiate_stk_push(payment=payment, phone_number="+254700000000")
        self.api.post(self.url, stk_body(old.checkout_request_id, result_code=1037, desc="Timeout"), format="json")
        payment.refresh_from_db()
        new = canonical_initiate_stk_push(payment=payment, phone_number="+254700000000")
        self.assertNotEqual(old.pk, new.pk)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)

        # Daraja (or an attacker with the secret) replays the old failure.
        self.api.post(self.url, stk_body(old.checkout_request_id, result_code=1032, desc="Cancelled"), format="json")

        payment.refresh_from_db()
        new.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)
        self.assertEqual(new.status, MpesaSTKRequest.STATUS_SENT)
        self.assertEqual(old.status, MpesaSTKRequest.STATUS_FAILED)

        # The live prompt still settles normally.
        self.api.post(self.url, stk_body(new.checkout_request_id, amount="5000", receipt="NEWOK"), format="json")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)


@override_settings(**{**DARAJA, "MPESA_CALLBACK_SECRET": SECRET, "MPESA_CALLBACK_ALLOWED_IPS": ["196.201.214.200"]})
class CallbackIpAllowlistTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.payment = _pending_mpesa_payment()
        self.url = f"{CALLBACK}?token={SECRET}"
        self.body = stk_body("ws_CO_PENDING_1", amount="5000", receipt="IPTEST")

    def _status(self, **meta):
        return self.api.post(self.url, self.body, format="json", **meta).status_code

    def test_non_allowlisted_ip_is_403_even_with_the_secret(self):
        self.assertEqual(self._status(REMOTE_ADDR="10.9.9.9"), 403)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, MpesaPaymentStatus.PENDING)

    def test_spoofed_x_forwarded_for_from_untrusted_peer_is_ignored(self):
        self.assertEqual(self._status(REMOTE_ADDR="10.9.9.9", HTTP_X_FORWARDED_FOR="196.201.214.200"), 403)
        # Through our proxy, the attacker's own address is the right-most untrusted hop.
        self.assertEqual(
            self._status(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="196.201.214.200, 6.6.6.6"), 403
        )
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, MpesaPaymentStatus.PENDING)

    def test_allowlisted_ip_via_trusted_proxy_is_accepted(self):
        self.assertEqual(self._status(REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="196.201.214.200"), 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, MpesaPaymentStatus.PAID)

    def test_resolve_client_ip_rules(self):
        request = MagicMock()
        request.META = {"REMOTE_ADDR": "203.0.113.5", "HTTP_X_FORWARDED_FOR": "196.201.214.200"}
        self.assertEqual(resolve_client_ip(request), "203.0.113.5")
        request.META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "1.1.1.1, 196.201.214.200, 127.0.0.1"}
        self.assertEqual(resolve_client_ip(request), "196.201.214.200")
        request.META = {"REMOTE_ADDR": "127.0.0.1", "HTTP_X_FORWARDED_FOR": "not-an-ip"}
        self.assertIsNone(resolve_client_ip(request))

    @override_settings(MPESA_CALLBACK_ALLOWED_IPS=[], MPESA_CALLBACK_SECRET_REQUIRED=True)
    def test_production_defaults_to_safaricom_ranges(self):
        self.assertEqual(self._status(REMOTE_ADDR="10.9.9.9"), 403)
        self.assertEqual(self._status(REMOTE_ADDR="196.201.214.206"), 200)


# ─────────────────────────────────────────────────────────────────────────────
# #2 amount + ownership on /api/payments/mpesa/stk-push/
# ─────────────────────────────────────────────────────────────────────────────


@override_settings(**DARAJA)
class StkPushAmountAndOwnershipTests(_Fixtures, TestCase):
    def setUp(self):
        cache.clear()  # the stk-push throttle is cache-backed
        self.api = APIClient()
        self.build()
        self.build_job()

    @patch("mpesa_payments.services.get_access_token", return_value="tok")
    @patch("mpesa_payments.services.requests.post")
    def test_kes_1_stk_push_on_kes_5000_job_charges_5000(self, post, _token):
        post.return_value = daraja_ok("ws_CO_JOB_1")
        self.api.force_authenticate(self.buyer)

        response = self.api.post(
            STK_PUSH, {"phone_number": "0712345678", "amount": "1.00", "managed_job_id": self.job.id}, format="json"
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(post.call_args.kwargs["json"]["Amount"], 5000)
        payment = MpesaPayment.objects.get()
        self.assertEqual(payment.amount, Decimal("5000.00"))
        self.assertEqual(Decimal(str(response.data["amount"])), Decimal("5000.00"))
        self.assertNotIn("checkout_request_id", response.data)
        self.assertNotIn("merchant_request_id", response.data)

        # And a callback paying KES 1 against it cannot settle the job.
        from mpesa_payments.services import process_callback

        process_callback(stk_body("ws_CO_JOB_1", amount="1", receipt="ONEBOB"))
        payment.refresh_from_db()
        self.job.refresh_from_db()
        self.assertEqual(payment.status, MpesaPaymentStatus.NEEDS_REVIEW)
        self.assertEqual(self.job.payment_status, ManagedJobPaymentStatus.PENDING)

    @patch("mpesa_payments.services.requests.post")
    def test_stk_push_on_another_users_job_is_403(self, post):
        self.api.force_authenticate(self.other)

        response = self.api.post(
            STK_PUSH, {"phone_number": "0712345678", "amount": "5000", "managed_job_id": self.job.id}, format="json"
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(MpesaPayment.objects.exists())
        post.assert_not_called()

    @patch("mpesa_payments.services.requests.post")
    def test_stk_push_respects_the_artwork_gate(self, post):
        JobFile.objects.filter(managed_job=self.job).delete()
        QuoteRequestAttachment.objects.all().delete()
        self.quote_request.refresh_from_db()
        snapshot = dict(self.quote_request.request_snapshot or {})
        snapshot.pop("artwork_review", None)
        self.quote_request.request_snapshot = snapshot
        self.quote_request.save(update_fields=["request_snapshot"])
        self.api.force_authenticate(self.buyer)

        response = self.api.post(STK_PUSH, {"phone_number": "0712345678", "managed_job_id": self.job.id}, format="json")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(response.data.get("artwork_payment_block_reason"))
        self.assertFalse(MpesaPayment.objects.exists())
        post.assert_not_called()

    @patch("mpesa_payments.services.get_access_token", return_value="tok")
    @patch("mpesa_payments.services.requests.post")
    def test_two_consecutive_failed_stk_initiations_do_not_raise_integrity_error(self, post, _token):
        post.return_value = daraja_rejected()
        self.api.force_authenticate(self.buyer)

        first = self.api.post(STK_PUSH, {"phone_number": "0712345678", "amount": "100"}, format="json")
        second = self.api.post(STK_PUSH, {"phone_number": "0712345678", "amount": "100"}, format="json")

        self.assertEqual([first.status_code, second.status_code], [502, 502])
        rows = list(MpesaPayment.objects.order_by("id"))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.status == MpesaPaymentStatus.FAILED for row in rows))
        self.assertTrue(all(row.checkout_request_id is None for row in rows))

    def test_rows_without_checkout_id_coexist(self):
        try:
            MpesaPayment.objects.create(phone_number="254712345678", amount=Decimal("10"))
            MpesaPayment.objects.create(phone_number="254712345678", amount=Decimal("10"))
        except IntegrityError as exc:  # pragma: no cover - this is the regression
            self.fail(f"second never-pushed payment collided: {exc}")
        self.assertEqual(MpesaPayment.objects.filter(checkout_request_id__isnull=True).count(), 2)

    def test_quote_request_payable_is_refused(self):
        self.api.force_authenticate(self.buyer)
        response = self.api.post(
            STK_PUSH, {"phone_number": "0712345678", "amount": "1", "quote_request_id": self.quote_request.id}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MpesaPayment.objects.exists())


@override_settings(**{**DARAJA, "MPESA_CALLBACK_SECRET": SECRET})
class StkPushQuoteSettlesTests(_Fixtures, TestCase):
    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.build()

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_quote_id_payment_settles_the_quote(self):
        self.api.force_authenticate(self.buyer)

        response = self.api.post(
            STK_PUSH, {"phone_number": "0712345678", "amount": "1", "quote_id": self.quote.id}, format="json"
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("checkout_request_id", response.data)
        self.assertFalse(MpesaPayment.objects.exists())  # no orphan "unhandled payable"
        payment = Payment.objects.get(quote=self.quote)
        self.assertEqual(payment.amount, Decimal("5000.00"))
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, QuoteOfferStatus.ACCEPTED)
        stk = payment.mpesa_stk_requests.get()
        self.assertEqual(stk.amount, Decimal("5000.00"))

        callback = self.api.post(
            f"{CALLBACK}?token={SECRET}", stk_body(stk.checkout_request_id, amount="5000", receipt="QUOTEPAID"), format="json"
        )

        self.assertEqual(callback.status_code, 200)
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        job = ManagedJob.objects.get(source_quote=self.quote)
        self.assertEqual(job.client_total, Decimal("5000.00"))
        self.assertEqual(job.payment_status, ManagedJobPaymentStatus.CONFIRMED)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_quote_id_payment_by_a_stranger_is_403(self):
        self.api.force_authenticate(self.other)
        response = self.api.post(STK_PUSH, {"phone_number": "0712345678", "quote_id": self.quote.id}, format="json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Payment.objects.filter(quote=self.quote).exists())
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, QuoteOfferStatus.SENT)


class CallbackSecurityConfigCheckTests(TestCase):
    def test_production_requires_a_strong_secret(self):
        from config.settings import _evaluate_mpesa_callback_security

        ids = lambda msgs: {m.id for m in msgs}  # noqa: E731
        self.assertIn("printy.E017", ids(_evaluate_mpesa_callback_security(required=True, secret="")))
        self.assertIn("printy.E017", ids(_evaluate_mpesa_callback_security(required=True, secret="short")))
        self.assertEqual(ids(_evaluate_mpesa_callback_security(required=True, secret=SECRET)), set())
        self.assertEqual(ids(_evaluate_mpesa_callback_security(required=False, secret="")), set())
        self.assertIn("printy.E018", ids(_evaluate_mpesa_callback_security(required=True, secret=SECRET + "/x?")))
        self.assertIn(
            "printy.E019",
            ids(_evaluate_mpesa_callback_security(required=True, secret=SECRET, allowed_ips=["nope"])),
        )
