"""
Tests for QuoteCalculator — deterministic pricing, KES rounding, fixtures.
"""
from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from catalog.models import Product
from inventory.choices import SheetSize
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PrintingRate
from shops.models import Shop

from .pricing.quote_builder import build_quote_preview
from .pricing.engine import calculate_sheet_pricing
from .quote_calculator import calculate_quote_item
from .pricing.finishings import compute_finishing_line


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
        self.assertIn("utilization_ratio", result.imposition)
        self.assertIn("waste_area_mm2", result.imposition)

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

    def test_lamination_uses_good_sheets_rate_side_count_formula(self):
        finishing = FinishingRate.objects.create(
            shop=self.shop,
            name="Lamination",
            charge_unit=ChargeUnit.PER_SHEET,
            billing_basis=FinishingBillingBasis.PER_SHEET,
            side_mode=FinishingSideMode.PER_SELECTED_SIDE,
            price=Decimal("12.00"),
            double_side_price=Decimal("20.00"),
            is_active=True,
        )

        line = compute_finishing_line(
            finishing,
            quantity=100,
            good_sheets=10,
            selected_side="both",
        )

        self.assertEqual(Decimal(line.total), Decimal("200.00"))
        self.assertEqual(line.rate, "20.00")
        self.assertEqual(line.good_sheets, 10)
        self.assertEqual(line.side_count, 2)
        self.assertEqual(line.formula, "good_sheets x both_side_rate")
        self.assertEqual(line.calculation_basis, "10 x 20.00")
        self.assertEqual(line.explanation, "Lamination: 10 sheets x 20.00 both-side rate")

    def test_round_corner_per_piece_ignores_side_selection(self):
        finishing = FinishingRate.objects.create(
            shop=self.shop,
            name="Round Corner",
            charge_unit=ChargeUnit.PER_PIECE,
            billing_basis=FinishingBillingBasis.PER_PIECE,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("2.50"),
            is_active=True,
        )

        line = compute_finishing_line(
            finishing,
            quantity=100,
            good_sheets=10,
            selected_side="both",
        )

        self.assertEqual(Decimal(line.total), Decimal("250.00"))
        self.assertEqual(line.units, "100")

    def test_lamination_front_only_counts_as_one_side(self):
        finishing = FinishingRate.objects.create(
            shop=self.shop,
            name="Gloss Lamination",
            charge_unit=ChargeUnit.PER_SHEET,
            billing_basis=FinishingBillingBasis.PER_SHEET,
            side_mode=FinishingSideMode.PER_SELECTED_SIDE,
            price=Decimal("12.00"),
            is_active=True,
        )

        line = compute_finishing_line(
            finishing,
            quantity=100,
            good_sheets=10,
            selected_side="front",
        )

        self.assertEqual(Decimal(line.total), Decimal("120.00"))
        self.assertEqual(line.side_count, 1)
        self.assertEqual(line.explanation, "Gloss Lamination: 10 sheets x 12.00 x 1 side")

    def test_lamination_both_sides_falls_back_to_double_single_rate_without_override(self):
        finishing = FinishingRate.objects.create(
            shop=self.shop,
            name="Gloss Lamination",
            charge_unit=ChargeUnit.PER_SHEET,
            billing_basis=FinishingBillingBasis.PER_SHEET,
            side_mode=FinishingSideMode.PER_SELECTED_SIDE,
            price=Decimal("12.00"),
            is_active=True,
        )

        line = compute_finishing_line(
            finishing,
            quantity=100,
            good_sheets=10,
            selected_side="both",
        )

        self.assertEqual(Decimal(line.total), Decimal("240.00"))
        self.assertEqual(line.formula, "good_sheets x rate x side_count")
        self.assertEqual(line.calculation_basis, "10 x 12.00 x 2")
        self.assertEqual(line.explanation, "Gloss Lamination: 10 sheets x 12.00 x 2 sides")

    def test_cutting_flat_per_line_uses_line_quantity(self):
        finishing = FinishingRate.objects.create(
            shop=self.shop,
            name="Cutting",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_LINE,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )

        line = compute_finishing_line(
            finishing,
            quantity=100,
            good_sheets=10,
            line_quantity=3,
        )

        self.assertEqual(Decimal(line.total), Decimal("150.00"))
        self.assertEqual(line.units, "3")

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

    def test_backend_preview_explains_missing_printing_rate(self):
        PrintingRate.objects.all().delete()

        preview = build_quote_preview(
            shop=self.shop,
            product=self.product,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="SIMPLEX",
            finishing_selections=[],
        )

        self.assertFalse(preview["can_calculate"])
        self.assertFalse(preview["totals"])
        self.assertIn("No active printing rate matches", preview["reason"])

    def test_sheet_pricing_uses_per_side_plus_duplex_surcharge_rule(self):
        PrintingRate.objects.all().delete()
        self.paper.selling_price = Decimal("10.00")
        self.paper.gsm = 250
        self.paper.save(update_fields=["selling_price", "gsm"])
        PrintingRate.objects.create(
            machine=self.machine,
            sheet_size=SheetSize.SRA3,
            color_mode="COLOR",
            single_price=Decimal("15.00"),
            double_price=None,
            duplex_surcharge=Decimal("5.00"),
            duplex_surcharge_enabled=True,
            duplex_surcharge_min_gsm=150,
            is_active=True,
        )

        result = calculate_sheet_pricing(
            shop=self.shop,
            product=self.product,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="DUPLEX",
        )

        self.assertEqual(result.breakdown["paper"]["paper_price_per_sheet"], "10.00")
        self.assertEqual(result.breakdown["printing"]["print_price_front"], "15.00")
        self.assertEqual(result.breakdown["printing"]["print_price_back"], "15.00")
        self.assertEqual(result.breakdown["printing"]["duplex_surcharge"], "5.00")
        self.assertEqual(result.breakdown["printing"]["total_per_sheet"], "35.00")
        self.assertEqual(result.breakdown["per_sheet_pricing"]["paper_price"], "10.00")
        self.assertEqual(result.breakdown["per_sheet_pricing"]["total_per_sheet"], "45.00")
        self.assertEqual(
            result.breakdown["per_sheet_pricing"]["formula"],
            "paper_price + print_price_front + print_price_back + duplex_surcharge",
        )
        self.assertIn("paper + 15.00 front + 15.00 back + 5.00 duplex surcharge", result.breakdown["per_sheet_pricing"]["explanation"])
        self.assertEqual(result.totals["total_per_sheet"], "45.00")

    def test_sheet_pricing_can_force_duplex_surcharge_off_or_on(self):
        PrintingRate.objects.all().delete()
        self.paper.selling_price = Decimal("5.00")
        self.paper.gsm = 130
        self.paper.save(update_fields=["selling_price", "gsm"])
        PrintingRate.objects.create(
            machine=self.machine,
            sheet_size=SheetSize.SRA3,
            color_mode="COLOR",
            single_price=Decimal("15.00"),
            double_price=None,
            duplex_surcharge=Decimal("5.00"),
            duplex_surcharge_enabled=True,
            duplex_surcharge_min_gsm=150,
            is_active=True,
        )

        auto_result = calculate_sheet_pricing(
            shop=self.shop,
            product=self.product,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="DUPLEX",
        )
        forced_result = calculate_sheet_pricing(
            shop=self.shop,
            product=self.product,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="DUPLEX",
            apply_duplex_surcharge=True,
        )

        self.assertEqual(auto_result.breakdown["printing"]["duplex_surcharge"], "0.00")
        self.assertEqual(auto_result.totals["total_per_sheet"], "35.00")
        self.assertEqual(
            auto_result.breakdown["per_sheet_pricing"]["formula"],
            "paper_price + print_price_front + print_price_back",
        )
        self.assertEqual(forced_result.breakdown["printing"]["duplex_surcharge"], "5.00")
        self.assertEqual(forced_result.totals["total_per_sheet"], "40.00")
