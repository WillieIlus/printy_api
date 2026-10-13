"""Law 3.5 role-projection test across the live calculator endpoints.

Implements the prompt's law 3.5 items 1-5:
  1. two specs (300 business cards, 200 A6 flyers) through all roles asserting
     3.1/3.2/3.3;
  2. buyer/anonymous payloads carry no forbidden 1.3 keys;
  3. printer payloads carry no buyer total / per-piece / margin / fee tier;
  4. a manager override flags ``price_updated`` for the buyer and a second
     quote send is rejected;
  5. the whole backend suite is run (see results.txt).

It runs two real specs through the one engine
(``calculate_sheet_pricing``), projects the canonical result for every role,
and asserts the law 3.5 consistency guarantees:

  3.1  buyer total == manager price for the same job/version
  3.2  printer payout == price - manager margin - fees
  3.3  sheets billed shown to manager and printer are identical
  plus that buyer and anonymous payloads contain none of the forbidden 1.3
  fields (law 1.3 / 3.5).

It then drives every endpoint in the section A (public/anonymous) and section
B (buyer/client) surface -- including the calculator draft and guest draft
entry points -- as an anonymous visitor and as a buyer, and asserts the HTTP
payloads never carry imposition, sheet-count, cost-breakdown, margin, fee-tier
or payout keys (law 1.3). It also drives the printer previews and the
send-quote response and asserts a printer never sees the buyer total, the
per-piece price, the manager margin, the cost breakdown or the fee tier.

Failing assertions here are EXPECTED where the server still leaks a field.
They must NOT be weakened to make the suite pass. The prompt requires the
exact failing key/endpoint be recorded in results.txt.

Section A / section B below are the endpoint map for results.txt (that file
did not previously carry lettered sections; this test is the source of the
map, per the prompt's "sections A and B" wording).
"""

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import CalculatorDraft, Quote, QuoteItem, QuoteRequest
from quotes.overrides import record_override
from services.pricing.engine import calculate_sheet_pricing
from services.pricing.role_projection import (
    BUYER_FORBIDDEN_FIELDS,
    ROLE_ANONYMOUS,
    ROLE_BUYER,
    ROLE_MANAGER,
    ROLE_PRINTER,
    build_canonical_result,
    project_canonical,
)
from shops.models import Shop


# --------------------------------------------------------------------------
# Forbidden key-sets (law 1.3)
# --------------------------------------------------------------------------

# Imposition / sheet / spoilage keys a buyer or anonymous visitor must never see.
IMPOSITION_AND_SHEET_KEYS = frozenset(
    {
        "press_sheet",
        "press_sheet_name",
        "parent_sheet",
        "parent_sheet_name",
        "parent_sheets_required",
        "layout",
        "ups",
        "copies_per_sheet",
        "pieces_per_sheet",
        "good_sheets",
        "billable_sheets",
        "sheets_billed",
        "sheets_required",
        "sheets_needed",
        "imposition_count",
        "imposition_label",
        "production_sheets",
        "production_preview",
        "production_summary",
        "production_description",
        "spoilage",
        "waste_sheets_added",
        "fixed_waste_sheets",
        "variable_waste_sheets",
        "variable_waste_rate",
        "maximum_spoilage_rate",
        "maximum_spoilage_sheets",
        "max_billable_sheets",
    }
)

# Cost / margin / fee / payout keys a buyer or anonymous visitor must never see.
FINANCIAL_KEYS = frozenset(
    {
        "cost_breakdown",
        "pricing_breakdown",
        "manager_margin",
        "gross_margin",
        "broker_margin",
        "broker_margin_amount",
        "broker_payout",
        "fee_tier",
        "printer_payout",
        "printer_side_fee",
        "production_cost",
        "production_base_price",
        "shop_payout",
        "printy_fee",
        "platform_service_amount",
        "platform_service_percent",
    }
)

BUYER_ANONYMOUS_FORBIDDEN = IMPOSITION_AND_SHEET_KEYS | FINANCIAL_KEYS

