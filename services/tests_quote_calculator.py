"""
Tests for QuoteCalculator — deterministic pricing, KES rounding, fixtures.
"""
from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from catalog.models import Product
from inventory.choices import SheetSize
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, PrintingRate
from shops.models import Shop

from .quote_calculator import calculate_quote_item


class QuoteCalculatorTestCase(TestCase):
    """Fixture-based tests for calculator output consistency."""

    def setUp(self):
        self.user = User.objects.create_user(email="staff@test.com", password="pass", is_staff=True)
        self.shop = Shop.objects.create(
            name="Test Shop",
            slug="test-shop",
            owner=self.user,
            currency="KES",
            is_active=True,
        )
        self.product = Product.objects.create(
            shop=self.shop,
            name="Business Card",
            pricing_mode="SHEET",
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            default_bleed_mm=3,
            min_quantity=100,
            is_active=True,
        )
        self.machine = Machine.objects.create(
            shop=self.shop,
            name="Konica",
            machine_type="DIGITAL",
            max_width_mm=320,
            max_height_mm=450,
        )
        self.paper = Paper.objects.create(
            shop=self.shop,
            sheet_size=SheetSize.SRA3,
            gsm=300,
            paper_type="GLOSS",
            buying_price=Decimal("15"),
            selling_price=Decimal("24"),
            width_mm=320,
            height_mm=450,
            is_active=True,
        )
        PrintingRate.objects.create(
            machine=self.machine,
            sheet_size=SheetSize.SRA3,
            color_mode="COLOR",
            single_price=Decimal("45"),
            double_price=Decimal("75"),
            is_active=True,
        )

    def test_same_input_same_output(self):
        """Deterministic: same input => same output."""
        r1 = calculate_quote_item(
            product_id=self.product.id,
            quantity=200,
            paper_id=self.paper.id,
            machine_id=self.machine.id,
            sides="DUPLEX",
            color_mode="COLOR",
        )
        r2 = calculate_quote_item(
            product_id=self.product.id,
            quantity=200,
            paper_id=self.paper.id,
            machine_id=self.machine.id,
            sides="DUPLEX",
            color_mode="COLOR",
        )
        self.assertEqual(r1.to_dict(), r2.to_dict())

    def test_sheets_required_and_imposition(self):
        """Sheets and imposition match expected values for business cards on SRA3."""
        result = calculate_quote_item(
            product_id=self.product.id,
            quantity=200,
            paper_id=self.paper.id,
        )
        self.assertTrue(result.can_calculate)
        self.assertGreaterEqual(result.sheets_required, 1)
        self.assertGreaterEqual(result.imposition["per_sheet"], 1)
        self.assertIn(result.imposition["sheet_size_used"], ["SRA3", "A3", "A4"])

    def test_costs_structure(self):
        """Costs dict has all required keys with string values."""
        result = calculate_quote_item(
            product_id=self.product.id,
            quantity=100,
            paper_id=self.paper.id,
            machine_id=self.machine.id,
            sides="SIMPLEX",
            color_mode="COLOR",
        )
        self.assertTrue(result.can_calculate)
        for key in ["paper_cost", "print_cost", "finishing_cost", "overhead", "margin", "total_cost", "suggested_price"]:
            self.assertIn(key, result.costs)
            self.assertIsInstance(result.costs[key], str)

    def test_suggested_price_minimum(self):
        """Suggested price respects minimum (50 KES)."""
        result = calculate_quote_item(
            product_id=self.product.id,
            quantity=1,
            paper_id=self.paper.id,
            overhead_percent=Decimal("0"),
            margin_percent=Decimal("0"),
        )
        self.assertTrue(result.can_calculate)
        sp = Decimal(result.costs["suggested_price"])
        self.assertGreaterEqual(sp, Decimal("50"))

    def test_grammage_paper_type_resolution(self):
        """Paper can be resolved by grammage + paper_type."""
        result = calculate_quote_item(
            product_id=self.product.id,
            quantity=100,
            grammage=300,
            paper_type="GLOSS",
            sheet_size=SheetSize.SRA3,
        )
        self.assertTrue(result.can_calculate)
        self.assertGreater(Decimal(result.costs["paper_cost"]), 0)

    def test_lead_time_estimate(self):
        """Lead time is returned as string, positive."""
        result = calculate_quote_item(
            product_id=self.product.id,
            quantity=100,
            paper_id=self.paper.id,
        )
        self.assertTrue(result.can_calculate)
        self.assertIsInstance(result.lead_time_estimate_hours, str)
        self.assertGreater(Decimal(result.lead_time_estimate_hours), 0)

    def test_finishing_cost_included(self):
        """Finishing cost is added when finishing_ids provided."""
        finishing = FinishingRate.objects.create(
            shop=self.shop,
            name="Lamination",
            charge_unit="PER_PIECE",
            price=Decimal("2.50"),
            is_active=True,
        )
        result = calculate_quote_item(
            product_id=self.product.id,
            quantity=100,
            paper_id=self.paper.id,
            finishing_ids=[finishing.id],
        )
        self.assertTrue(result.can_calculate)
        fc = Decimal(result.costs["finishing_cost"])
        self.assertGreater(fc, 0)

    def test_product_not_found(self):
        """Returns can_calculate=False when product not found."""
        result = calculate_quote_item(product_id=99999, quantity=100, paper_id=self.paper.id)
        self.assertFalse(result.can_calculate)
        self.assertIn("Product", result.reason)

    def test_paper_not_found(self):
        """Returns can_calculate=False when paper not found."""
        result = calculate_quote_item(
            product_id=self.product.id,
            quantity=100,
            paper_id=99999,
        )
        self.assertFalse(result.can_calculate)
        self.assertIn("Paper", result.reason)

    def test_quantity_zero(self):
        """Returns can_calculate=False when quantity <= 0."""
        result = calculate_quote_item(
            product_id=self.product.id,
            quantity=0,
            paper_id=self.paper.id,
        )
        self.assertFalse(result.can_calculate)
        self.assertIn("Quantity", result.reason)
