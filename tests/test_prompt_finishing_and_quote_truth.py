"""Launch-blocker regression tests (prompt items 1-5).

1. Finishing resolver coverage (round corners, brochure lamination/stitching/
   saddle stitch/binding) and the platform capability gate.
2. Lamination is per_sheet, never per_pc.
3. One source of price truth: the quote's per-sheet figure equals the printer
   table's "Total 2s" (65 for SRA3 300gsm matte, 100 business cards).
4. Manager-only quote-prepare fields, gated by ``X-Printy-Active-Role``.
"""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PrintingRate, ShopRateCardSetup
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from services.pricing.breakdown_projection import production_breakdown_from_preview
from services.pricing.calculator_preview import build_public_calculator_preview
from services.pricing.engine import calculate_sheet_pricing
from services.pricing.finishing_normalization import (
    finishing_family,
    finishing_is_offered_anywhere,
    resolve_finishing_rate_for_slug,
)
from services.public_matching import _normalize_booklet_binding
from services.pricing.mvp_rate_card import (
    _activated_default_finishing_rows,
    _persist_finishing_rows,
    preview_shop_rate_card_table,
    _build_default_paper_rows,
)
from shops.models import Shop


def _make_shop(*, slug="prompt5-shop", email="prompt5@test.com"):
    owner = User.objects.create_user(
        email=email, password="pass12345", role=User.Role.PRODUCTION
    )
    shop = Shop.objects.create(
        owner=owner,
        name="Prompt5 Press",
        slug=slug,
        is_active=True,
        is_public=True,
        city="Nairobi",
        service_area="Westlands",
    )
    return owner, shop


def _machine(shop):
    return Machine.objects.create(
        shop=shop,
        name="Digital Press",
        max_width_mm=320,
        max_height_mm=450,
        is_active=True,
    )


def _paper(shop, *, gsm, category, paper_type, price, cover=False, insert=False, default=False):
    return Paper.objects.create(
        shop=shop,
        name=f"SRA3 {gsm}gsm",
        sheet_size="SRA3",
        gsm=gsm,
        paper_type=paper_type,
        category=category,
        buying_price=Decimal("1.00"),
        selling_price=Decimal(price),
        width_mm=320,
        height_mm=450,
        is_active=True,
        is_default=default,
        is_cover_stock=cover,
        is_insert_stock=insert,
    )


def _printing_rate(machine):
    return PrintingRate.objects.create(
        machine=machine,
        sheet_size="SRA3",
        color_mode="COLOR",
        single_price=Decimal("15.00"),
        double_price=None,
        duplex_surcharge=Decimal("10.00"),
        duplex_surcharge_enabled=True,
        duplex_surcharge_min_gsm=250,
        is_active=True,
        is_default=True,
    )


def _finishing(shop, *, name, slug, price, mode="flat_per_job", minimum=None, double=None):
    fields = {
        "per_sheet": (ChargeUnit.PER_SHEET, FinishingBillingBasis.PER_SHEET, FinishingSideMode.PER_SELECTED_SIDE),
        "per_piece": (ChargeUnit.PER_PIECE, FinishingBillingBasis.PER_PIECE, FinishingSideMode.IGNORE_SIDES),
        "flat_per_job": (ChargeUnit.FLAT, FinishingBillingBasis.FLAT_PER_JOB, FinishingSideMode.IGNORE_SIDES),
    }[mode]
    charge_unit, billing_basis, side_mode = fields
    return FinishingRate.objects.create(
        shop=shop,
        name=name,
        slug=slug,
        charge_unit=charge_unit,
        billing_basis=billing_basis,
        side_mode=side_mode,
        price=Decimal(price),
        double_side_price=Decimal(double) if double else None,
        minimum_charge=Decimal(minimum) if minimum else None,
        is_active=True,
    )


