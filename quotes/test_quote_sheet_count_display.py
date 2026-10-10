"""Regression guard: the sheet count shown on a quote must match the count that
paper and printing are actually charged on.

History: the engine bills paper + printing on the capped *billable* sheet count
(good sheets + spoilage, clamped by ``WastePolicy.maximum_spoilage_rate``), but
the quote description and the paper line-item formula printed the theoretical
*good* sheet count. A 2-sheet business-card job is billed on 3 sheets, yet the
quote said "2 sheets x price", so the displayed count disagreed with the money.

These tests pin the displayed count to ``PricingResult.billable_sheets``.
"""

from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from catalog.choices import PricingMode
from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.choices import ColorMode, Sides
from pricing.models import PrintingRate, WastePolicy
from quotes.models import QuoteItem, QuoteItemService, QuoteRequest
from quotes.pricing_service import compute_quote_item_pricing
from quotes.services import _build_item_breakdown_lines
from quotes.summary import build_quote_item_summary, summary_to_breakdown_lines
from shops.models import Shop


class QuoteSheetCountDisplayTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="sheet-count@test.com", password="pass12345")
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Sheet Count Shop",
            slug="sheet-count-shop",
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

    def test_billable_sheets_reflect_capped_charge_count(self):
        result = compute_quote_item_pricing(self.item)
        self.assertTrue(result.can_calculate, result.reason)
        self.assertEqual(result.copies_per_sheet, 21)
        # 2 good sheets + 2 fixed + 1 variable = 5 physical, capped at
        # 2 + ceil(2 * 0.5) = 3 billable.
        self.assertEqual(result.sheets_needed, 2)
        self.assertEqual(result.billable_sheets, 3)

    def test_calculation_description_uses_billed_sheet_count(self):
        result = compute_quote_item_pricing(self.item)
        self.assertIn("3 sheet(s)", result.calculation_description)
        self.assertNotIn("2 sheet(s)", result.calculation_description)

    def test_paper_formula_matches_billed_sheet_count(self):
        result = compute_quote_item_pricing(self.item)
        paper_line = next(
            line
            for line in result.calculation_result["line_items"]
            if line["code"] == "paper"
        )
        self.assertIn("3 sheets", paper_line["formula"])

    def test_summary_breakdown_displays_billed_sheet_count(self):
        summary = build_quote_item_summary(self.item)
        self.assertEqual(summary.billable_sheets, 3)
        labels = [line["label"] for line in summary_to_breakdown_lines(summary)]
        self.assertTrue(
            any("Sheets: 3" in label and "21 up" in label for label in labels),
            labels,
        )

    def test_selected_service_override_adds_breakdown_line(self):
        QuoteItemService.objects.create(
            quote_item=self.item,
            is_selected=True,
            price_override=Decimal("500.00"),
            note="Rush fee",
        )
        lines = _build_item_breakdown_lines(self.item)
        self.assertTrue(
            any("Service: Rush fee" in line["label"] for line in lines),
            lines,
        )
        self.assertTrue(
            any(line["amount"] == "500" for line in lines),
            lines,
        )
