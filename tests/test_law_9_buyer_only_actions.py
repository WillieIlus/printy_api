"""Law 9.2-9.5 — buyer-only actions, active role and guest claims.

Written test-first (prompt item 1). Before the enforcement was added, a manager
or printer account could reach several of these endpoints (notably the public
calculator guest-draft endpoints and the customer quote-request create); the
tests below assert the law-9 behaviour and therefore failed pre-fix.

Buyer-only endpoints covered (law 9.2 / prompt item 1):
  1. POST /api/quote-requests/                          quote request create
  2. POST /api/calculator/guest-drafts/                 calculator guest draft
  3. POST /api/calculator/drafts/                       buyer draft create
  4. POST /api/calculator/drafts/<pk>/send/             draft send
  5. POST /api/calculator/drafts/<pk>/direct-shop/submit/  direct-shop draft submit
  6. POST /api/calculator/drafts/claim/                 draft claim
  7. POST /api/intake/submit/                           guest manager selection claim
  8. POST /api/client-calculator/drafts/                calculator guest draft (react)

Guest flows (2 and 8) stay open to anonymous visitors; authenticated managers
and printers are refused (law 9.2 "a manager or printer calling any of these
gets a clear 403").
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from shops.models import Shop

from accounts.models import User


MINIMAL_CALC_PAYLOAD = {
    "calculator_inputs_snapshot": {
        "product_type": "business_card",
        "quantity": 100,
        "size": {"width": 85, "height": 55, "unit": "mm"},
    },
}

CLIENT_CALC_DRAFT_PAYLOAD = {
    "product_type": "business_card",
    "quantity": 100,
    "width_mm": "85",
    "height_mm": "55",
}


class _BuyerOnlyBase(TestCase):
    def setUp(self):
        self.buyer = User.objects.create_user(
            email="law9-buyer@test.ke", password="pass12345", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="law9-manager@test.ke",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.printer = User.objects.create_user(
            email="law9-printer@test.ke",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.printer,
            name="Law 9 Press",
            slug="law9-press",
            is_active=True,
            is_public=True,
            city="Nairobi",
        )
        # Round out the printer's production role (owner of an active shop).
        self._configure_shop_pricing(self.shop)

    def _configure_shop_pricing(self, shop):
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Law 9 policy",
            is_active=True,
            printer_fee_rate=Decimal("0.05"),
            broker_margin_fee_rate=Decimal("0.15"),
            add_platform_fee_on_top=False,
        )
        machine = Machine.objects.create(
            shop=shop, name="Law 9 Press", max_width_mm=320, max_height_mm=450, is_active=True
        )
        paper = Paper.objects.create(
            shop=shop,
            name="SRA3 Card",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("1.00"),
            selling_price=Decimal("20.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
            is_default=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
            is_default=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting",
            slug="cutting-law9",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )

    def _client(self, user=None, **extra):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def _make_buyer_draft(self, user=None):
        from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent
        from quotes.models import CalculatorDraft

        return CalculatorDraft.objects.create(
            user=user or self.buyer,
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )


class BuyerOnlyEndpointEnforcementTests(_BuyerOnlyBase):
    """Manager and printer get 403 on every buyer-only endpoint."""

    def _quote_request_payload(self):
        return {
            "shop": self.shop.id,
            "customer_name": "Buyer",
            "delivery_preference": "pickup",
        }

    def test_manager_gets_403_on_quote_request_create(self):
        resp = self._client(self.manager).post(
            "/api/quote-requests/", self._quote_request_payload(), format="json"
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_printer_gets_403_on_quote_request_create(self):
        resp = self._client(self.printer).post(
            "/api/quote-requests/", self._quote_request_payload(), format="json"
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_buyer_can_create_quote_request(self):
        resp = self._client(self.buyer).post(
            "/api/quote-requests/", self._quote_request_payload(), format="json"
        )
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_manager_gets_403_on_draft_create(self):
        resp = self._client(self.manager).post(
            "/api/calculator/drafts/", MINIMAL_CALC_PAYLOAD, format="json"
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_printer_gets_403_on_draft_create(self):
        resp = self._client(self.printer).post(
            "/api/calculator/drafts/", MINIMAL_CALC_PAYLOAD, format="json"
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_buyer_can_create_draft(self):
        resp = self._client(self.buyer).post(
            "/api/calculator/drafts/", MINIMAL_CALC_PAYLOAD, format="json"
        )
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_manager_gets_403_on_draft_send(self):
        draft = self._make_buyer_draft()
        resp = self._client(self.manager).post(
            f"/api/calculator/drafts/{draft.id}/send/",
            {"manager_selection_mode": "printy_auto"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_printer_gets_403_on_draft_send(self):
        draft = self._make_buyer_draft()
        resp = self._client(self.printer).post(
            f"/api/calculator/drafts/{draft.id}/send/",
            {"manager_selection_mode": "printy_auto"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_manager_gets_403_on_direct_shop_submit(self):
        draft = self._make_buyer_draft()
        resp = self._client(self.manager).post(
            f"/api/calculator/drafts/{draft.id}/direct-shop/submit/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_printer_gets_403_on_direct_shop_submit(self):
        draft = self._make_buyer_draft()
        resp = self._client(self.printer).post(
            f"/api/calculator/drafts/{draft.id}/direct-shop/submit/", {}, format="json"
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_manager_gets_403_on_guest_draft_claim(self):
        resp = self._client(self.manager).post(
            "/api/calculator/drafts/claim/", {"session_key": "law9-claim"}, format="json"
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_printer_gets_403_on_guest_draft_claim(self):
        resp = self._client(self.printer).post(
            "/api/calculator/drafts/claim/", {"session_key": "law9-claim"}, format="json"
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_manager_gets_403_on_guest_manager_selection_claim(self):
        draft = self._make_buyer_draft(user=self.manager)
        resp = self._client(self.manager).post(
            "/api/intake/submit/",
            {"draft_id": draft.id, "manager_selection_mode": "printy_auto"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_printer_gets_403_on_guest_manager_selection_claim(self):
        resp = self._client(self.printer).post(
            "/api/intake/submit/",
            {"manager_selection_mode": "printy_auto"},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_manager_gets_403_on_guest_draft_upsert(self):
        resp = self._client(self.manager).post(
            "/api/calculator/guest-drafts/",
            {"session_key": "law9-guest", **MINIMAL_CALC_PAYLOAD},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_printer_gets_403_on_guest_draft_upsert(self):
        resp = self._client(self.printer).post(
            "/api/calculator/guest-drafts/",
            {"session_key": "law9-guest", **MINIMAL_CALC_PAYLOAD},
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_manager_gets_403_on_client_calculator_draft(self):
        resp = self._client(self.manager).post(
            "/api/client-calculator/drafts/",
            CLIENT_CALC_DRAFT_PAYLOAD,
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_printer_gets_403_on_client_calculator_draft(self):
        resp = self._client(self.printer).post(
            "/api/client-calculator/drafts/",
            CLIENT_CALC_DRAFT_PAYLOAD,
            format="json",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_anonymous_guest_can_still_create_guest_draft(self):
        resp = self._client().post(
            "/api/calculator/guest-drafts/",
            {"session_key": "law9-anon", **MINIMAL_CALC_PAYLOAD},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_anonymous_guest_can_still_create_calculator_draft(self):
        resp = self._client().post(
            "/api/client-calculator/drafts/",
            {**CLIENT_CALC_DRAFT_PAYLOAD, "customer_email": "guest-law9@test.ke"},
            format="json",
        )
        self.assertIn(resp.status_code, (201, 422), resp.content)


class ActiveRoleValidationTests(_BuyerOnlyBase):
    """Law 9.3 — the active role decides, and unheld roles are rejected."""

    def test_buyer_sending_unheld_role_is_rejected(self):
        resp = self._client(self.buyer).post(
            "/api/calculator/drafts/",
            MINIMAL_CALC_PAYLOAD,
            format="json",
            HTTP_X_PRINTY_ACTIVE_ROLE="partner",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_invalid_active_role_is_rejected(self):
        resp = self._client(self.buyer).post(
            "/api/calculator/drafts/",
            MINIMAL_CALC_PAYLOAD,
            format="json",
            HTTP_X_PRINTY_ACTIVE_ROLE="banana",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_hybrid_account_using_buyer_active_role_is_allowed(self):
        hybrid = User.objects.create_user(
            email="law9-hybrid@test.ke",
            password="pass12345",
            role=User.Role.CLIENT,
            partner_profile_enabled=True,
        )
        Shop.objects.create(
            owner=hybrid, name="Hybrid Press", slug="law9-hybrid-press", is_active=True
        )
        # Owning a shop promotes the account to production via signal; restore
        # the buyer role so the account genuinely holds client+partner+production.
        hybrid.role = User.Role.CLIENT
        hybrid.save(update_fields=["role", "updated_at"])
        resp = self._client(hybrid).post(
            "/api/calculator/drafts/",
            MINIMAL_CALC_PAYLOAD,
            format="json",
            HTTP_X_PRINTY_ACTIVE_ROLE="client",
        )
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_hybrid_account_using_manager_active_role_is_rejected(self):
        hybrid = User.objects.create_user(
            email="law9-hybrid2@test.ke",
            password="pass12345",
            role=User.Role.CLIENT,
            partner_profile_enabled=True,
        )
        Shop.objects.create(
            owner=hybrid, name="Hybrid Press 2", slug="law9-hybrid-press-2", is_active=True
        )
        hybrid.role = User.Role.CLIENT
        hybrid.save(update_fields=["role", "updated_at"])
        resp = self._client(hybrid).post(
            "/api/calculator/drafts/",
            MINIMAL_CALC_PAYLOAD,
            format="json",
            HTTP_X_PRINTY_ACTIVE_ROLE="partner",
        )
        self.assertEqual(resp.status_code, 403, resp.content)

    def test_validate_active_role_helper_rejects_unheld_role(self):
        from accounts.services.roles import validate_active_role, ActiveRoleNotHeld

        with self.assertRaises(ActiveRoleNotHeld):
            validate_active_role(self.buyer, "partner")

    def test_validate_active_role_helper_accepts_held_role(self):
        from accounts.services.roles import validate_active_role

        self.assertEqual(validate_active_role(self.buyer, "client"), "client")
        self.assertEqual(validate_active_role(self.buyer, "buyer"), "client")


class ManagerPrinterFlowsUnaffectedTests(_BuyerOnlyBase):
    """Law 9.2 — manager-to-printer flows are unchanged by the buyer gate."""

    def test_partner_can_reach_partner_dashboard(self):
        resp = self._client(self.manager).get("/api/dashboard/partner-home/")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_partner_can_list_partner_quotes(self):
        resp = self._client(self.manager).get("/api/partner/quotes/")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_partner_can_reach_production_matches(self):
        resp = self._client(self.manager).post(
            "/api/partner/production-matches/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "width_mm": 85,
                "height_mm": 55,
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )
        self.assertNotIn(resp.status_code, (401, 403), resp.content)

    def test_printer_can_reach_production_dashboard(self):
        resp = self._client(self.printer).get("/api/dashboard/production-home/")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_printer_can_reach_production_jobs(self):
        resp = self._client(self.printer).get("/api/dashboard/production/jobs/")
        self.assertEqual(resp.status_code, 200, resp.content)


class GuestSignupClearingTests(TestCase):
    """Law 9.4 — guest drafts are claimed only into buyer accounts.

    A manager or printer sign-up ignores and clears them.
    """

    def _guest_draft(self, session_key):
        from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent
        from quotes.models import CalculatorDraft

        return CalculatorDraft.objects.create(
            user=None,
            guest_session_key=session_key,
            calculator_context=CalculatorDraftContext.PUBLIC_GUEST,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot=MINIMAL_CALC_PAYLOAD["calculator_inputs_snapshot"],
        )

    def test_partner_signup_ignores_and_clears_guest_draft(self):
        from quotes.models import CalculatorDraft

        draft = self._guest_draft("law9-signup-partner")
        client = APIClient()
        resp = client.post(
            "/api/auth/register/partner/",
            {
                "email": "law9-signup-partner@test.ke",
                "password": "pass12345",
                "guest_session_key": "law9-signup-partner",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIsNone(resp.json().get("claimed_guest_draft_id"))
        self.assertFalse(CalculatorDraft.objects.filter(pk=draft.pk).exists())

    def test_printer_signup_ignores_and_clears_guest_draft(self):
        from quotes.models import CalculatorDraft

        draft = self._guest_draft("law9-signup-printer")
        client = APIClient()
        resp = client.post(
            "/api/auth/register/production/",
            {
                "email": "law9-signup-printer@test.ke",
                "password": "pass12345",
                "guest_session_key": "law9-signup-printer",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIsNone(resp.json().get("claimed_guest_draft_id"))
        self.assertFalse(CalculatorDraft.objects.filter(pk=draft.pk).exists())

    def test_client_signup_still_claims_guest_draft(self):
        from quotes.models import CalculatorDraft

        draft = self._guest_draft("law9-signup-client")
        client = APIClient()
        resp = client.post(
            "/api/auth/register/client/",
            {
                "email": "law9-signup-client@test.ke",
                "password": "pass12345",
                "guest_session_key": "law9-signup-client",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(resp.json().get("claimed_guest_draft_id"), draft.pk)
        draft.refresh_from_db()
        self.assertIsNotNone(draft.user_id)
        self.assertEqual(draft.user.email, "law9-signup-client@test.ke")
