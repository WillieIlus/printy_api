"""The buyer dashboard CTA must produce a submitted request, not another draft."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import UserProfile
from quotes.models import CalculatorDraft, QuoteRequest

User = get_user_model()

DRAFTS_URL = "/api/calculator/drafts/"
BUYER_QUOTES_URL = "/api/calculator/buyer-quotes/"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BuyerQuoteRequestFlowTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.client_user = User.objects.create(
            email="buyer-flow@example.test",
            role=User.Role.CLIENT,
            name="Bea Buyer",
        )
        self.manager = User.objects.create(
            email="manager-flow@example.test",
            role=User.Role.BROKER,
            name="Kaskazini Solutions",
        )
        UserProfile.objects.get_or_create(user=self.manager)
        self.api.force_authenticate(user=self.client_user)

    def _create_draft(self):
        response = self.api.post(
            DRAFTS_URL,
            {
                "title": "Business cards · 250 pcs",
                "calculator_inputs_snapshot": {
                    "product_type": "business_card",
                    "quantity": 250,
                    "print_sides": "DUPLEX",
                    "color_mode": "COLOR",
                },
                "pricing_snapshot": {"product_name": "Business cards", "quantity": 250, "total": 5200},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_request_sends_the_draft_and_the_feed_shows_the_request(self):
        draft_payload = self._create_draft()

        sent = self.api.post(
            f"{DRAFTS_URL}{draft_payload['id']}/send/",
            {"selected_manager_id": self.manager.id, "manager_selection_mode": "client_selected"},
            format="json",
        )
        self.assertEqual(sent.status_code, 201, sent.content)
        requests = sent.json()
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertTrue(request["request_reference"].startswith("QR-"))
        self.assertEqual(request["status"], "sent")
        self.assertEqual(request["raw_status"], "submitted")
        self.assertEqual(request["assigned_manager"]["id"], self.manager.id)
        self.assertEqual(request["source_draft_reference"], draft_payload["draft_reference"])

        draft = CalculatorDraft.objects.get(pk=draft_payload["id"])
        self.assertEqual(draft.status, CalculatorDraft.Status.SENT)
        self.assertEqual(QuoteRequest.objects.filter(source_draft=draft).count(), 1)

        feed = self.api.get(BUYER_QUOTES_URL)
        self.assertEqual(feed.status_code, 200, feed.content)
        items = feed.json()
        self.assertEqual(
            [item["item_type"] for item in items],
            ["quote_request"],
            "the sent draft must be replaced by its submitted request",
        )
        self.assertEqual(items[0]["quote_request"]["request_reference"], request["request_reference"])

    def test_request_defaults_to_printy_auto_when_no_manager_is_picked(self):
        draft_payload = self._create_draft()

        sent = self.api.post(f"{DRAFTS_URL}{draft_payload['id']}/send/", {}, format="json")

        self.assertEqual(sent.status_code, 201, sent.content)
        request = sent.json()[0]
        self.assertEqual(request["status"], "sent")
        self.assertEqual(request["raw_status"], "submitted")
        self.assertEqual(request["manager_selection_mode"], QuoteRequest.MANAGER_SELECTION_PRINTY_AUTO)
        self.assertEqual(request["assigned_manager"]["short_title"], "Managed by Printy")

    def test_a_sent_draft_cannot_be_sent_twice(self):
        draft_payload = self._create_draft()
        first = self.api.post(f"{DRAFTS_URL}{draft_payload['id']}/send/", {}, format="json")
        self.assertEqual(first.status_code, 201, first.content)

        second = self.api.post(f"{DRAFTS_URL}{draft_payload['id']}/send/", {}, format="json")

        self.assertEqual(second.status_code, 400, second.content)
        self.assertEqual(QuoteRequest.objects.count(), 1)