# Keys a printer must never see: buyer total / per-piece price, manager margin,
# cost breakdown beyond own payout, fee tier.
PRINTER_FORBIDDEN = frozenset(
    {
        "grand_total",
        "total_job_price",
        "unit_price",
        "per_piece",
        "breakdown",
        "cost_breakdown",
        "pricing_breakdown",
        "manager_margin",
        "gross_margin",
        "broker_margin",
        "broker_payout",
        "fee_tier",
        "printer_side_fee",
        "production_cost",
        "shop_payout",
        "client_total",
        "client_price",
        "printy_fee",
        "platform_service_amount",
        "platform_service_percent",
    }
)


def _walk_keys(value, prefix=""):
    """Yield ``(path, key)`` for every dict key anywhere in ``value``."""
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path, str(key)
            yield from _walk_keys(child, path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk_keys(child, f"{prefix}[{index}]")


def _forbidden_hits(payload, forbidden):
    hits = []
    for path, key in _walk_keys(payload):
        if key in forbidden:
            hits.append(path)
    return sorted(set(hits))


class Law35RoleProjectionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Law 3.5 projection policy",
            is_active=True,
            add_platform_fee_on_top=False,
        )

        self.buyer = User.objects.create_user(
            email="law35p-buyer@test.com", password="pass12345", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="law35p-manager@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.printer = User.objects.create_user(
            email="law35p-printer@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.printer,
            name="Law 3.5 Projection Press",
            slug="law35p",
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        self.machine = Machine.objects.create(
            shop=self.shop,
            name="Law 3.5 Projection Press",
            max_width_mm=320,
            max_height_mm=450,
            is_active=True,
        )
        self.paper = Paper.objects.create(
            shop=self.shop,
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
            machine=self.machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
            is_default=True,
        )
        FinishingRate.objects.create(
            shop=self.shop,
            name="Cutting",
            slug="cutting-law35p",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )

    # -- helpers -----------------------------------------------------------

    def _client(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def _post(self, client, path, payload):
        try:
            return client.post(path, payload, format="json"), None
        except Exception as exc:  # noqa: BLE001 - surface as a test failure
            return None, exc

    def _get(self, client, path):
        try:
            return client.get(path), None
        except Exception as exc:  # noqa: BLE001
            return None, exc

    def _collect_clean(self, label, response, exc, forbidden):
        if exc is not None:
            return [f"{label}: raised {type(exc).__name__}: {exc}"]
        if response.status_code in (401, 403):
            return []
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return [
                f"{label}: status {response.status_code} returned non-JSON body "
                f"{getattr(response, 'content', b'')[:200]!r}"
            ]
        hits = _forbidden_hits(payload, forbidden)
        if hits:
            return [f"{label}: payload leaks forbidden 1.3 keys {hits} (status {response.status_code})"]
        return []

    def _assert_all_clean(self, problems):
        if problems:
            self.fail("forbidden-key leaks:\n  " + "\n  ".join(problems))

    # -- item 1: two specs through every role (law 3.5) --------------------

    def _run_spec_through_roles(self, *, quantity, width_mm, height_mm, label):
        result = calculate_sheet_pricing(
            shop=self.shop,
            quantity=quantity,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="SIMPLEX",
            width_mm=width_mm,
            height_mm=height_mm,
        )
        engine_payload = result.to_dict()
        price = Decimal(str(engine_payload["totals"]["grand_total"]))
        margin = Decimal("100.00")
        printer_side_fee = Decimal("50.00")
        payout = price - margin - printer_side_fee

        canonical = build_canonical_result(
            engine_payload,
            spec_summary=f"{quantity} x {label}, simplex colour, gloss 300gsm",
            ready_date="2026-11-01",
            delivery="Nairobi, KES 250",
            job_status="quoted",
            current_holder="Print Manager",
            financials={
                "manager_margin": str(margin),
                "printer_side_fee": str(printer_side_fee),
                "printer_payout": str(payout),
            },
        )

        buyer = project_canonical(canonical, ROLE_BUYER)
        manager = project_canonical(canonical, ROLE_MANAGER)
        printer = project_canonical(canonical, ROLE_PRINTER)
        anonymous = project_canonical(canonical, ROLE_ANONYMOUS)

        # 3.1 buyer total == manager price for the same job/version.
        self.assertEqual(Decimal(str(buyer["price"])), Decimal(str(manager["price"])))
        self.assertEqual(Decimal(str(manager["price"])), price)

        # 3.2 printer payout == price - manager margin - fees.
        self.assertEqual(Decimal(str(printer["printer_payout"])), payout)
        self.assertEqual(
            Decimal(str(printer["printer_payout"])),
            price
            - Decimal(str(canonical["manager_margin"]))
            - Decimal(str(canonical["printer_side_fee"])),
        )

        # 3.3 sheets billed identical for manager and printer.
        self.assertIsNotNone(manager["sheets_billed"])
        self.assertEqual(manager["sheets_billed"], printer["sheets_billed"])
        self.assertEqual(printer["sheets_billed"], engine_payload["billable_sheets"])

        # 3.5 buyer/anonymous carry none of the forbidden 1.3 fields.
        for field in BUYER_FORBIDDEN_FIELDS:
            self.assertNotIn(field, buyer, f"{label}:{field}")
            self.assertNotIn(field, anonymous, f"{label}:{field}")

    def test_spec_300_business_cards_through_all_roles(self):
        self._run_spec_through_roles(quantity=300, width_mm=90, height_mm=55, label="business cards")

    def test_spec_200_a6_flyers_through_all_roles(self):
        self._run_spec_through_roles(quantity=200, width_mm=105, height_mm=148, label="A6 flyers")

    # -- section A / B endpoint maps ---------------------------------------

    def _section_a_cases(self):
        """Public/anonymous endpoints (results.txt section A)."""
        return [
            (
                "A:calculator/preview",
                "/api/calculator/preview/",
                {
                    "shop": self.shop.id,
                    "quantity": 300,
                    "paper": self.paper.id,
                    "machine": self.machine.id,
                    "color_mode": "COLOR",
                    "sides": "SIMPLEX",
                    "width_mm": 90,
                    "height_mm": 55,
                },
            ),
            (
                "A:calculator/booklet-preview",
                "/api/calculator/booklet-preview/",
                {
                    "shop": self.shop.id,
                    "quantity": 50,
                    "total_pages": 16,
                    "width_mm": 210,
                    "height_mm": 297,
                    "binding_type": "saddle_stitch",
                    "cover_lamination_mode": "none",
                    "cover_sides": "DUPLEX",
                    "insert_sides": "DUPLEX",
                    "cover_color_mode": "COLOR",
                    "insert_color_mode": "COLOR",
                },
            ),
            (
                "A:calculator/large-format-preview",
                "/api/calculator/large-format-preview/",
                {},
            ),
            (
                "A:calculator/public-preview",
                "/api/calculator/public-preview/",
                {
                    "product_type": "business_card",
                    "quantity": 300,
                    "finished_size": "90x55mm",
                    "requested_paper_category": "gloss",
                    "requested_gsm": 300,
                    "print_sides": "SIMPLEX",
                    "color_mode": "COLOR",
                },
            ),
            (
                "A:public/calculator/preview",
                "/api/public/calculator/preview/",
                {
                    "job_type": "business_cards",
                    "quantity": 300,
                    "width_mm": 90,
                    "height_mm": 55,
                    "print_sides": "SIMPLEX",
                    "colour_mode": "COLOR",
                },
            ),
            (
                "A:public/match-shops",
                "/api/public/match-shops/",
                {
                    "job_type": "business_cards",
                    "quantity": 300,
                    "width_mm": 90,
                    "height_mm": 55,
                    "print_sides": "SIMPLEX",
                    "colour_mode": "COLOR",
                },
            ),
            (
                "A:public/shops/<slug>/quote-preview",
                f"/api/public/shops/{self.shop.slug}/quote-preview/",
                {
                    "session_key": "law35p-guest",
                    "product_type": "business_card",
                    "quantity": 300,
                    "width_mm": 90,
                    "height_mm": 55,
                    "paper_gsm": 300,
                    "paper_type": "gloss",
                    "print_sides": "SIMPLEX",
                    "colour_mode": "COLOR",
                },
            ),
            (
                "A:calculator/guest-drafts",
                "/api/calculator/guest-drafts/",
                {
                    "session_key": "law35p-guest",
                    "calculator_inputs_snapshot": {"product_type": "business_card", "quantity": 300},
                    "pricing_snapshot": {
                        "totals": {"grand_total": "3200.00"},
                        "cost_breakdown": {"manager_margin": "100.00"},
                    },
                    "request_details_snapshot": {"production_preview": {"good_sheets": 5}},
                },
            ),
        ]

    def _section_b_cases(self, draft_id, quote_request_id):
        """Authenticated buyer/client endpoints (results.txt section B)."""
        return [
            ("B:calculator/drafts (list)", f"/api/calculator/drafts/"),
            ("B:calculator/drafts/<pk>", f"/api/calculator/drafts/{draft_id}/"),
            ("B:calculator/buyer-quotes", "/api/calculator/buyer-quotes/"),
            ("B:quote-requests/<id>", f"/api/quote-requests/{quote_request_id}/"),
            ("B:client/requests/<id>", f"/api/client/requests/{quote_request_id}/"),
            ("B:client/responses", "/api/client/responses/"),
        ]

    def _seed_quote_request(self):
        request_snapshot = {
            "pricing_preview_snapshot": {"totals": {"grand_total": "3200.00"}},
            "production_preview_snapshot": {"good_sheets": 5, "billable_sheets": 8},
            "internal_pricing": {"production_cost": "1500.00"},
        }
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            customer_name="Law 3.5 Buyer",
            customer_email=self.buyer.email,
            status=QuoteStatus.QUOTED,
            request_snapshot=request_snapshot,
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.printer,
            status="SENT",
            total=Decimal("3200.00"),
            response_snapshot={
                "production_preview": {"good_sheets": 5},
                "internal_pricing": {"production_cost": "1500.00"},
            },
        )
        QuoteItem.objects.create(
            quote_request=quote_request,
            quote=quote,
            item_type="PRODUCT",
            pricing_mode="SHEET",
            title="300 business cards",
            quantity=300,
            pricing_snapshot={
                "totals": {"grand_total": "3200.00"},
                "good_sheets": 5,
                "billable_sheets": 8,
                "production_cost": "1500.00",
            },
        )
        return quote_request

    def _seed_draft(self):
        return CalculatorDraft.objects.create(
            user=self.buyer,
            title="Law 3.5 draft",
            calculator_inputs_snapshot={"product_type": "business_card", "quantity": 300},
            pricing_snapshot={
                "totals": {"grand_total": "3200.00"},
                "good_sheets": 5,
                "billable_sheets": 8,
                "cost_breakdown": {"manager_margin": "100.00"},
            },
            request_details_snapshot={"production_preview": {"good_sheets": 5}},
        )

    # -- item 2: section A as anonymous / buyer ----------------------------

    def test_section_a_anonymous_payloads_have_no_forbidden_keys(self):
        client = self._client()
        problems = []
        for label, path, payload in self._section_a_cases():
            response, exc = self._post(client, path, payload)
            problems += self._collect_clean(f"anonymous:{label}", response, exc, BUYER_ANONYMOUS_FORBIDDEN)
        self._assert_all_clean(problems)

    def test_section_a_buyer_payloads_have_no_forbidden_keys(self):
        client = self._client(self.buyer)
        problems = []
        for label, path, payload in self._section_a_cases():
            response, exc = self._post(client, path, payload)
            problems += self._collect_clean(f"buyer:{label}", response, exc, BUYER_ANONYMOUS_FORBIDDEN)
        self._assert_all_clean(problems)

    # -- item 2: section B as buyer ----------------------------------------

    def test_section_b_buyer_snapshots_have_no_forbidden_keys(self):
        quote_request = self._seed_quote_request()
        draft = self._seed_draft()
        client = self._client(self.buyer)
        problems = []
        for label, path in self._section_b_cases(draft.id, quote_request.id):
            response, exc = self._get(client, path)
            problems += self._collect_clean(f"buyer:{label}", response, exc, BUYER_ANONYMOUS_FORBIDDEN)
        self._assert_all_clean(problems)

    def test_section_b_draft_send_and_direct_shop_have_no_forbidden_keys(self):
        draft = self._seed_draft()
        client = self._client(self.buyer)
        problems = []
        send_response, send_exc = self._post(
            client,
            f"/api/calculator/drafts/{draft.id}/send/",
            {"manager_selection_mode": "printy_auto"},
        )
        problems += self._collect_clean("buyer:B:calculator/drafts/<pk>/send", send_response, send_exc, BUYER_ANONYMOUS_FORBIDDEN)

        # A second, fresh draft for the direct-shop submit path.
        draft2 = self._seed_draft()
        submit_response, submit_exc = self._post(
            client,
            f"/api/calculator/drafts/{draft2.id}/direct-shop/submit/",
            {},
        )
        problems += self._collect_clean(
            "buyer:B:calculator/drafts/<pk>/direct-shop/submit", submit_response, submit_exc, BUYER_ANONYMOUS_FORBIDDEN
        )
        self._assert_all_clean(problems)

    def test_guest_draft_endpoints_have_no_forbidden_keys(self):
        problems = []
        # Anonymous guest draft upsert.
        anon = self._client()
        upsert_response, upsert_exc = self._post(
            anon,
            "/api/calculator/guest-drafts/",
            {
                "session_key": "law35p-guest-2",
                "calculator_inputs_snapshot": {"product_type": "business_card", "quantity": 300},
                "pricing_snapshot": {"cost_breakdown": {"manager_margin": "100.00"}},
            },
        )
        problems += self._collect_clean(
            "anonymous:A:calculator/guest-drafts", upsert_response, upsert_exc, BUYER_ANONYMOUS_FORBIDDEN
        )

        # Authenticated buyer claims the guest draft.
        claim_client = self._client(self.buyer)
        claim_response, claim_exc = self._post(
            claim_client,
            "/api/calculator/drafts/claim/",
            {"session_key": "law35p-guest-2"},
        )
        problems += self._collect_clean(
            "buyer:B:calculator/drafts/claim", claim_response, claim_exc, BUYER_ANONYMOUS_FORBIDDEN
        )

        # Guest quote-request send.
        guest_send_response, guest_send_exc = self._post(anon, "/api/quote-requests/guest-send/", {})
        problems += self._collect_clean(
            "anonymous:A:quote-requests/guest-send", guest_send_response, guest_send_exc, BUYER_ANONYMOUS_FORBIDDEN
        )
        self._assert_all_clean(problems)

    # -- item 3: printer payloads ------------------------------------------

    def test_printer_preview_payloads_have_no_buyer_total_margin_or_fee(self):
        client = self._client(self.printer)
        problems = []
        for label, path, payload in self._section_a_cases():
            if label not in ("A:calculator/preview", "A:calculator/booklet-preview"):
                continue
            response, exc = self._post(client, path, payload)
            problems += self._collect_clean(f"printer:{label}", response, exc, PRINTER_FORBIDDEN)
        self._assert_all_clean(problems)

    # -- item 4: manager override -> buyer price_updated; sent quote locked --

    def test_manager_override_flags_buyer_price_updated(self):
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            customer_name="Law 3.5 Buyer",
            customer_email=self.buyer.email,
            status=QuoteStatus.QUOTED,
        )
        Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.printer,
            status=QuoteOfferStatus.SENT,
            total=Decimal("3200.00"),
            sent_at=timezone.now(),
        )
        record_override(
            quote_request=quote_request,
            field="markup",
            old_value={"total": "3200.00"},
            new_value={"total": "3500.00"},
            user=self.manager,
            old_total="3200.00",
            new_total="3500.00",
        )
        quote_request.refresh_from_db()
        self.assertTrue(quote_request.price_updated)
        self.assertEqual(quote_request.price_updated_old_total, Decimal("3200.00"))
        self.assertEqual(quote_request.price_updated_new_total, Decimal("3500.00"))

        # The buyer's own view must surface the "price updated" state (law 2.3).
        client = self._client(self.buyer)
        response, exc = self._get(client, f"/api/quote-requests/{quote_request.id}/")
        self.assertIsNone(exc)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["price_updated"])
        self.assertEqual(Decimal(str(payload["price_updated_new_total"])), Decimal("3500.00"))

    def test_buyer_result_fields_and_acknowledge_endpoint(self):
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            customer_name="Law 3.5 Buyer",
            customer_email=self.buyer.email,
            status=QuoteStatus.QUOTED,
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.printer,
            status=QuoteOfferStatus.SENT,
            total=Decimal("3500.00"),
            sent_at=timezone.now(),
        )
        QuoteItem.objects.create(
            quote_request=quote_request,
            quote=quote,
            item_type="PRODUCT",
            pricing_mode="SHEET",
            title="300 business cards",
            spec_text="90x55mm, 300gsm gloss",
            quantity=300,
            unit_price=Decimal("11.67"),
        )
        record_override(
            quote_request=quote_request,
            field="markup",
            old_value={"total": "3200.00"},
            new_value={"total": "3500.00"},
            user=self.manager,
            old_total="3200.00",
            new_total="3500.00",
        )

        client = self._client(self.buyer)
        listing = client.get("/api/client/responses/")
        self.assertEqual(listing.status_code, 200)
        row = listing.json()[0]
        self.assertEqual(Decimal(str(row["price"])), Decimal("3500.00"))
        self.assertEqual(Decimal(str(row["per_piece"])), Decimal("11.67"))
        self.assertEqual(row["spec_summary"][0]["title"], "300 business cards")
        self.assertTrue(row["price_updated"])
        self.assertEqual(Decimal(str(row["price_updated_old_total"])), Decimal("3200.00"))
        self.assertEqual(Decimal(str(row["price_updated_new_total"])), Decimal("3500.00"))
        self.assertIn("ready_text", row)
        self.assertIn("delivery", row)
        self.assertIn("holder", row)

        ack = client.post(f"/api/client/responses/{quote.id}/acknowledge/")
        self.assertEqual(ack.status_code, 200)
        quote_request.refresh_from_db()
        self.assertFalse(quote_request.price_updated)
        self.assertIsNotNone(quote_request.price_acknowledged_at)

    def test_second_quote_send_is_rejected(self):
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            customer_name="Law 3.5 Buyer",
            customer_email=self.buyer.email,
            status=QuoteStatus.QUOTED,
        )
        Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.printer,
            status=QuoteOfferStatus.SENT,
            total=Decimal("3200.00"),
            sent_at=timezone.now(),
        )
        client = self._client(self.printer)
        path = f"/api/shops/{self.shop.slug}/incoming-requests/{quote_request.id}/send-quote/"
        response, exc = self._post(client, path, {"total": "3200.00"})
        self.assertIsNone(exc)
        self.assertEqual(response.status_code, 400)
        self.assertIn("already sent", str(response.json().get("detail", "")).lower())

    def test_send_quote_response_has_no_buyer_total_margin_or_fee(self):
        request_snapshot = {
            "pricing_preview_snapshot": {"totals": {"grand_total": "3200.00"}},
            "production_preview_snapshot": {"good_sheets": 5, "billable_sheets": 8},
        }
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            customer_name="Law 3.5 Buyer",
            customer_email=self.buyer.email,
            status=QuoteStatus.SUBMITTED,
            request_snapshot=request_snapshot,
        )
        client = self._client(self.printer)
        path = f"/api/shops/{self.shop.slug}/incoming-requests/{quote_request.id}/send-quote/"
        response, exc = self._post(client, path, {"total": "3200.00"})
        self._assert_all_clean(
            self._collect_clean("printer:send-quote", response, exc, PRINTER_FORBIDDEN)
        )
