from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.choices import QuoteOfferStatus
from quotes.models import ProductionOption, Quote
from shops.models import Shop
from quotes.artwork_review import (
    PAYMENT_BLOCK_AWAITING_MANAGER,
    PAYMENT_BLOCK_CHANGES,
    PAYMENT_BLOCK_UPLOAD,
    approve_artwork,
    is_approved,
    mark_pending,
    payment_block_reason,
    request_changes,
)
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest, QuoteRequestAttachment


class QuoteArtworkReviewGateTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.buyer = User.objects.create_user(
            email="review-buyer@example.com", password="pass", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="review-manager@example.com", password="pass", role=User.Role.PARTNER
        )
        self.other_manager = User.objects.create_user(
            email="review-other@example.com", password="pass", role=User.Role.PARTNER
        )
        self.request = QuoteRequest.objects.create(
            created_by=self.buyer,
            assigned_manager=self.manager,
            customer_name="Review Buyer",
            status=QuoteStatus.SUBMITTED,
            request_snapshot={},
        )

    def upload(self, name="artwork.pdf"):
        attachment = QuoteRequestAttachment.objects.create(
            quote_request=self.request,
            file=SimpleUploadedFile(name, b"pdf", content_type="application/pdf"),
            name=name,
        )
        mark_pending(self.request, attachment)
        return attachment

    def test_missing_artwork_blocks_payment(self):
        self.assertEqual(payment_block_reason(self.request), PAYMENT_BLOCK_UPLOAD)
        self.assertFalse(is_approved(self.request))

    def test_uploaded_artwork_needs_manager_approval(self):
        self.upload()
        self.assertEqual(payment_block_reason(self.request), PAYMENT_BLOCK_AWAITING_MANAGER)
        self.assertFalse(is_approved(self.request))

    def test_manager_approval_unlocks_the_current_file(self):
        attachment = self.upload()
        approve_artwork(self.request, actor=self.manager, attachment_id=attachment.id)
        self.assertIsNone(payment_block_reason(self.request))
        self.assertTrue(is_approved(self.request))

    def test_replacing_approved_artwork_resets_approval(self):
        attachment = self.upload()
        approve_artwork(self.request, actor=self.manager, attachment_id=attachment.id)
        replacement = self.upload("artwork-v2.pdf")
        self.assertEqual(payment_block_reason(self.request), PAYMENT_BLOCK_AWAITING_MANAGER)
        self.assertFalse(is_approved(self.request))
        self.assertNotEqual(attachment.id, replacement.id)

    def test_manager_can_request_changes(self):
        self.upload()
        request_changes(self.request, actor=self.manager, note="Fix the bleed.")
        self.assertEqual(payment_block_reason(self.request), PAYMENT_BLOCK_CHANGES)
        self.assertFalse(is_approved(self.request))

    def test_manager_api_approves_and_other_manager_is_denied(self):
        attachment = self.upload()
        url = f"/api/quote-requests/{self.request.id}/artwork/"

        self.api.force_authenticate(self.other_manager)
        denied = self.api.post(url, {"action": "approve"}, format="json")
        self.assertEqual(denied.status_code, 403, denied.content)

        self.api.force_authenticate(self.manager)
        allowed = self.api.post(
            url,
            {"action": "approve", "attachment_id": attachment.id},
            format="json",
        )
        self.assertEqual(allowed.status_code, 200, allowed.content)
        self.assertTrue(allowed.json()["approved"])
        self.assertIsNone(allowed.json()["payment_block_reason"])

    def test_manager_can_confirm_they_hold_artwork_for_a_phone_client(self):
        url = f"/api/quote-requests/{self.request.id}/artwork/"
        self.api.force_authenticate(self.manager)
        response = self.api.post(
            url,
            {"action": "manager_holds", "note": "File is at the shop."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["approved"])
        self.assertIsNone(payload["payment_block_reason"])
        self.request.refresh_from_db()
        self.assertIsNone(payment_block_reason(self.request))
        self.assertTrue(is_approved(self.request))

    def test_with_manager_claim_needs_manager_acceptance(self):
        snapshot = dict(self.request.request_snapshot or {})
        snapshot["artwork_claim"] = {"state": "with_manager", "message": "With you."}
        self.request.request_snapshot = snapshot
        self.request.save(update_fields=["request_snapshot", "updated_at"])
        mark_pending(self.request, None)

        self.assertEqual(payment_block_reason(self.request), PAYMENT_BLOCK_AWAITING_MANAGER)
        self.assertFalse(is_approved(self.request))
        approve_artwork(self.request, actor=self.manager)
        self.assertIsNone(payment_block_reason(self.request))
        self.assertTrue(is_approved(self.request))


class QuotePaymentArtworkGateTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.buyer = User.objects.create_user(
            email="pay-review-buyer@example.com", password="pass", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="pay-review-manager@example.com", password="pass", role=User.Role.PARTNER
        )
        self.shop_owner = User.objects.create_user(
            email="pay-review-shop@example.com", password="pass", role=User.Role.PRODUCTION
        )
        self.shop = Shop.objects.create(name="Review Payment Shop", owner=self.shop_owner, is_active=True)
        self.policy = PlatformFeePolicy.objects.create(
            name="Review payment policy",
            is_active=True,
            printer_fee_rate=Decimal("0.10"),
            broker_margin_fee_rate=Decimal("0.50"),
            add_platform_fee_on_top=False,
        )
        self.request = QuoteRequest.objects.create(
            created_by=self.buyer,
            assigned_manager=self.manager,
            customer_name="Review Buyer",
            customer_email=self.buyer.email,
            customer_phone="+254700000000",
            status=QuoteStatus.QUOTED,
        )
        option = ProductionOption.objects.create(
            quote_request=self.request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=self.manager,
            status=ProductionOption.SELECTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.request,
            shop=self.shop,
            production_option=option,
            created_by=self.manager,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1750.00"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )
        create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1750.00"),
            production_option=option,
            policy=self.policy,
        )

    def _pay(self):
        self.api.force_authenticate(self.buyer)
        return self.api.post(
            "/api/payments/stk-push/",
            {"quote_id": self.quote.id, "phone_number": "+254700000000"},
            format="json",
        )

    def _upload_and_approve(self):
        attachment = QuoteRequestAttachment.objects.create(
            quote_request=self.request,
            file=SimpleUploadedFile("artwork.pdf", b"pdf", content_type="application/pdf"),
            name="artwork.pdf",
        )
        mark_pending(self.request, attachment)
        approve_artwork(self.request, actor=self.manager)

    def test_quote_payment_is_blocked_before_artwork_approval(self):
        response = self._pay()
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["detail"], PAYMENT_BLOCK_UPLOAD)

    def test_quote_payment_passes_the_artwork_gate_after_approval(self):
        self._upload_and_approve()
        response = self._pay()
        body = response.json()
        self.assertNotEqual(body.get("detail"), PAYMENT_BLOCK_UPLOAD)
        self.assertNotEqual(body.get("detail"), PAYMENT_BLOCK_AWAITING_MANAGER)