class FinishingResolverCoverageTests(TestCase):
    def setUp(self):
        self.owner, self.shop = _make_shop()
        self.machine = _machine(self.shop)
        self.paper = _paper(
            self.shop, gsm=300, category="artcard", paper_type="Matte",
            price="25.00", cover=True, insert=True, default=True,
        )
        _printing_rate(self.machine)
        self.round_corner = _finishing(
            self.shop, name="Round Cornering Piece", slug="round-corner-piece",
            price="1.00", mode="per_piece", minimum="50.00",
        )
        self.stitching = _finishing(
            self.shop, name="Stitching Booklet", slug="stitching-booklet",
            price="5.00", mode="per_piece", minimum="100.00",
        )
        self.perfect_binding = _finishing(
            self.shop, name="Perfect Bind", slug="perfect-binding",
            price="50.00", mode="per_piece", minimum="200.00",
        )
        self.wire_o = _finishing(
            self.shop, name="Wire-O-Wire", slug="wire-o",
            price="30.00", mode="per_piece", minimum="100.00",
        )
        self.lamination = _finishing(
            self.shop, name="Gloss Lamination Double", slug="gloss-lamination-double",
            price="15.00", mode="per_sheet", minimum="50.00", double="30.00",
        )

    def test_every_buyer_finishing_vocabulary_resolves_to_the_configured_row(self):
        cases = {
            "corner-rounding": self.round_corner,
            "rounded-corners": self.round_corner,
            "corner_rounding": self.round_corner,
            "saddle-stitch": self.stitching,
            "saddle_stitch": self.stitching,
            "stitching": self.stitching,
            "perfect-bind": self.perfect_binding,
            "binding": self.perfect_binding,
            "wire-o": self.wire_o,
            "wire_o": self.wire_o,
            "gloss-lamination": self.lamination,
            "gloss": self.lamination,
        }
        for requested, expected in cases.items():
            resolved = resolve_finishing_rate_for_slug(self.shop, requested)
            self.assertIsNotNone(resolved, f"{requested} must resolve")
            self.assertEqual(resolved.id, expected.id, requested)

    def test_gloss_request_never_resolves_to_a_matt_row(self):
        _finishing(
            self.shop, name="Matt Lamination Double", slug="matt-lamination-double",
            price="45.00", mode="per_sheet", minimum="50.00", double="90.00",
        )
        gloss = resolve_finishing_rate_for_slug(self.shop, "gloss-lamination")
        matt = resolve_finishing_rate_for_slug(self.shop, "matt-lamination")
        self.assertEqual(gloss.id, self.lamination.id)
        self.assertNotEqual(matt.id, self.lamination.id)
        self.assertIn("matt", matt.slug)

    def test_round_corners_are_priced_in_public_preview(self):
        response = build_public_calculator_preview({
            "product_type": "business_card",
            "quantity": 100,
            "finished_size": "90x55mm",
            "print_sides": "DUPLEX",
            "color_mode": "COLOR",
            "requested_paper_category": "artcard",
            "requested_gsm": 300,
            "corner_rounding": True,
        })
        self.assertTrue(response["can_calculate"], response.get("summary"))
        self.assertGreaterEqual(response["matches_count"], 1)
        selected = response["production_preview"]["selected_finishings"]
        self.assertIn("Round Cornering Piece", selected)
        self.assertGreater(Decimal(str(response["estimate_min"])), Decimal("0"))
        self.assertNotIn("Finish your spec", str(response.get("summary") or ""))

    def test_unconfigured_optional_finishing_does_not_hide_every_price(self):
        """No shop configures folding, so it is a preference, not a gate."""
        self.assertFalse(finishing_is_offered_anywhere("half-fold"))
        response = build_public_calculator_preview({
            "product_type": "flyer",
            "quantity": 100,
            "finished_size": "A5",
            "print_sides": "SIMPLEX",
            "color_mode": "COLOR",
            "requested_paper_category": "artcard",
            "requested_gsm": 300,
            "folding": "half-fold",
        })
        self.assertTrue(response["can_calculate"], response.get("summary"))
        self.assertGreaterEqual(response["matches_count"], 1)

    def test_brochure_binding_and_cover_lamination_are_priced(self):
        insert = _paper(
            self.shop, gsm=130, category="matt", paper_type="MATTE",
            price="12.00", insert=True,
        )
        self.assertTrue(insert.is_insert_stock)
        self.assertEqual(finishing_family("saddle_stitch"), "stitching")
        self.assertEqual(_normalize_booklet_binding("saddle_stitch"), "saddle_stitch")
        response = build_public_calculator_preview({
            "product_type": "booklet",
            "quantity": 100,
            "finished_size": "A5",
            "total_pages": 12,
            "binding_type": "saddle_stitch",
            "cover_lamination": "both",
            "color_mode": "COLOR",
        })
        self.assertTrue(response["can_calculate"], response.get("summary"))
        self.assertGreaterEqual(response["matches_count"], 1)
        self.assertGreater(Decimal(str(response["estimate_min"])), Decimal("0"))
        production = response["production_preview"]
        self.assertEqual(production["booklet_binding_label"], "Saddle stitch")
        self.assertEqual(production["booklet_normalized_pages"], 12)


