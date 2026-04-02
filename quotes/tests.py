"""Tests for quotes app."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from catalog.choices import PricingMode
from catalog.imposition import pieces_per_sheet, sheets_needed
from catalog.models import Product
from inventory.choices import SheetSize
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, ColorMode, Sides
from pricing.models import FinishingRate, Material, PrintingRate
from quotes.choices import QuoteStatus
from quotes.models import QuoteItem, QuoteItemFinishing, QuoteRequest
from catalog.services import product_price_hint
from quotes.services import (
    build_preview_price_response,
    calculate_quote_item,
    calculate_quote_request,
)
from quotes.pricing_service import compute_and_store_pricing
from shops.models import Shop

User = get_user_model()


class QuoteEngineTests(TestCase):
    """Quote engine calculation tests."""

    def setUp(self):
        self.user = User.objects.create_user(email="s@test.com", password="pass")
        self.shop = Shop.objects.create(
            owner=self.user, name="Test Shop", slug="test-shop", is_active=True
        )
        self.product_sheet = Product.objects.create(
            shop=self.shop,
            name="Flyer",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=210,
            default_finished_height_mm=297,
            default_sides=Sides.SIMPLEX,
        )
        self.product_lf = Product.objects.create(
            shop=self.shop,
            name="Banner",
            pricing_mode=PricingMode.LARGE_FORMAT,
            default_finished_width_mm=1000,
            default_finished_height_mm=500,
            default_sides=Sides.SIMPLEX,
        )
        self.paper = Paper.objects.create(
            shop=self.shop,
            sheet_size=SheetSize.A4,
            gsm=80,
            paper_type="COATED",
            buying_price=Decimal("0.05"),
            selling_price=Decimal("0.10"),
            width_mm=210,
            height_mm=297,
        )
        self.machine = Machine.objects.create(
            shop=self.shop,
            name="Digital Press",
            machine_type="DIGITAL",
            max_width_mm=297,
            max_height_mm=420,
        )
        PrintingRate.objects.create(
            machine=self.machine,
            sheet_size=SheetSize.A4,
            color_mode=ColorMode.COLOR,
            single_price=Decimal("0.15"),
            double_price=Decimal("0.25"),
        )
        self.material = Material.objects.create(
            shop=self.shop,
            material_type="Vinyl",
            buying_price=Decimal("5.00"),
            selling_price=Decimal("12.00"),
        )
        self.finishing_per_piece = FinishingRate.objects.create(
            shop=self.shop,
            name="Lamination",
            charge_unit=ChargeUnit.PER_PIECE,
            price=Decimal("0.20"),
        )
        self.finishing_flat = FinishingRate.objects.create(
            shop=self.shop,
            name="Setup",
            charge_unit=ChargeUnit.FLAT,
            price=Decimal("25.00"),
            setup_fee=Decimal("5.00"),
        )

    def test_sheet_mode_basic(self):
        """SHEET mode: paper cost + printing cost."""
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="John",
            customer_email="john@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_sheet,
            quantity=100,
            pricing_mode=PricingMode.SHEET,
            paper=self.paper,
            machine=self.machine,
            sides=Sides.SIMPLEX,
            color_mode=ColorMode.COLOR,
        )
        unit_price, line_total = calculate_quote_item(item)
        # Paper: 0.10 * 100 = 10, Printing: 0.15 * 100 = 15, Total = 25
        self.assertEqual(unit_price, Decimal("0.25"))
        self.assertEqual(line_total, Decimal("25.00"))

    def test_sheet_mode_with_finishing_per_piece(self):
        """SHEET mode with PER_PIECE finishing."""
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="John",
            customer_email="john@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_sheet,
            quantity=50,
            pricing_mode=PricingMode.SHEET,
            paper=self.paper,
            machine=self.machine,
            sides=Sides.SIMPLEX,
            color_mode=ColorMode.COLOR,
        )
        QuoteItemFinishing.objects.create(
            quote_item=item,
            finishing_rate=self.finishing_per_piece,
        )
        unit_price, line_total = calculate_quote_item(item)
        # Paper: 0.10*50=5, Print: 0.15*50=7.5, Finishing: 0.20*50=10, Total=22.50
        self.assertEqual(line_total, Decimal("22.50"))

    def test_sheet_mode_with_finishing_flat(self):
        """SHEET mode with FLAT finishing (price + setup_fee)."""
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="John",
            customer_email="john@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_sheet,
            quantity=10,
            pricing_mode=PricingMode.SHEET,
            paper=self.paper,
        )
        QuoteItemFinishing.objects.create(
            quote_item=item,
            finishing_rate=self.finishing_flat,
        )
        unit_price, line_total = calculate_quote_item(item)
        # Paper: 0.10*10=1, Finishing: 25+5=30, Total=31
        self.assertEqual(line_total, Decimal("31.00"))

    def test_large_format_mode(self):
        """LARGE_FORMAT: material.selling_price * area_sqm."""
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="John",
            customer_email="john@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_lf,
            quantity=2,
            pricing_mode=PricingMode.LARGE_FORMAT,
            material=self.material,
            chosen_width_mm=1000,
            chosen_height_mm=500,
        )
        unit_price, line_total = calculate_quote_item(item)
        # area_sqm = (1000/1000)*(500/1000)*2 = 1.0
        # base = 12.00 * 1.0 = 12.00
        self.assertEqual(line_total, Decimal("12.00"))

    def test_pricing_locked_skips_recalc(self):
        """When pricing_locked_at is set, do not recalculate unless force=True."""
        from django.utils import timezone

        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="John",
            customer_email="john@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_sheet,
            quantity=100,
            pricing_mode=PricingMode.SHEET,
            paper=self.paper,
            machine=self.machine,
            sides=Sides.SIMPLEX,
            color_mode=ColorMode.COLOR,
            unit_price=Decimal("1.00"),
            line_total=Decimal("100.00"),
            pricing_locked_at=timezone.now(),
        )
        unit_price, line_total = calculate_quote_item(item, force=False)
        self.assertEqual(unit_price, Decimal("1.00"))
        self.assertEqual(line_total, Decimal("100.00"))

        unit_price, line_total = calculate_quote_item(item, force=True)
        self.assertEqual(unit_price, Decimal("0.25"))
        self.assertEqual(line_total, Decimal("25.00"))

    def test_imposition_14x17cm_on_sra3(self):
        """14×17cm on SRA3: 4 up, 100 qty → 25 sheets."""
        # SRA3 = 320×450mm; 14×17cm = 140×170mm; with 3mm bleed: 146×176mm
        pieces = pieces_per_sheet(
            finished_width_mm=140,
            finished_height_mm=170,
            sheet_width_mm=320,
            sheet_height_mm=450,
            bleed_mm=3,
        )
        self.assertEqual(pieces, 4, "4 pieces fit on SRA3 (2×2 grid)")
        self.assertEqual(sheets_needed(100, pieces), 25)

    def test_sheet_mode_imposition_and_per_sheet_finishing(self):
        """SHEET mode: 14×17cm on SRA3, PER_SHEET finishing, breakdown shows sheets_needed."""
        product = Product.objects.create(
            shop=self.shop,
            name="Postcard 14×17cm",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=140,
            default_finished_height_mm=170,
            default_sides=Sides.SIMPLEX,
        )
        paper_sra3 = Paper.objects.create(
            shop=self.shop,
            sheet_size=SheetSize.SRA3,
            gsm=300,
            paper_type="COATED",
            buying_price=Decimal("0.20"),
            selling_price=Decimal("0.50"),
            width_mm=320,
            height_mm=450,
        )
        Machine.objects.filter(shop=self.shop).update(max_width_mm=450, max_height_mm=640)
        PrintingRate.objects.create(
            machine=self.machine,
            sheet_size=SheetSize.SRA3,
            color_mode=ColorMode.COLOR,
            single_price=Decimal("1.00"),
            double_price=Decimal("1.50"),
        )
        finishing_per_sheet = FinishingRate.objects.create(
            shop=self.shop,
            name="Cutting",
            charge_unit=ChargeUnit.PER_SHEET,
            price=Decimal("0.10"),
        )
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="Jane",
            customer_email="jane@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=product,
            quantity=100,
            pricing_mode=PricingMode.SHEET,
            paper=paper_sra3,
            machine=self.machine,
            sides=Sides.SIMPLEX,
            color_mode=ColorMode.COLOR,
        )
        QuoteItemFinishing.objects.create(
            quote_item=item,
            finishing_rate=finishing_per_sheet,
        )
        unit_price, line_total = calculate_quote_item(item)
        # 100 qty, 4 up → 25 sheets
        # Paper: 0.50 × 25 = 12.50
        # Print: 1.00 × 25 = 25.00
        # Finishing PER_SHEET: 0.10 × 25 = 2.50
        # Total = 40.00
        self.assertEqual(line_total, Decimal("40.00"))
        # Preview breakdown should mention sheets
        resp = build_preview_price_response(qr)
        labels = [ln["label"] for ln in resp["lines"]]
        self.assertTrue(
            any("Sheets:" in lbl for lbl in labels),
            f"Breakdown should mention sheets_needed: {labels}",
        )

    def test_per_side_per_sheet_lamination(self):
        """PER_SIDE_PER_SHEET: lamination = price × sheets × sides (e.g. 100 bcards 10-up duplex)."""
        product = Product.objects.create(
            shop=self.shop,
            name="Business Card 90×50",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=90,
            default_finished_height_mm=50,
            default_sides=Sides.DUPLEX,
        )
        paper_sra3 = Paper.objects.create(
            shop=self.shop,
            sheet_size=SheetSize.SRA3,
            gsm=300,
            paper_type="COATED",
            buying_price=Decimal("0.20"),
            selling_price=Decimal("0.50"),
            width_mm=320,
            height_mm=450,
        )
        Machine.objects.filter(shop=self.shop).update(max_width_mm=450, max_height_mm=640)
        PrintingRate.objects.create(
            machine=self.machine,
            sheet_size=SheetSize.SRA3,
            color_mode=ColorMode.COLOR,
            single_price=Decimal("1.00"),
            double_price=Decimal("1.50"),
        )
        lamination = FinishingRate.objects.create(
            shop=self.shop,
            name="Lamination",
            charge_unit=ChargeUnit.PER_SIDE_PER_SHEET,
            price=Decimal("12.50"),
        )
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="Jane",
            customer_email="jane@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=product,
            quantity=100,
            pricing_mode=PricingMode.SHEET,
            paper=paper_sra3,
            machine=self.machine,
            sides=Sides.DUPLEX,
            color_mode=ColorMode.COLOR,
        )
        QuoteItemFinishing.objects.create(
            quote_item=item,
            finishing_rate=lamination,
        )
        unit_price, line_total = calculate_quote_item(item)
        # 100 qty, 24 up (90×50 on SRA3) → 5 sheets
        # Paper: 0.50 × 5 = 2.50
        # Print: 1.50 × 5 = 7.50
        # Lamination PER_SIDE_PER_SHEET: 12.50 × 5 × 2 = 125.00
        # Total = 135.00
        self.assertEqual(line_total, Decimal("135.00"))

    def test_preview_price_standardized_response(self):
        """Preview-price always returns can_calculate, total, lines, needs_review_items, missing_fields, reason."""
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="Test",
            customer_email="test@test.com",
            status="DRAFT",
        )
        resp = build_preview_price_response(qr)
        for key in ("can_calculate", "total", "lines", "needs_review_items", "missing_fields", "reason"):
            self.assertIn(key, resp, f"Response must include {key}")
        self.assertIn("item_explanations", resp)
        self.assertIn("item_calculations", resp)
        self.assertEqual(resp["total"], 0)
        self.assertEqual(resp["lines"], [])
        self.assertEqual(resp["needs_review_items"], [])
        self.assertEqual(resp["missing_fields"], [])
        self.assertIn("items_missing_fields", resp)
        self.assertEqual(resp["items_missing_fields"], {})

    def test_preview_price_item_level_missing_fields(self):
        """Items needing review have item-level missing_fields in items_missing_fields."""
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="Test",
            customer_email="test@test.com",
            status="DRAFT",
        )
        # Item without paper (SHEET product needs paper)
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_sheet,
            quantity=100,
            pricing_mode=PricingMode.SHEET,
            machine=self.machine,
            sides=Sides.SIMPLEX,
            color_mode=ColorMode.COLOR,
        )
        resp = build_preview_price_response(qr)
        self.assertFalse(resp["can_calculate"])
        self.assertIn(item.id, resp["needs_review_items"])
        self.assertIn("paper", resp["missing_fields"])
        self.assertIn(str(item.id), resp["items_missing_fields"])
        self.assertIn("paper", resp["items_missing_fields"][str(item.id)])
        # Actionable diagnostics
        self.assertIn("suggestions", resp)
        codes = [s["code"] for s in resp["suggestions"]]
        self.assertIn("ADD_PAPER", codes)
        self.assertIn("item_diagnostics", resp)
        self.assertIn(str(item.id), resp["item_diagnostics"])
        item_diag = resp["item_diagnostics"][str(item.id)]
        self.assertIn("suggestions", item_diag)
        self.assertIn("reason", item_diag)

    def test_diagnostics_missing_printing_rate(self):
        """Missing printing rate -> ADD_PRINTING_RATE suggestion."""
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="Test",
            customer_email="test@test.com",
            status="DRAFT",
        )
        # Paper without PrintingRate for this machine/sheet/color
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_sheet,
            quantity=100,
            pricing_mode=PricingMode.SHEET,
            paper=self.paper,
            machine=self.machine,
            sides=Sides.SIMPLEX,
            color_mode=ColorMode.BW,  # No BW rate created
        )
        resp = build_preview_price_response(qr)
        codes = [s["code"] for s in resp["suggestions"]]
        self.assertIn("ADD_PRINTING_RATE", codes)
        add_rate = next(s for s in resp["suggestions"] if s["code"] == "ADD_PRINTING_RATE")
        self.assertIn("Digital Press", add_rate["message"])
        self.assertIn("A4", add_rate["message"])

    def test_diagnostics_missing_dimensions(self):
        """Missing dimensions (LARGE_FORMAT) -> ADD_DIMENSIONS suggestion."""
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="Test",
            customer_email="test@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_lf,
            quantity=2,
            pricing_mode=PricingMode.LARGE_FORMAT,
            material=self.material,
            # chosen_width_mm, chosen_height_mm missing
        )
        resp = build_preview_price_response(qr)
        codes = [s["code"] for s in resp["suggestions"]]
        self.assertIn("ADD_DIMENSIONS", codes)

    def test_product_price_hint_includes_diagnostics(self):
        """Product price hint includes suggestions when cannot calculate."""
        # Product in shop with no papers
        shop_no_paper = Shop.objects.create(
            owner=self.user, name="Empty Shop", slug="empty-shop", is_active=True
        )
        product = Product.objects.create(
            shop=shop_no_paper,
            name="Flyer",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=210,
            default_finished_height_mm=297,
            default_sides=Sides.SIMPLEX,
        )
        hint = product_price_hint(product)
        self.assertFalse(hint["can_calculate"])
        self.assertIn("paper", hint["missing_fields"])
        self.assertIn("suggestions", hint)
        codes = [s["code"] for s in hint["suggestions"]]
        self.assertIn("ADD_PAPER", codes)

    def test_calculate_quote_request_lock(self):
        """calculate_quote_request with lock=True persists prices."""
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="John",
            customer_email="john@test.com",
            status="SUBMITTED",
        )
        QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_sheet,
            quantity=100,
            pricing_mode=PricingMode.SHEET,
            paper=self.paper,
            machine=self.machine,
            sides=Sides.SIMPLEX,
            color_mode=ColorMode.COLOR,
        )
        total = calculate_quote_request(qr, lock=True)
        qr.refresh_from_db()
        self.assertEqual(qr.status, QuoteStatus.QUOTED)
        self.assertEqual(total, Decimal("25.00"))
        item = qr.items.first()
        self.assertIsNotNone(item.pricing_locked_at)
        self.assertEqual(item.unit_price, Decimal("0.25"))
        self.assertEqual(item.line_total, Decimal("25.00"))

    def test_compute_and_store_pricing_snapshot_includes_engine_layout(self):
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="Engine Snapshot",
            customer_email="engine@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_sheet,
            quantity=100,
            pricing_mode=PricingMode.SHEET,
            paper=self.paper,
            machine=self.machine,
            sides=Sides.SIMPLEX,
            color_mode=ColorMode.COLOR,
        )

        result = compute_and_store_pricing(item)
        item.refresh_from_db()

        self.assertTrue(result.can_calculate)
        self.assertEqual(item.pricing_snapshot["engine_type"], "flat_sheet")
        self.assertIn("layout_result", item.pricing_snapshot)
        self.assertIn("finishing_plan", item.pricing_snapshot)
        self.assertIn("explanations", item.pricing_snapshot)
        self.assertIn("calculation_description", item.pricing_snapshot)
        self.assertIn("calculation_result", item.pricing_snapshot)
        self.assertEqual(item.pricing_snapshot["calculation_result"]["quote_type"], "flat")
        self.assertEqual(
            item.pricing_snapshot["calculation_result"]["grand_total"],
            item.pricing_snapshot["line_total"],
        )

    def test_preview_price_response_includes_item_explanations(self):
        qr = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="Explain",
            customer_email="explain@test.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=qr,
            product=self.product_sheet,
            quantity=100,
            pricing_mode=PricingMode.SHEET,
            paper=self.paper,
            machine=self.machine,
            sides=Sides.SIMPLEX,
            color_mode=ColorMode.COLOR,
        )

        resp = build_preview_price_response(qr)

        self.assertIn(str(item.id), resp["item_explanations"])
        self.assertIn(str(item.id), resp["item_calculations"])
        self.assertTrue(resp["item_explanations"][str(item.id)])
        self.assertIn("Sheet job", resp["item_calculations"][str(item.id)])
        self.assertIn("calculation_result", resp)
        self.assertIn("item_calculation_results", resp)
        self.assertEqual(resp["item_calculation_results"][str(item.id)]["quote_type"], "flat")
        self.assertEqual(resp["calculation_result"]["quote_type"], "quote_request_preview")
        self.assertEqual(resp["calculation_result"]["grand_total"], "25.00")
