"""Phase 1 — Quote handoff tests.

Covers the full flow: calculator draft create/update → send → QuoteRequest,
anonymous guest-draft claim on registration, and the unified buyer quotes list.
"""
from datetime import timedelta, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from quotes.choices import (
    CalculatorDraftContext,
    CalculatorDraftIntent,
    QuoteStatus,
)
from quotes.models import CalculatorDraft, QuoteRequest
from shops.models import Shop


User = get_user_model()


def _make_client(email="phase1-client@test.ke", password="pass12345"):
    return User.objects.create_user(
        email=email, password=password, role=User.Role.CLIENT
    )


def _make_partner(email="phase1-partner@test.ke"):
    return User.objects.create_user(
        email=email, password="pass12345", role=User.Role.PARTNER,
        capability_overrides={"can_source_jobs": True},
    )


def _make_shop_owner(email="phase1-shop@test.ke"):
    return User.objects.create_user(
        email=email, password="pass12345", role=User.Role.PRODUCTION,
    )


def _make_shop(owner, name="Phase1 Print"):
    return Shop.objects.create(
        name=name, owner=owner, is_active=True,
        opening_time=time(8, 0), closing_time=time(18, 0),
    )


MINIMAL_CALC_PAYLOAD = {
    "calculator_inputs_snapshot": {
        "product_type": "business_card",
        "quantity": 100,
        "size": {"width": 85, "height": 55, "unit": "mm"},
    },
}


# ── Draft create / update / read ──────────────────────────────────────────


