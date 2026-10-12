"""Regression guards: every pricing path must use the WastePolicy-spoilage-capped
billable sheet count for paper + printing, keep the good (finished) sheet count as
the finishing basis, and never present the uncapped physical production count as
the billed figure.

History (from docs/PRICING_MPESA_DIAGNOSIS.md): the canonical capped count
(``calculate_billable_sheets``) was only applied on the engine pricing path. The
fallback branch in ``_compute_sheet_pricing`` (no machine/sides/color_mode) billed
paper on the good-sheet count, and several payloads exposed the physical
``production_sheets`` (= 5 for a 2-good-sheet job) next to the billed count (= 3)
without a consistent ``sheets_required``/``billable_sheets`` contract.
"""

from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from catalog.choices import PricingMode
from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.choices import (
    ChargeUnit,
    ColorMode,
    FinishingBillingBasis,
    FinishingSideMode,
    Sides,
)
from pricing.models import FinishingRate, PrintingRate, WastePolicy
from pricing.services.production_cost_calculator import calculate_billable_sheets
from quotes.models import QuoteItem, QuoteItemFinishing, QuoteRequest
from quotes.pricing_service import compute_quote_item_pricing
from shops.models import Shop


class WasteBillingConsistencyTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="waste-billing@test.com", password="pass12345")
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Waste Billing Shop",
            slug="waste-billing-shop",
            is_active=True,
            currency="KES",
        )
        WastePolicy.objects.create(
            name="Default",
            is_active=True,
            fixed_waste_sheets=2,
            variable_waste_rate=Decimal("0.1000"),
            minimum_billable_sheets=3,
            maximum_spoilage_rate=Decimal("0.5000"),
        )
        self.machine = Machine.objects.create(
            shop=self.shop,
            name="Digital A3 Press",
            max_width_mm=330,
            max_height_mm=488,
            is_active=True,
        )
        self.paper = Paper.objects.create(
            shop=self.shop,
            name="Art Card 350gsm",
            sheet_size="SRA3",
            gsm=350,
            paper_type="MATTE",
            category="artcard",
            buying_price=Decimal("24.00"),
            selling_price=Decimal("38.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
            is_default=True,
        )
        PrintingRate.objects.create(
            machine=self.machine,
            sheet_size="SRA3",
            color_mode=ColorMode.COLOR,
            single_price=Decimal("55.00"),
            double_price=Decimal("95.00"),
            is_active=True,
            is_default=True,
        )
        self.product = Product.objects.create(
            name="Standard Business Card",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            default_bleed_mm=3,
            default_sides=Sides.DUPLEX,
            min_quantity=100,
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.owner,
            customer_name="Jane",
            customer_email="jane@test.com",
            status="DRAFT",
        )
        self.item = QuoteItem.objects.create(
            quote_request=self.quote_request,
            item_type="PRODUCT",
            product=self.product,
            quantity=40,
            pricing_mode="SHEET",
            paper=self.paper,
            machine=self.machine,
            sides="DUPLEX",
            color_mode="COLOR",
        )

    def _fallback_item(self):
        # The engine is skipped when machine/sides/color_mode are absent.
        return QuoteItem.objects.create(
            quote_request=self.quote_request,
            item_type="PRODUCT",
            product=self.product,
            quantity=40,
            pricing_mode="SHEET",
            paper=self.paper,
        )

    def test_canonical_cap_is_2_good_sheets_billed_as_3(self):
        raw = calculate_billable_sheets(quantity=40, yield_per_sheet=21)
        self.assertEqual(raw["total_sheets_needed"], 5)
        self.assertEqual(raw["waste_sheets_added"], 3)
        self.assertEqual(raw["billable_sheets"], 3)
        self.assertTrue(raw["spoilage_capped"])

    def test_engine_path_exposes_capped_billable_and_good_sheets_required(self):
        result = compute_quote_item_pricing(self.item)
        self.assertTrue(result.can_calculate, result.reason)
        self.assertEqual(result.copies_per_sheet, 21)
        self.assertEqual(result.sheets_needed, 2)
        self.assertEqual(result.billable_sheets, 3)
        imposition = result.breakdown.get("imposition") or {}
        self.assertEqual(imposition.get("good_sheets"), 2)
        self.assertEqual(imposition.get("billable_sheets"), 3)
        self.assertEqual(imposition.get("production_sheets"), 5)
        self.assertEqual(imposition.get("waste_sheets_added"), 3)

    def test_engine_path_metadata_contract_sheets_required_equals_good_sheets(self):
        result = compute_quote_item_pricing(self.item)
        metadata = result.calculation_result["metadata"]["imposition"]
        self.assertEqual(metadata["sheets_required"], metadata["good_sheets"])
        self.assertEqual(metadata["good_sheets"], 2)
        self.assertEqual(metadata["billable_sheets"], 3)
        self.assertNotEqual(metadata["sheets_required"], 5)

    def test_paper_and_printing_charged_on_capped_billable_count(self):
        result = compute_quote_item_pricing(self.item)
        paper_line = next(
            line
            for line in result.calculation_result["line_items"]
            if line["code"] == "paper"
        )
        self.assertIn("3 sheets", paper_line["formula"])
        self.assertNotIn("2 sheets", paper_line["formula"])
        self.assertNotIn("5 sheets", paper_line["formula"])
        self.assertGreater(Decimal(result.print_cost), 0)

    def test_fallback_path_bills_paper_on_capped_billable_count(self):
        item = self._fallback_item()
        result = compute_quote_item_pricing(item)
        self.assertEqual(result.billable_sheets, 3)
        # paper.selling_price x billable(3), NOT good(2) nor physical(5).
        self.assertEqual(Decimal(result.paper_cost), Decimal("114.00"))
        self.assertNotEqual(Decimal(result.paper_cost), Decimal("190.00"))
        self.assertIn("3 sheet(s)", result.calculation_description)
        imposition = result.breakdown.get("imposition") or {}
        self.assertEqual(imposition.get("good_sheets"), 2)
        self.assertEqual(imposition.get("billable_sheets"), 3)
        self.assertEqual(imposition.get("production_sheets"), 5)
        self.assertEqual(imposition.get("waste_sheets_added"), 3)
        metadata = result.calculation_result["metadata"]["imposition"]
        self.assertEqual(metadata["sheets_required"], metadata["good_sheets"])
        self.assertEqual(metadata["good_sheets"], 2)
        self.assertEqual(metadata["billable_sheets"], 3)

    def test_fallback_path_never_bills_the_physical_production_count(self):
        item = self._fallback_item()
        result = compute_quote_item_pricing(item)
        breakdown = result.breakdown.get("imposition") or {}
        self.assertEqual(breakdown.get("production_sheets"), 5)
        self.assertNotEqual(breakdown.get("production_sheets"), result.billable_sheets)
        self.assertNotEqual(Decimal(result.paper_cost), Decimal("190.00"))

    def test_finishing_basis_stays_on_good_sheets_while_paper_uses_billable(self):
        FinishingRate.objects.create(
            shop=self.shop,
            name="Per-sheet finishing",
            slug="per-sheet-finishing",
            charge_unit=ChargeUnit.PER_SHEET,
            billing_basis=FinishingBillingBasis.PER_SHEET,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("10.00"),
        )
        fr = FinishingRate.objects.get(slug="per-sheet-finishing")
        QuoteItemFinishing.objects.create(
            quote_item=self.item,
            finishing_rate=fr,
            apply_to_sides="BOTH",
        )
        result = compute_quote_item_pricing(self.item)
        # Finishing processes only the good output: 2 good sheets x 10.
        self.assertEqual(Decimal(result.finishing_total), Decimal("20.00"))
        # Paper + printing still use the capped billable count: 3 sheets.
        self.assertEqual(result.billable_sheets, 3)
        paper_line = next(
            line
            for line in result.calculation_result["line_items"]
            if line["code"] == "paper"
        )
        self.assertIn("3 sheets", paper_line["formula"])