"""Audit #7: client portal and OTP.

Each test failed on the code before audit #7:

* the link artwork upload returned 202 and threw the file away;
* the link acknowledgement wrote unused ``request_snapshot`` keys, so
  ``price_updated``/``date_updated`` stayed set and payment stayed blocked;
* the link payment ignored the artwork gate (it accepted the quote and opened
  a payment for unapproved artwork);
* a verified OTP token never expired;
* the messaging stub logged the plaintext OTP, stored it in the delivery row
  and recorded unsent messages as SENT, in every environment.
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services.phone import normalize_phone
from clients.models import ClientOtp, MessageDelivery
from clients.services import links as link_service
from clients.services.messaging import send_client_message
from clients.services.otp import request_otp, validate_action_token, verify_otp
from payments.models import Payment
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.artwork_review import PAYMENT_BLOCK_AWAITING_MANAGER, approve_artwork, mark_manager_holds
from quotes.models import Quote, QuoteRequest, QuoteRequestAttachment
from shops.models import Shop

PHONE = "254711222333"
PDF = b"%PDF-1.4\n%client link artwork\n"


def link_url(token, suffix=""):
    return f"/api/client-portal/links/{token}/{suffix}"


class _LinkBase(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        media = tempfile.mkdtemp(prefix="printy-audit7-")
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)

        self.manager = User.objects.create_user(
            email="a7-manager@test.com", password="pw12345678", name="A7 Manager", role=User.Role.BROKER
        )
        self.shop = Shop.objects.create(owner=self.manager, name="A7 Shop", slug="a7-shop")
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.manager,
            assigned_manager=self.manager,
            created_by_manager=self.manager,
            customer_name="Client A7",
            customer_phone=PHONE,
            status=QuoteRequest.QUOTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.manager,
            total=Decimal("1600.00"),
            status=Quote.SENT,
        )
        create_quote_financial_split(
            quote=self.quote, production_cost=Decimal("1000.00"), broker_client_price=Decimal("1600.00")
        )
        self.link = link_service.create_client_link(quote_request=self.quote_request, manager=self.manager)
        self.otp_token = self._verified_token()

    def _verified_token(self) -> str:
        phone = normalize_phone(PHONE)
        _challenge, code = request_otp(phone_normalized=phone, quote_request=self.quote_request)
        return verify_otp(phone_normalized=phone, code=code, quote_request=self.quote_request).verified_token

    def post(self, suffix, data=None, fmt="json"):
        return APIClient().post(
            link_url(self.link.token, suffix), data or {}, format=fmt, HTTP_X_PRINTY_OTP=self.otp_token
        )


# ---------------------------------------------------------------------------
# Artwork upload is persisted
# ---------------------------------------------------------------------------


class ClientLinkArtworkTests(_LinkBase):
    def test_upload_is_saved_and_goes_to_manager_review(self):
        response = self.post("artwork/", {"file": SimpleUploadedFile("brief.pdf", PDF)}, fmt="multipart")
        self.assertEqual(response.status_code, 202, response.content)
        attachment = QuoteRequestAttachment.objects.get(pk=response.data["attachment_id"])
        self.assertEqual(attachment.quote_request_id, self.quote_request.id)
        self.assertEqual(attachment.name, "brief.pdf")
        with attachment.file.open("rb") as handle:
            self.assertEqual(handle.read(), PDF)
        self.quote_request.refresh_from_db()
        from quotes.artwork_review import get_review

        review = get_review(self.quote_request)
        self.assertEqual(review["state"], "pending")
        self.assertEqual(review["file_id"], attachment.id)

    def test_missing_file_is_a_400_not_a_202(self):
        response = self.post("artwork/", {"filename": "brief.pdf"})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(QuoteRequestAttachment.objects.exists())

    def test_renamed_file_is_rejected_by_the_upload_service(self):
        response = self.post(
            "artwork/", {"file": SimpleUploadedFile("brief.pdf", b"<html>not a pdf</html>")}, fmt="multipart"
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(QuoteRequestAttachment.objects.exists())

    def test_save_failure_returns_an_error_not_202(self):
        with mock.patch.object(QuoteRequestAttachment.objects, "create", side_effect=OSError("disk full")):
            response = self.post("artwork/", {"file": SimpleUploadedFile("brief.pdf", PDF)}, fmt="multipart")
        self.assertEqual(response.status_code, 500, response.content)
        self.assertNotEqual(response.status_code, 202)
        self.assertFalse(QuoteRequestAttachment.objects.exists())

    def test_closed_request_refuses_new_artwork(self):
        QuoteRequest.objects.filter(pk=self.quote_request.pk).update(status=QuoteRequest.CLOSED)
        response = self.post("artwork/", {"file": SimpleUploadedFile("brief.pdf", PDF)}, fmt="multipart")
        self.assertEqual(response.status_code, 409, response.content)


# ---------------------------------------------------------------------------
# Acknowledgement uses the canonical services and unblocks payment
# ---------------------------------------------------------------------------


class ClientLinkAcknowledgeTests(_LinkBase):
    def setUp(self):
        super().setUp()
        self.new_due = timezone.now() + timedelta(days=9)
        QuoteRequest.objects.filter(pk=self.quote_request.pk).update(
            price_updated=True,
            price_updated_old_total=Decimal("1500.00"),
            price_updated_new_total=Decimal("1600.00"),
            date_updated=True,
            date_updated_new=self.new_due,
        )

    def test_acknowledging_both_clears_the_flags(self):
        response = self.post("acknowledge/", {"acknowledge": "both"})
        self.assertEqual(response.status_code, 200, response.content)
        self.quote_request.refresh_from_db()
        self.assertFalse(self.quote_request.price_updated)
        self.assertFalse(self.quote_request.date_updated)
        self.assertIsNotNone(self.quote_request.price_acknowledged_at)
        self.assertIsNotNone(self.quote_request.date_acknowledged_at)
        self.assertEqual(self.quote_request.due_date, self.new_due)
        self.assertEqual(response.data["acknowledged_changes"], ["price", "date"])
        self.assertFalse(response.data["price_updated"])

    def test_price_only_leaves_the_date_pending(self):
        self.post("acknowledge/", {"acknowledge": "price"})
        self.quote_request.refresh_from_db()
        self.assertFalse(self.quote_request.price_updated)
        self.assertTrue(self.quote_request.date_updated)

    def test_acknowledgement_unblocks_payment(self):
        mark_manager_holds(self.quote_request, actor=self.manager)
        with mock.patch("payments.services.initiate_stk_push", return_value=None):
            blocked = self.post("payment/")
            self.assertEqual(blocked.status_code, 400, blocked.content)
            self.assertIn("price", blocked.data["detail"].lower())

            self.post("acknowledge/", {"acknowledge": "both"})
            paid = self.post("payment/")
        self.assertEqual(paid.status_code, 201, paid.content)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, Quote.ACCEPTED)


# ---------------------------------------------------------------------------
# Payment honours the artwork gate before accepting anything
# ---------------------------------------------------------------------------


class ClientLinkPaymentGateTests(_LinkBase):
    def test_unreviewed_artwork_blocks_payment_before_acceptance(self):
        self.post("artwork/", {"file": SimpleUploadedFile("brief.pdf", PDF)}, fmt="multipart")
        with mock.patch("payments.services.initiate_stk_push") as push:
            response = self.post("payment/")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.data["artwork_payment_block_reason"], PAYMENT_BLOCK_AWAITING_MANAGER)
        push.assert_not_called()
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, Quote.SENT)
        self.assertFalse(Payment.objects.filter(quote=self.quote).exists())

    def test_no_artwork_at_all_blocks_payment(self):
        response = self.post("payment/")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("artwork_payment_block_reason", response.data)
        self.assertFalse(Payment.objects.filter(quote=self.quote).exists())

    def test_approved_artwork_lets_payment_start(self):
        self.post("artwork/", {"file": SimpleUploadedFile("brief.pdf", PDF)}, fmt="multipart")
        approve_artwork(self.quote_request, actor=self.manager)
        with mock.patch("payments.services.initiate_stk_push", return_value=None):
            response = self.post("payment/")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(response.data["accepted_now"])


# ---------------------------------------------------------------------------
# validate_action_token enforces expires_at
# ---------------------------------------------------------------------------


class ActionTokenExpiryTests(_LinkBase):
    def test_live_token_is_valid(self):
        self.assertTrue(
            validate_action_token(self.otp_token, quote_request=self.quote_request, phone_normalized=PHONE)
        )

    def test_expired_token_is_refused_by_the_service(self):
        ClientOtp.objects.filter(verified_token=self.otp_token).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(
            validate_action_token(self.otp_token, quote_request=self.quote_request, phone_normalized=PHONE)
        )

    def test_expired_token_cannot_perform_actions(self):
        ClientOtp.objects.filter(verified_token=self.otp_token).update(expires_at=timezone.now() - timedelta(seconds=1))
        for suffix, data, fmt in (
            ("artwork/", {"file": SimpleUploadedFile("brief.pdf", PDF)}, "multipart"),
            ("acknowledge/", {"acknowledge": "both"}, "json"),
            ("payment/", {}, "json"),
        ):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.post(suffix, data, fmt).status_code, 403)


# ---------------------------------------------------------------------------
# Messaging stub: no plaintext OTP, never SENT, fails outside dev/test
# ---------------------------------------------------------------------------


@override_settings(CLIENT_OTP_ECHO=True, OTP_RESEND_COOLDOWN_SECONDS=0)
class StubMessagingTests(_LinkBase):
    def _request_code(self):
        return APIClient().post(link_url(self.link.token, "otp/request/"), {}, format="json")

    def test_otp_is_not_logged_or_stored_in_plaintext(self):
        with self.assertLogs("clients.services.messaging", level="DEBUG") as logs:
            response = self._request_code()
        self.assertEqual(response.status_code, 200, response.content)
        code = response.data["code"]
        self.assertNotIn(code, "\n".join(logs.output))
        self.assertNotIn(PHONE, "\n".join(logs.output))
        delivery = MessageDelivery.objects.filter(quote_request=self.quote_request).latest("id")
        self.assertNotIn(code, delivery.body)
        self.assertIn("verification code is ******", delivery.body)

    def test_stub_delivery_is_queued_not_sent(self):
        delivery = send_client_message(recipient=PHONE, message="hello", quote_request=self.quote_request)
        self.assertEqual(delivery.status, MessageDelivery.Status.QUEUED)
        self.assertIsNone(delivery.sent_at)
        self.assertNotEqual(delivery.status, MessageDelivery.Status.SENT)

    @override_settings(DEBUG=False, APP_ENV="production")
    def test_outside_dev_and_test_the_stub_fails_loudly(self):
        with self.assertLogs("clients.services.messaging", level="ERROR"):
            delivery = send_client_message(recipient=PHONE, message="hello", quote_request=self.quote_request)
        self.assertEqual(delivery.status, MessageDelivery.Status.FAILED)
        self.assertIn("No SMS", delivery.error)

    @override_settings(DEBUG=False, APP_ENV="production")
    def test_otp_request_reports_503_when_the_code_was_not_sent(self):
        with self.assertLogs("clients.services.messaging", level="ERROR"):
            response = self._request_code()
        self.assertEqual(response.status_code, 503, response.content)
        self.assertNotIn("code", response.data)