class LaminationPerSheetTests(TestCase):
    def setUp(self):
        self.owner, self.shop = _make_shop(slug="lamination-shop", email="lam@test.com")
        self.machine = _machine(self.shop)
        self.paper = _paper(
            self.shop, gsm=300, category="artcard", paper_type="Matte",
            price="25.00", cover=True, insert=True, default=True,
        )
        _printing_rate(self.machine)

    def test_legacy_per_piece_lamination_row_is_repaired_to_per_sheet(self):
        legacy = _finishing(
            self.shop, name="Gloss Lamination Double", slug="gloss-lamination-double",
            price="15.00", mode="per_piece",
        )
        _persist_finishing_rows(self.shop, [{
            "key": "gloss_lamination_double",
            "name": "Gloss Lamination Double",
            "price": "15.00",
            "minimum_charge": "50.00",
            "pricing_mode": "per_piece",
            "active": True,
        }])
        legacy.refresh_from_db()
        self.assertEqual(legacy.billing_basis, FinishingBillingBasis.PER_SHEET)
        self.assertEqual(legacy.charge_unit, ChargeUnit.PER_SHEET)
        self.assertEqual(legacy.side_mode, FinishingSideMode.PER_SELECTED_SIDE)

    def test_lamination_bills_good_sheets_not_pieces(self):
        lamination = _finishing(
            self.shop, name="Gloss Lamination Double", slug="gloss-lamination-double",
            price="15.00", mode="per_sheet", minimum="50.00", double="30.00",
        )
        result = calculate_sheet_pricing(
            shop=self.shop,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="DUPLEX",
            finishing_selections=[{"rule": lamination, "selected_side": "both"}],
            width_mm=90,
            height_mm=55,
        ).to_dict()
        line = result["breakdown"]["finishings"][0]
        self.assertEqual(line["billing_basis"], "per_sheet")
        self.assertEqual(line["units"], str(result["good_sheets"]))
        self.assertNotEqual(line["units"], "100")
        # Both sides uses the both-side rate per sheet (30.00), not 2 x per piece.
        self.assertEqual(Decimal(line["total"]), Decimal("30.00") * result["good_sheets"])


