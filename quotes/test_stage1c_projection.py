"""Stage 1C: one Quote projection over request/quote/draft legacy shapes."""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from quotes.choices import CalculatorDraftStatus, QuoteOfferStatus, QuoteStatus
from quotes.models import CalculatorDraft, Quote, QuoteRequest
from quotes.quote_projection import (
    CANONICAL_STATUSES,
    build_quote_card,
    canonical_status_for_draft,
    canonical_status_for_legacy,
    canonical_status_for_quote,
    canonical_status_for_request,
)
from shops.models import Shop

QUOTE_EXPECTED = {
    "pending": "Draft",
    "modified": "Sent",
    "sent": "Sent",
    "revised": "Sent",
    "accepted": "Accepted",
    "rejected": "Declined",
    "declined": "Declined",
    "expired": "Expired",
}

REQUEST_EXPECTED = {
    "draft": "Draft",
    "submitted": "Sent",
    "viewed": "Sent",
    "awaiting_shop_action": "Sent",
    "awaiting_client_reply": "Sent",
    "quoted": "Sent",
    "accepted": "Accepted",
    "rejected": "Declined",
    "closed": "Declined",
    "cancelled": "Declined",
    "expired": "Expired",
}

DRAFT_EXPECTED = {
    "draft": "Draft",
    "sent": "Sent",
    "archived": "Expired",
}


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class QuoteProjectionBase(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.buyer = User.objects.create_user(
            email="1c-buyer@example.com", password="pass", role=User.Role.CLIENT
        )
        self.other_buyer = User.objects.create_user(
            email="1c-other@example.com", password="pass", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="1c-manager@example.com",
            password="pass",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.printer = User.objects.create_user(
            email="1c-printer@example.com", password="pass", role=User.Role.PRODUCTION
        )
        self.admin = User.objects.create_user(
            email="1c-admin@example.com",
            password="pass",
            role=User.Role.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        self.shop = Shop.objects.create(
            owner=self.printer, name="1C Press", slug="1c-press", is_active=True
        )
        self.request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="1C Buyer",
            customer_phone="0712000111",
            assigned_manager=self.manager,
            status=QuoteStatus.SUBMITTED,
            request_reference="QR-1C-1",
            request_snapshot={
                "calculator_inputs": {
                    "product_type": "business_card",
                    "quantity": 250,
                    "size": "90x55",
                    "paper_stock": "300gsm",
                    "print_sides": "DUPLEX",
                    "color_mode": "COLOR",
                },
                "pricing_snapshot": {"client_total": "1500.00"},
            },
        )
        self.quote = Quote.objects.create(
            quote_request=self.request,
            shop=self.shop,
            created_by=self.manager,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1500.00"),
            sent_at=timezone.now(),
            quote_reference="Q-1C-1",
        )
        self.draft = CalculatorDraft.objects.create(
            user=self.buyer,
            title="1C draft",
            status=CalculatorDraftStatus.DRAFT,
            draft_reference="DRAFT-1C-1",
            calculator_inputs_snapshot={
                "product_type": "flyer",
                "quantity": 100,
                "size": "A5",
                "color_mode": "COLOR",
            },
            pricing_snapshot={"total": "900.00"},
        )

    def get_card(self, *, user, role, legacy_type, pk):
        self.api.force_authenticate(user)
        return self.api.get(
            f"/api/quote-cards/{legacy_type}/{pk}/",
            HTTP_X_PRINTY_ACTIVE_ROLE=role,
        )


class QuoteStatusMappingTests(QuoteProjectionBase):
    def test_all_quote_offer_statuses_map_to_one_of_five(self):
        self.assertEqual(set(QUOTE_EXPECTED), set(QuoteOfferStatus.values))
        for raw, expected in QUOTE_EXPECTED.items():
            with self.subTest(status=raw):
                self.assertEqual(canonical_status_for_legacy("quote", raw), expected)
                self.assertIn(expected, CANONICAL_STATUSES)

    def test_all_request_statuses_map_to_one_of_five(self):
        self.assertEqual(set(REQUEST_EXPECTED), set(QuoteStatus.values))
        for raw, expected in REQUEST_EXPECTED.items():
            with self.subTest(status=raw):
                self.assertEqual(canonical_status_for_legacy("request", raw), expected)
                self.assertIn(expected, CANONICAL_STATUSES)

    def test_all_draft_statuses_map_to_one_of_five(self):
        self.assertEqual(set(DRAFT_EXPECTED), set(CalculatorDraftStatus.values))
        for raw, expected in DRAFT_EXPECTED.items():
            with self.subTest(status=raw):
                self.assertEqual(canonical_status_for_legacy("draft", raw), expected)
                self.assertIn(expected, CANONICAL_STATUSES)

    def test_model_status_helpers(self):
        for raw, expected in QUOTE_EXPECTED.items():
            self.quote.status = raw
            with self.subTest(quote=raw):
                self.assertEqual(canonical_status_for_quote(self.quote), expected)
        for raw, expected in REQUEST_EXPECTED.items():
            self.request.status = raw
            with self.subTest(request=raw):
                self.assertEqual(canonical_status_for_request(self.request), expected)
        for raw, expected in DRAFT_EXPECTED.items():
            self.draft.status = raw
            with self.subTest(draft=raw):
                self.assertEqual(canonical_status_for_draft(self.draft), expected)

    def test_superseded_quote_is_expired(self):
        self.quote.client_quote_status = "superseded"
        self.assertEqual(canonical_status_for_quote(self.quote), "Expired")


class LegacyShapeTests(QuoteProjectionBase):
    def test_quote_shape_projects_spec_price_from_to_status_and_action(self):
        card = build_quote_card(self.quote, viewer_role="buyer")
        item = card["quotes"][0]

        self.assertEqual(card["id"], f"quote-card-quote-{self.quote.id}")
        self.assertEqual(item["legacy_type"], "quote")
        self.assertEqual(item["legacy_id"], self.quote.id)
        self.assertTrue(item["spec"]["summary"])
        self.assertEqual(item["price"]["amount"], "1500.00")
        self.assertEqual(item["from"]["role"], "manager")
        self.assertEqual(item["to"]["role"], "buyer")
        self.assertEqual(item["status"], "Sent")
        self.assertEqual(item["next_action"]["code"], "review_quote")

    def test_request_shape_projects_spec_price_from_to_status_and_action(self):
        card = build_quote_card(self.request, viewer_role="buyer")
        item = card["quotes"][0]

        self.assertEqual(item["legacy_type"], "request")
        self.assertTrue(item["spec"]["summary"])
        self.assertEqual(item["price"]["amount"], "1500.00")
        self.assertEqual(item["from"]["role"], "buyer")
        self.assertEqual(item["to"]["role"], "manager")
        self.assertIn(item["status"], CANONICAL_STATUSES)

    def test_draft_shape_projects_spec_price_from_to_status_and_action(self):
        card = build_quote_card(self.draft, viewer_role="buyer")
        item = card["quotes"][0]

        self.assertEqual(item["legacy_type"], "draft")
        self.assertTrue(item["spec"]["summary"])
        self.assertEqual(item["price"]["amount"], "900.00")
        self.assertEqual(item["from"]["role"], "buyer")
        self.assertEqual(item["to"]["role"], "none")
        self.assertEqual(item["status"], "Draft")
        self.assertEqual(item["next_action"]["code"], "continue_quote")

    def test_endpoint_serves_all_three_legacy_shapes(self):
        quote = self.get_card(user=self.buyer, role="client", legacy_type="quote", pk=self.quote.id)
        request = self.get_card(user=self.buyer, role="client", legacy_type="request", pk=self.request.id)
        draft = self.get_card(user=self.buyer, role="client", legacy_type="draft", pk=self.draft.id)
        self.assertEqual(quote.status_code, 200, quote.content)
        self.assertEqual(request.status_code, 200, request.content)
        self.assertEqual(draft.status_code, 200, draft.content)
        self.assertEqual(quote.json()["quotes"][0]["status"], "Sent")
        self.assertEqual(request.json()["quotes"][0]["status"], "Sent")
        self.assertEqual(draft.json()["quotes"][0]["status"], "Draft")

    def test_alias_offer_and_response_resolve_to_quote(self):
        for alias in ("offer", "response"):
            response = self.get_card(user=self.manager, role="partner", legacy_type=alias, pk=self.quote.id)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["quotes"][0]["legacy_type"], "quote")


class QuoteNextActionTests(QuoteProjectionBase):
    def _action(self, *, quote_status, viewer_role):
        self.quote.status = quote_status
        item = build_quote_card(self.quote, viewer_role=viewer_role)["quotes"][0]
        return item["next_action"]

    def test_role_status_action_matrix(self):
        cases = [
            ("pending", "buyer", "continue_quote"),
            ("pending", "manager", "prepare_quote"),
            ("sent", "buyer", "review_quote"),
            ("sent", "manager", "none"),
            ("accepted", "buyer", "pay_quote"),
            ("accepted", "manager", "none"),
            ("declined", "buyer", "start_quote"),
            ("declined", "manager", "revise_quote"),
            ("expired", "buyer", "start_quote"),
            ("expired", "manager", "revise_quote"),
            ("sent", "printer", "none"),
            ("sent", "admin", "none"),
        ]
        for status, role, expected in cases:
            with self.subTest(status=status, role=role):
                self.assertEqual(self._action(quote_status=status, viewer_role=role)["code"], expected)


class QuotePrivacyTests(QuoteProjectionBase):
    def test_buyer_never_sees_shop_or_internal_status(self):
        item = build_quote_card(self.quote, viewer_role="buyer")["quotes"][0]
        self.assertNotIn("shop", item)
        self.assertNotIn("client", item)
        self.assertNotIn("internal_status", item)
        self.assertEqual(item["from"]["role"], "manager")
        self.assertEqual(item["price"]["label"], "Client total")
        raw = str(item)
        self.assertNotIn(self.shop.name, raw)

    def test_manager_sees_shop_and_client(self):
        item = build_quote_card(self.quote, viewer_role="manager")["quotes"][0]
        self.assertEqual(item["shop"]["name"], self.shop.name)
        self.assertEqual(item["client"]["name"], "1C Buyer")
        self.assertNotIn("internal_status", item)

    def test_printer_sees_no_client_or_client_total(self):
        item = build_quote_card(self.quote, viewer_role="printer")["quotes"][0]
        self.assertNotIn("client", item)
        self.assertNotIn("internal_status", item)
        self.assertEqual(item["shop"]["name"], self.shop.name)
        self.assertIsNone(item["price"])
        raw = str(item)
        self.assertNotIn("1500", raw)
        self.assertNotIn("1C Buyer", raw)

    def test_admin_sees_internal_status(self):
        item = build_quote_card(self.quote, viewer_role="admin")["quotes"][0]
        self.assertEqual(item["internal_status"], self.quote.status)
        self.assertIn("shop", item)
        self.assertIn("client", item)

    def test_endpoint_denies_unrelated_buyer(self):
        response = self.get_card(
            user=self.other_buyer, role="client", legacy_type="quote", pk=self.quote.id
        )
        self.assertEqual(response.status_code, 403)

    def test_endpoint_denies_a_role_the_account_does_not_hold(self):
        response = self.get_card(
            user=self.buyer, role="production", legacy_type="quote", pk=self.quote.id
        )
        self.assertEqual(response.status_code, 403)