class DraftCreateUpdateReadTestCase(TestCase):
    """POST /api/calculator/drafts/ creates; PATCH updates; GET reads."""

    def setUp(self):
        self.client = APIClient()
        self.user = _make_client()
        self.client.force_authenticate(self.user)

    def test_create_draft(self):
        resp = self.client.post("/api/calculator/drafts/", {
            **MINIMAL_CALC_PAYLOAD,
            "title": "My first quote",
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["title"], "My first quote")
        self.assertTrue(data["draft_reference"].startswith("QD-"))
        self.assertEqual(data["status"], "draft")
        self.assertEqual(data["raw_status"], "draft")

    def test_read_draft(self):
        draft = CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        resp = self.client.get(f"/api/calculator/drafts/{draft.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["id"], draft.id)

    def test_update_draft(self):
        draft = CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        resp = self.client.patch(
            f"/api/calculator/drafts/{draft.id}/",
            {"title": "Updated title"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], "Updated title")

    def test_non_client_cannot_create_draft(self):
        partner = _make_partner()
        self.client.force_authenticate(partner)
        resp = self.client.post("/api/calculator/drafts/", MINIMAL_CALC_PAYLOAD, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_cannot_create_draft(self):
        self.client.force_authenticate(None)
        resp = self.client.post("/api/calculator/drafts/", MINIMAL_CALC_PAYLOAD, format="json")
        self.assertEqual(resp.status_code, 401)

    def test_draft_reference_is_unique(self):
        d1 = self.client.post("/api/calculator/drafts/", MINIMAL_CALC_PAYLOAD, format="json").json()
        d2 = self.client.post("/api/calculator/drafts/", MINIMAL_CALC_PAYLOAD, format="json").json()
        self.assertNotEqual(d1["draft_reference"], d2["draft_reference"])


# ── Draft → QuoteRequest conversion ───────────────────────────────────────


class DraftSendTestCase(TestCase):
    """POST /api/calculator/drafts/<id>/send/ converts to QuoteRequest."""

    def setUp(self):
        self.client = APIClient()
        self.user = _make_client()
        self.client.force_authenticate(self.user)
        self.draft = CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
            request_details_snapshot={"customer_name": "Test Buyer"},
        )

    def test_send_creates_quote_request(self):
        resp = self.client.post(
            f"/api/calculator/drafts/{self.draft.id}/send/",
            {"manager_selection_mode": "printy_auto"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertTrue(len(data) >= 1)
        qr_ref = data[0]["request_reference"]
        self.assertTrue(qr_ref.startswith("QR-"))

        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, "sent")

    def test_send_transitions_draft_status(self):
        self.client.post(
            f"/api/calculator/drafts/{self.draft.id}/send/",
            {"manager_selection_mode": "printy_auto"},
            format="json",
        )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, "sent")

    def test_cannot_send_already_sent_draft(self):
        self.draft.status = "sent"
        self.draft.save(update_fields=["status"])
        resp = self.client.post(
            f"/api/calculator/drafts/{self.draft.id}/send/",
            {"manager_selection_mode": "printy_auto"},
            format="json",
        )
        self.assertIn(resp.status_code, (400, 403))

    def test_cannot_send_draft_owned_by_another_user(self):
        other = _make_client(email="other@test.ke")
        self.draft.user = other
        self.draft.save(update_fields=["user_id"])
        resp = self.client.post(
            f"/api/calculator/drafts/{self.draft.id}/send/",
            {"manager_selection_mode": "printy_auto"},
            format="json",
        )
        self.assertIn(resp.status_code, (403, 404))

    def test_quote_request_has_reference(self):
        resp = self.client.post(
            f"/api/calculator/drafts/{self.draft.id}/send/",
            {"manager_selection_mode": "printy_auto"},
            format="json",
        )
        qr_ref = resp.json()[0]["request_reference"]
        self.assertRegex(qr_ref, r"^QR-\d{8}-\d{4}$")


# ── Guest draft → claim ──────────────────────────────────────────────────


class GuestDraftClaimTestCase(TestCase):
    """POST /api/calculator/guest-drafts/ upserts; claim on registration."""

    def setUp(self):
        self.client = APIClient()

    def test_guest_draft_create(self):
        resp = self.client.post("/api/calculator/guest-drafts/", {
            "session_key": "guest-abc-123",
            "calculator_inputs_snapshot": MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        draft = CalculatorDraft.objects.get(guest_session_key="guest-abc-123")
        self.assertIsNone(draft.user_id)
        self.assertEqual(draft.calculator_context, CalculatorDraftContext.PUBLIC_GUEST)

    def test_guest_draft_upsert(self):
        self.client.post("/api/calculator/guest-drafts/", {
            "session_key": "guest-upsert",
            "calculator_inputs_snapshot": MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        }, format="json")
        resp = self.client.post("/api/calculator/guest-drafts/", {
            "session_key": "guest-upsert",
            "calculator_inputs_snapshot": {"product_type": "flyer", "quantity": 200},
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(CalculatorDraft.objects.filter(guest_session_key="guest-upsert").count(), 1)
        self.assertEqual(resp.json()["calculator_inputs_snapshot"]["product_type"], "flyer")

    def test_claim_guest_draft_on_registration(self):
        guest = CalculatorDraft.objects.create(
            guest_session_key="claim-me",
            calculator_context=CalculatorDraftContext.PUBLIC_GUEST,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        resp = self.client.post("/api/auth/register/", {
            "email": "claimed@test.ke",
            "password": "pass12345",
            "name": "Claimed User",
            "role": "client",
            "session_key": "claim-me",
            "guest_draft_id": guest.id,
        }, format="json")
        self.assertEqual(resp.status_code, 201)
        guest.refresh_from_db()
        self.assertIsNotNone(guest.user_id)
        self.assertEqual(guest.guest_session_key, "")
        self.assertEqual(resp.json()["claimed_guest_draft_id"], guest.id)

    def test_claim_guest_draft_via_endpoint(self):
        user = _make_client(email="claimer@test.ke")
        guest = CalculatorDraft.objects.create(
            guest_session_key="claim-endpoint",
            calculator_context=CalculatorDraftContext.PUBLIC_GUEST,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        self.client.force_authenticate(user)
        resp = self.client.post("/api/calculator/drafts/claim/", {
            "session_key": "claim-endpoint",
        }, format="json")
        self.assertEqual(resp.status_code, 200)
        guest.refresh_from_db()
        self.assertEqual(guest.user_id, user.id)
        self.assertEqual(guest.guest_session_key, "")
        self.assertEqual(guest.calculator_context, CalculatorDraftContext.CLIENT_DASHBOARD)


# ── Buyer quotes list ────────────────────────────────────────────────────


class BuyerQuotesListViewTestCase(TestCase):
    """GET /api/calculator/buyer-quotes/ returns drafts + requests combined."""

    def setUp(self):
        self.client = APIClient()
        self.user = _make_client()
        self.client.force_authenticate(self.user)

    def test_empty_list(self):
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_shows_drafts(self):
        CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["item_type"], "draft")
        self.assertIn("draft", resp.json()[0])

    def test_shows_quote_requests(self):
        qr = QuoteRequest.objects.create(
            created_by=self.user,
            customer_name="Buyer",
            status=QuoteStatus.SUBMITTED,
        )
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["item_type"], "quote_request")
        self.assertIn("quote_request", resp.json()[0])
        self.assertEqual(resp.json()[0]["quote_request"]["id"], qr.id)

    def test_shows_both_drafts_and_requests(self):
        CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        QuoteRequest.objects.create(
            created_by=self.user,
            customer_name="Buyer",
            status=QuoteStatus.SUBMITTED,
        )
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(len(resp.json()), 2)
        types = {item["item_type"] for item in resp.json()}
        self.assertEqual(types, {"draft", "quote_request"})

    def test_ordered_newest_first(self):
        # Create an older draft
        old = CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot={"product_type": "flyer", "quantity": 50},
        )
        old_ts = timezone.now() - timedelta(days=30)
        CalculatorDraft.objects.filter(pk=old.pk).update(
            created_at=old_ts, updated_at=old_ts
        )

        # Create a newer request
        new_qr = QuoteRequest.objects.create(
            created_by=self.user,
            customer_name="Buyer",
            status=QuoteStatus.SUBMITTED,
        )
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(len(resp.json()), 2)
        # The QuoteRequest should be first (newer)
        self.assertEqual(resp.json()[0]["item_type"], "quote_request")
        self.assertEqual(resp.json()[0]["quote_request"]["id"], new_qr.id)

    def test_does_not_show_other_users_drafts(self):
        other = _make_client(email="other@test.ke")
        CalculatorDraft.objects.create(
            user=other,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(resp.json(), [])

    def test_does_not_show_other_users_requests(self):
        other = _make_client(email="other@test.ke")
        QuoteRequest.objects.create(
            created_by=other,
            customer_name="Other Buyer",
            status=QuoteStatus.SUBMITTED,
        )
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(resp.json(), [])

    def test_non_client_forbidden(self):
        partner = _make_partner()
        self.client.force_authenticate(partner)
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(resp.status_code, 403)

    def test_sent_draft_with_generated_request_is_omitted(self):
        draft = CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        QuoteRequest.objects.create(
            created_by=self.user,
            source_draft=draft,
            customer_name="Buyer",
            status=QuoteStatus.SUBMITTED,
        )
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(len(resp.json()), 1)
        self.assertEqual(resp.json()[0]["item_type"], "quote_request")
        self.assertEqual(resp.json()[0]["quote_request"]["source_draft_reference"], draft.draft_reference)

    def test_unsent_draft_and_request_both_listed(self):
        draft = CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        QuoteRequest.objects.create(
            created_by=self.user,
            source_draft=draft,
            customer_name="Buyer",
            status=QuoteStatus.SUBMITTED,
        )
        saved = CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(len(resp.json()), 2)
        by_type = {item["item_type"]: item for item in resp.json()}
        self.assertEqual(by_type["draft"]["draft"]["id"], saved.id)
        self.assertEqual(by_type["quote_request"]["quote_request"]["id"], draft.id)

    def test_unauthenticated_forbidden(self):
        self.client.force_authenticate(None)
        resp = self.client.get("/api/calculator/buyer-quotes/")
        self.assertEqual(resp.status_code, 401)

    def test_draft_reference_in_response(self):
        CalculatorDraft.objects.create(
            user=self.user,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
            draft_reference="QD-20260101-0001",
        )
        resp = self.client.get("/api/calculator/buyer-quotes/")
        draft_data = resp.json()[0]["draft"]
        self.assertTrue(draft_data["draft_reference"].startswith("QD-"))