class OneSourceOfPriceTruthTests(TestCase):
    def setUp(self):
        self.owner, self.shop = _make_shop(slug="truth-shop", email="truth@test.com")
        self.machine = _machine(self.shop)
        self.paper = _paper(
            self.shop, gsm=300, category="artcard", paper_type="Matte",
            price="25.00", cover=True, insert=True, default=True,
        )
        _printing_rate(self.machine)
        self.cutting = _finishing(
            self.shop, name="Cutting", slug="cutting", price="50.00", mode="flat_per_job",
        )

    def test_quote_per_sheet_equals_printer_table_total_two_sides(self):
        table = preview_shop_rate_card_table(
            paper_rows=_build_default_paper_rows(), finishing_rows=[]
        )
        row = next(r for r in table["rows"] if r["key"] == "300gsm_matte_art_card")
        self.assertEqual(row["total_both_sides"], "65.00")

        result = calculate_sheet_pricing(
            shop=self.shop,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="DUPLEX",
            finishing_selections=[{"rule": self.cutting, "selected_side": "both"}],
            width_mm=90,
            height_mm=55,
        ).to_dict()

        self.assertEqual(result["totals"]["stock_printing_total_per_sheet"], "65.00")
        self.assertEqual(
            result["totals"]["stock_printing_total_per_sheet"],
            row["total_both_sides"],
        )
        self.assertEqual(
            Decimal(result["totals"]["stock_printing_total"]),
            Decimal("65.00") * Decimal(result["billable_sheets"]),
        )
        self.assertEqual(
            result["breakdown"]["stock_printing_total_per_sheet"], "65.00"
        )

    def test_table_publishes_server_side_quote_per_sheet_and_note(self):
        """Prompt item 5 — the quote's per-sheet figure ships from the server.

        The printer's price table shows the figure a quote charges plus the
        note explaining which column it came from; the browser never derives it.
        """
        duplex_table = preview_shop_rate_card_table(
            paper_rows=_build_default_paper_rows(), finishing_rows=[]
        )
        basis = duplex_table["quote_basis"]
        self.assertEqual(basis["sides"], "DUPLEX")
        self.assertEqual(basis["column"], "total_both_sides")
        self.assertEqual(basis["label"], "Total 2s")
        self.assertEqual(basis["note"], "Quote uses Total 2s for duplex")
        duplex_row = next(r for r in duplex_table["rows"] if r["key"] == "300gsm_matte_art_card")
        self.assertEqual(duplex_row["quote_column"], "total_both_sides")
        self.assertEqual(duplex_row["quote_per_sheet"], duplex_row["total_both_sides"])
        self.assertEqual(duplex_row["quote_per_sheet"], "65.00")

        simplex_table = preview_shop_rate_card_table(
            paper_rows=_build_default_paper_rows(), finishing_rows=[], sides="SIMPLEX"
        )
        self.assertEqual(simplex_table["quote_basis"]["sides"], "SIMPLEX")
        self.assertEqual(simplex_table["quote_basis"]["label"], "Total 1s")
        self.assertEqual(
            simplex_table["quote_basis"]["note"], "Quote uses Total 1s for single-sided"
        )
        simplex_row = next(
            r for r in simplex_table["rows"] if r["key"] == "300gsm_matte_art_card"
        )
        self.assertEqual(simplex_row["quote_column"], "total_one_side")
        self.assertEqual(simplex_row["quote_per_sheet"], simplex_row["total_one_side"])

        # The engine's own per-sheet figure still equals both published columns.
        for sides, column in (("DUPLEX", "total_both_sides"), ("SIMPLEX", "total_one_side")):
            result = calculate_sheet_pricing(
                shop=self.shop,
                quantity=100,
                paper=self.paper,
                machine=self.machine,
                color_mode="COLOR",
                sides=sides,
                width_mm=90,
                height_mm=55,
            ).to_dict()
            table = next(
                t
                for t in (duplex_table, simplex_table)
                if t["quote_basis"]["sides"] == sides
            )
            row = next(r for r in table["rows"] if r["key"] == "300gsm_matte_art_card")
            self.assertEqual(result["totals"]["stock_printing_total_per_sheet"], row[column])
            self.assertEqual(row["quote_per_sheet"], row[column])

    def test_manager_breakdown_exposes_one_stock_printing_line(self):
        """The manager quote shows ONE paper + printing line, not two.

        Its label, sides, sheet count, per-sheet rate and total all come from the
        engine projection, so the quote and the printer's "Total 2s" agree.
        """
        result = calculate_sheet_pricing(
            shop=self.shop,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="DUPLEX",
            finishing_selections=[{"rule": self.cutting, "selected_side": "both"}],
            width_mm=90,
            height_mm=55,
        ).to_dict()

        projection = production_breakdown_from_preview(result)
        line = projection["stock_printing_line"]
        self.assertIsNotNone(line)
        self.assertEqual(line["component"], "stock_printing")
        self.assertEqual(line["unit_price_kes"], "65.00")
        self.assertEqual(line["quantity"], result["billable_sheets"])
        self.assertEqual(
            Decimal(line["total_kes"]),
            Decimal("65.00") * Decimal(result["billable_sheets"]),
        )
        self.assertEqual(
            Decimal(projection["stock_printing_total"]), Decimal(line["total_kes"])
        )
        self.assertIn("both sides", line["spec"])
        self.assertIn(f"{result['billable_sheets']} sheets", line["spec"])
        # Cutting stays its own line, so the manager still sees the finishing.
        components = [item["component"] for item in projection["line_items"]]
        self.assertIn("cutting", components)


class ManagerQuotePrepareFieldsTests(TestCase):
    """Item 4: manager money fields exist for manager, never for buyer/printer."""

    MANAGER_KEYS = (
        "printer_total",
        "markup_pct",
        "markup_amount",
        "printy_fee",
        "manager_total_to_client",
        "per_piece",
    )

    def setUp(self):
        self.manager = User.objects.create_user(
            email="prepare-manager@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            partner_profile_enabled=True,
            capability_overrides={"can_manage_clients": True, "can_source_jobs": True},
        )
        # Owning a shop adds the production role so the same account can act as
        # the printer and prove the payload is gated by the active role header.
        self.shop = Shop.objects.create(
            owner=self.manager,
            name="Prepare Press",
            slug="prepare-press",
            is_active=True,
            is_public=True,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.manager,
            assigned_manager=self.manager,
            customer_name="Prepare Client",
            status=QuoteStatus.SUBMITTED,
            request_snapshot={
                "calculator_inputs": {
                    "product_type": "business_card",
                    "quantity": 100,
                    "finished_size": "90x55mm",
                    "print_sides": "DUPLEX",
                    "color_mode": "COLOR",
                }
            },
        )
        self.snapshot = {
            "currency": "KES",
            "selected_shops": [
                {
                    "id": self.shop.id,
                    "slug": self.shop.slug,
                    "price_available": True,
                    "production_cost": "650.00",
                    "preview": {
                        "quantity": 100,
                        "totals": {"subtotal": "650.00", "grand_total": "650.00"},
                        "breakdown": {"imposition": {"good_sheets": 5}},
                    },
                }
            ],
        }
        self.api = APIClient()
        self.api.force_authenticate(self.manager)

    def _prepare(self, active_role):
        return self.api.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/prepare/",
            {
                "shop": self.shop.id,
                "pricing_snapshot": self.snapshot,
                "markup_pct": "75.00",
                "note": "Prepared from the manager builder.",
            },
            format="json",
            HTTP_X_PRINTY_ACTIVE_ROLE=active_role,
        )

    def test_manager_response_carries_every_manager_field(self):
        response = self._prepare("partner")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        for key in self.MANAGER_KEYS:
            self.assertIn(key, body, key)
            self.assertEqual(body[key], body["manager_pricing"][key])
        self.assertEqual(Decimal(body["printer_total"]), Decimal("650.00"))
        self.assertEqual(Decimal(body["markup_pct"]), Decimal("75.00"))
        self.assertEqual(
            Decimal(body["markup_amount"]),
            Decimal("487.50").quantize(Decimal("1")),
        )
        self.assertEqual(
            Decimal(body["manager_total_to_client"]),
            Decimal(str(body["quote"]["total"])),
        )
        self.assertEqual(
            Decimal(body["per_piece"]),
            (Decimal(body["manager_total_to_client"]) / Decimal("100")).quantize(
                Decimal("0.01")
            ),
        )
        self.assertGreater(Decimal(body["printy_fee"]), Decimal("0"))

    def test_buyer_payload_never_contains_manager_money_fields(self):
        response = self._prepare("client")
        self.assertIn(response.status_code, (200, 201), response.content)
        text = response.content.decode().lower()
        for key in self.MANAGER_KEYS:
            self.assertNotIn(key, text, key)
        self.assertNotIn("printy_fee", text)

    def test_printer_payload_never_contains_manager_money_fields(self):
        response = self._prepare("production")
        self.assertIn(response.status_code, (200, 201), response.content)
        text = response.content.decode().lower()
        self.assertNotIn("markup_pct", text)
        self.assertNotIn("markup_amount", text)
        self.assertNotIn("printy_fee", text)


class AuditFinishingCoverageCommandTests(TestCase):
    def setUp(self):
        self.owner, self.shop = _make_shop(slug="audit-shop", email="audit@test.com")
        _persist_finishing_rows(self.shop, _activated_default_finishing_rows())
        ShopRateCardSetup.objects.update_or_create(
            shop=self.shop, defaults={"is_confirmed": True, "completed": True}
        )

    def test_audit_reports_zero_gaps_for_a_fully_configured_shop(self):
        out = StringIO()
        call_command("audit_finishing_coverage", stdout=out)
        self.assertIn("0 configured finishing gaps", out.getvalue())
