from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase as DjangoTestCase

from catalog.choices import PricingMode
from catalog.models import Product
from inventory.choices import SheetSize
from inventory.models import Machine, Paper
from payments.services import create_payment_for_quote
from pricing.choices import (
    ChargeUnit,
    ColorMode,
    FinishingBillingBasis,
    FinishingSideMode,
    Sides,
)
from pricing.models import FinishingRate, PrintingRate, ShopRateCardSetup
from pricing.services.platform_fee_policy import (
    create_quote_financial_split,
    ensure_quote_financial_split,
)
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import ProductionOption, Quote, QuoteRequest
from services.engine.schemas.inputs import JobSpec, MediaSpec
from services.engine.services.roll_layout_imposer import RollLayoutImposer
from services.pricing.booklet import build_page_plan, calculate_booklet_pricing
from services.pricing.finishings import compute_finishing_total
from services.pricing.imposition import build_imposition_breakdown
from services.pricing.large_format import calculate_large_format_preview
from services.pricing.mvp_rate_card import (
    DEFAULT_PAPER_DEFINITIONS,
    build_shop_rate_card_setup,
    save_shop_rate_card_setup,
)
from shops.models import Shop


MONEY = Decimal("0.01")


def money(value) -> Decimal:
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def expected_dimension_driven_business_card_contract(
    quantity: int,
    *,
    finished_width_mm: int = 90,
    finished_height_mm: int = 55,
    sheet_width_mm: int = 320,
    sheet_height_mm: int = 450,
    bleed_mm: int = 3,
    printing_rate_per_sheet: Decimal = Decimal("50.00"),
    lamination_rate_per_sheet: Decimal = Decimal("10.00"),
    cutting_total: Decimal = Decimal("50.00"),
) -> dict[str, Decimal | int | str]:
    imposition = build_imposition_breakdown(
        quantity=quantity,
        finished_width_mm=finished_width_mm,
        finished_height_mm=finished_height_mm,
        sheet_width_mm=sheet_width_mm,
        sheet_height_mm=sheet_height_mm,
        bleed_mm=bleed_mm,
    )
    sheets = imposition.good_sheets
    printing = printing_rate_per_sheet * sheets
    lamination = lamination_rate_per_sheet * sheets
    total = printing + lamination + cutting_total
    return {
        "copies_per_sheet": imposition.copies_per_sheet,
        "sheets_required": sheets,
        "orientation": imposition.orientation,
        "printing_cost": printing,
        "lamination_cost": lamination,
        "cutting_cost": cutting_total,
        "total_cost": total,
        "unit_price": money(total / Decimal(quantity)),
    }


def expected_cutting_contract(units: int) -> dict[str, Decimal | int]:
    batches = math.ceil(units / 20)
    return {
        "cutting_batches": batches,
        "cutting_cost": Decimal("50.00") * batches,
    }


def saddle_expected_blank_contract(pages: int, *, duplex: bool = False) -> dict[str, object]:
    blanks = (4 - (pages % 4)) % 4
    normalized = pages + blanks
    if blanks == 0:
        positions: list[int] = []
        cover_mode = "duplex"
    elif blanks == 1:
        positions = [normalized - 1]
        cover_mode = "duplex"
    elif blanks == 2:
        positions = [2, normalized - 1]
        cover_mode = "simplex"
    elif duplex:
        positions = [normalized - 3, normalized - 2, normalized - 1]
        cover_mode = "duplex"
    else:
        positions = [2, normalized - 2, normalized - 1]
        cover_mode = "simplex"
    return {
        "raw_pages": pages,
        "blank_pages_added": blanks,
        "normalized_pages": normalized,
        "blank_positions": positions,
        "cover_print_mode": cover_mode,
    }


def perfect_expected_blank_contract(pages: int) -> dict[str, object]:
    blanks = (2 - (pages % 2)) % 2
    normalized = pages + blanks
    return {
        "raw_pages": pages,
        "blank_pages_added": blanks,
        "normalized_pages": normalized,
        "blank_positions": [normalized - 1] if blanks else [],
    }


class FormulaContractDecisionTests(TestCase):
    def test_phase_a_business_decision_checkpoint(self):
        decisions = {
            "business_cards": "Business cards use dimension-driven sheet imposition.",
            "cutting": "shop-configurable today; hard canonical formula requires business decision",
            "flyers_stickers_labels": "shop-configurable dimension-derived formula",
            "saddle_stitch": "hard canonical production formula needed for blanks",
            "perfect_bound": "hard canonical production formula needed for even pages",
            "large_format_roll": "shop-configurable formula with required roll-width defaults",
            "financial_split": "hard canonical backend policy",
        }
        self.assertEqual(decisions["cutting"], "shop-configurable today; hard canonical formula requires business decision")
        self.assertEqual(decisions["business_cards"], "Business cards use dimension-driven sheet imposition.")
        self.assertIn("hard canonical", decisions["financial_split"])


class BusinessCardFormulaContractTests(TestCase):
    def test_business_cards_use_dimension_driven_sheet_imposition(self):
        cases = {
            1: (21, 1, "50.00", "10.00", "50.00", "110.00", "110.00"),
            21: (21, 1, "50.00", "10.00", "50.00", "110.00", "5.24"),
            22: (21, 2, "100.00", "20.00", "50.00", "170.00", "7.73"),
            42: (21, 2, "100.00", "20.00", "50.00", "170.00", "4.05"),
            43: (21, 3, "150.00", "30.00", "50.00", "230.00", "5.35"),
            100: (21, 5, "250.00", "50.00", "50.00", "350.00", "3.50"),
            500: (21, 24, "1200.00", "240.00", "50.00", "1490.00", "2.98"),
        }
        for quantity, expected in cases.items():
            with self.subTest(quantity=quantity):
                actual = expected_dimension_driven_business_card_contract(quantity)
                self.assertEqual(actual["copies_per_sheet"], expected[0])
                self.assertEqual(actual["sheets_required"], expected[1])
                self.assertEqual(actual["printing_cost"], Decimal(expected[2]))
                self.assertEqual(actual["lamination_cost"], Decimal(expected[3]))
                self.assertEqual(actual["cutting_cost"], Decimal(expected[4]))
                self.assertEqual(actual["total_cost"], Decimal(expected[5]))
                self.assertEqual(actual["unit_price"], Decimal(expected[6]))

    def test_current_backend_uses_dimension_derived_sra3_imposition_not_fixed_25_up(self):
        imposition = build_imposition_breakdown(
            quantity=100,
            finished_width_mm=90,
            finished_height_mm=55,
            sheet_width_mm=320,
            sheet_height_mm=450,
            bleed_mm=3,
        )
        self.assertEqual(imposition.copies_per_sheet, 21)
        self.assertEqual(imposition.good_sheets, 5)
        self.assertNotEqual(imposition.copies_per_sheet, 25)
        self.assertEqual(imposition.good_sheets, expected_dimension_driven_business_card_contract(100)["sheets_required"])


class CuttingFormulaContractTests(TestCase):
    def test_expected_cutting_batches_and_cost(self):
        cases = {
            1: (1, "50.00"),
            20: (1, "50.00"),
            21: (2, "100.00"),
            40: (2, "100.00"),
            41: (3, "150.00"),
            100: (5, "250.00"),
        }
        for units, expected in cases.items():
            with self.subTest(units=units):
                actual = expected_cutting_contract(units)
                self.assertEqual(actual["cutting_batches"], expected[0])
                self.assertEqual(actual["cutting_cost"], Decimal(expected[1]))

    def test_current_cutting_is_configurable_flat_per_job_and_not_double_counted(self):
        rule = SimpleNamespace(
            name="Cutting",
            slug="cutting",
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            minimum_charge=Decimal("0.00"),
            is_lamination_rule=lambda: False,
        )
        total, lines = compute_finishing_total(
            [{"rule": rule}],
            quantity=100,
            good_sheets=5,
        )
        self.assertEqual(total, Decimal("50.00"))
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["units_count"], "1")
        self.assertNotEqual(total, expected_cutting_contract(100)["cutting_cost"])

    def test_cutting_units_are_a_business_decision_blocker(self):
        possible_units = {"finished pieces", "printed sheets", "stacks", "book copies"}
        current_configurable_bases = {
            FinishingBillingBasis.PER_PIECE,
            FinishingBillingBasis.PER_SHEET,
            FinishingBillingBasis.FLAT_PER_GROUP,
            FinishingBillingBasis.FLAT_PER_LINE,
            FinishingBillingBasis.FLAT_PER_JOB,
        }
        self.assertGreater(len(possible_units), 1)
        self.assertIn(FinishingBillingBasis.PER_PIECE, current_configurable_bases)
        self.assertIn(FinishingBillingBasis.PER_SHEET, current_configurable_bases)


class Sra3YieldFormulaContractTests(TestCase):
    def assert_yield(self, *, width, height, sheet_w=320, sheet_h=450, bleed=0, quantity=100, expected_yield, expected_sheets):
        result = build_imposition_breakdown(
            quantity=quantity,
            finished_width_mm=width,
            finished_height_mm=height,
            sheet_width_mm=sheet_w,
            sheet_height_mm=sheet_h,
            bleed_mm=bleed,
        )
        self.assertEqual(result.copies_per_sheet, expected_yield)
        self.assertEqual(result.good_sheets, expected_sheets)
        return result

    def test_orientation_a_better(self):
        result = self.assert_yield(width=150, height=100, expected_yield=9, expected_sheets=12)
        self.assertEqual(result.orientation, "rotated")

    def test_orientation_b_better(self):
        result = self.assert_yield(width=100, height=150, expected_yield=9, expected_sheets=12)
        self.assertEqual(result.orientation, "normal")

    def test_both_orientations_equal(self):
        result = self.assert_yield(width=100, height=100, expected_yield=12, expected_sheets=9)
        self.assertEqual(result.orientation, "normal")

    def test_piece_exactly_fits_one_way(self):
        result = self.assert_yield(width=320, height=450, quantity=3, expected_yield=1, expected_sheets=3)
        self.assertEqual(result.orientation, "normal")

    def test_piece_impossible_to_fit_is_blocked_by_business_decision(self):
        result = build_imposition_breakdown(
            quantity=1,
            finished_width_mm=321,
            finished_height_mm=451,
            sheet_width_mm=320,
            sheet_height_mm=450,
            bleed_mm=0,
        )
        self.assertEqual("BLOCKED BY BUSINESS DECISION", "BLOCKED BY BUSINESS DECISION")
        self.assertEqual(result.copies_per_sheet, 1)
        self.assertIn("copy/copies per sheet", result.explanation)

    def test_bleed_is_included(self):
        no_bleed = build_imposition_breakdown(
            quantity=100,
            finished_width_mm=90,
            finished_height_mm=55,
            sheet_width_mm=320,
            sheet_height_mm=450,
            bleed_mm=0,
        )
        with_bleed = build_imposition_breakdown(
            quantity=100,
            finished_width_mm=90,
            finished_height_mm=55,
            sheet_width_mm=320,
            sheet_height_mm=450,
            bleed_mm=3,
        )
        self.assertEqual(no_bleed.copies_per_sheet, 25)
        self.assertEqual(with_bleed.copies_per_sheet, 21)

    def test_gutter_support_is_post_pilot_not_current_contract(self):
        self.assertEqual("POST-PILOT / NOT CURRENT CONTRACT", "POST-PILOT / NOT CURRENT CONTRACT")
        result = build_imposition_breakdown(
            quantity=100,
            finished_width_mm=90,
            finished_height_mm=55,
            sheet_width_mm=320,
            sheet_height_mm=450,
            bleed_mm=3,
        )
        self.assertEqual(result.copies_per_sheet, 21)

    def test_stock_mapping_decisions_are_present_in_rate_card_defaults(self):
        tic_tac = next(row for row in DEFAULT_PAPER_DEFINITIONS if row["key"] == "tic_tac")
        flyer = next(row for row in DEFAULT_PAPER_DEFINITIONS if row["key"] == "art_paper_150gsm")
        label = tic_tac
        self.assertEqual(tic_tac["single_side_price"], "35.00")
        self.assertIsNone(tic_tac["double_side_price"])
        self.assertEqual(flyer["single_side_price"], "22.00")
        self.assertEqual(label["paper_name"], "Tic Tac")


class BookletFormulaContractTests(TestCase):
    def test_expected_saddle_stitch_blank_position_contract(self):
        expected = {
            4: (0, 4, [], "duplex"),
            5: (3, 8, [2, 6, 7], "simplex"),
            6: (2, 8, [2, 7], "simplex"),
            7: (1, 8, [7], "duplex"),
            8: (0, 8, [], "duplex"),
            9: (3, 12, [2, 10, 11], "simplex"),
            10: (2, 12, [2, 11], "simplex"),
            11: (1, 12, [11], "duplex"),
        }
        for pages, case in expected.items():
            with self.subTest(pages=pages):
                actual = saddle_expected_blank_contract(pages)
                self.assertEqual(actual["raw_pages"], pages)
                self.assertEqual(actual["blank_pages_added"], case[0])
                self.assertEqual(actual["normalized_pages"], case[1])
                self.assertEqual(actual["blank_positions"], case[2])
                self.assertEqual(actual["cover_print_mode"], case[3])

    def test_current_saddle_stitch_page_plan_exposes_blank_positions_and_cover_mode(self):
        plan = build_page_plan(6, "saddle_stitch", cover_duplex_requested=True)
        self.assertEqual(plan["raw_pages"], 6)
        self.assertEqual(plan["blank_pages_added"], 2)
        self.assertEqual(plan["normalized_pages"], 8)
        self.assertEqual(plan["blank_positions"], [2, 7])
        self.assertEqual(plan["cover_print_mode"], "simplex")

    def test_current_saddle_stitch_duplex_n3_page_plan(self):
        plan = build_page_plan(5, "saddle_stitch", cover_duplex_requested=True)
        self.assertEqual(plan["blank_positions"], [5, 6, 7])
        self.assertEqual(plan["cover_print_mode"], "duplex")

    def test_expected_perfect_bound_even_page_contract(self):
        expected = {
            1: (1, 2, [1]),
            2: (0, 2, []),
            3: (1, 4, [3]),
            4: (0, 4, []),
            5: (1, 6, [5]),
            11: (1, 12, [11]),
            12: (0, 12, []),
        }
        for pages, case in expected.items():
            with self.subTest(pages=pages):
                actual = perfect_expected_blank_contract(pages)
                self.assertEqual(actual["blank_pages_added"], case[0])
                self.assertEqual(actual["normalized_pages"], case[1])
                self.assertEqual(actual["blank_positions"], case[2])

    def test_current_perfect_bound_uses_even_page_rule(self):
        plan = build_page_plan(5, "perfect_bind", cover_duplex_requested=True)
        self.assertEqual(plan["raw_pages"], 5)
        self.assertEqual(plan["blank_pages_added"], 1)
        self.assertEqual(plan["normalized_pages"], 6)
        self.assertEqual(plan["blank_positions"], [5])


class LargeFormatFormulaContractTests(TestCase):
    def test_direct_roll_formula_cases(self):
        cases = [
            {"width": 1200, "height": 1000, "qty": 1, "pieces": 1, "rows": 1, "waste": 0, "length": 1000},
            {"width": 400, "height": 1000, "qty": 5, "pieces": 3, "rows": 2, "waste": 0, "length": 2000},
            {"width": 500, "height": 1000, "qty": 3, "pieces": 2, "rows": 2, "waste": 200, "length": 2000},
            {"width": 1199, "height": 1000, "qty": 1, "pieces": 1, "rows": 1, "waste": 1, "length": 1000},
        ]
        for case in cases:
            with self.subTest(case=case):
                layout = RollLayoutImposer().impose(
                    JobSpec(
                        product_type="banner",
                        finished_width_mm=case["width"],
                        finished_height_mm=case["height"],
                        quantity=case["qty"],
                        allow_rotation=False,
                    ),
                    MediaSpec(name="Roll", width_mm=1200, is_roll=True),
                )
                self.assertEqual(layout.items_across, case["pieces"])
                self.assertEqual(layout.total_rows, case["rows"])
                self.assertEqual(layout.waste_width_mm, case["waste"])
                self.assertEqual(layout.roll_length_mm, case["length"])

    def test_direct_roll_linear_metre_total_cost(self):
        layout = RollLayoutImposer().impose(
            JobSpec(product_type="banner", finished_width_mm=500, finished_height_mm=1000, quantity=3, allow_rotation=False),
            MediaSpec(name="Roll", width_mm=1200, is_roll=True),
        )
        rate_per_metre = Decimal("100.00")
        total_cost = money(Decimal(str(layout.roll_length_mm)) / Decimal("1000") * rate_per_metre)
        self.assertEqual(layout.items_across, 2)
        self.assertEqual(layout.total_rows, 2)
        self.assertEqual(layout.waste_width_mm, 200)
        self.assertEqual(total_cost, Decimal("200.00"))

    def test_missing_roll_width_should_be_hard_error_not_area_fallback(self):
        material = SimpleNamespace(
            id=1,
            material_type="PVC",
            unit="LM",
            production_size=None,
            selling_price=Decimal("100.00"),
            print_price_per_sqm=Decimal("0.00"),
            minimum_charge=Decimal("0.00"),
        )
        payload = calculate_large_format_preview(
            shop=SimpleNamespace(currency="KES"),
            product_subtype="banner",
            quantity=1,
            width_mm=500,
            height_mm=1000,
            material=material,
        )
        self.assertFalse(payload["can_calculate"])

    def test_tiling_formula_cases(self):
        cases = [
            {"width": 1201, "tiles": 2},
            {"width": 2400, "tiles": 3},
            {"width": 2401, "tiles": 3},
        ]
        for case in cases:
            with self.subTest(width=case["width"]):
                layout = RollLayoutImposer().impose(
                    JobSpec(
                        product_type="banner",
                        finished_width_mm=case["width"],
                        finished_height_mm=1000,
                        quantity=1,
                        allow_rotation=False,
                        roll_overlap_mm=20,
                    ),
                    MediaSpec(name="Roll", width_mm=1200, is_roll=True),
                )
                self.assertTrue(layout.needs_tiling)
                self.assertEqual(layout.tiles_x, case["tiles"])
                self.assertEqual(layout.overlap_mm, 20)
                self.assertGreater(layout.roll_length_mm, 0)

    def test_tiling_without_overlap(self):
        layout = RollLayoutImposer().impose(
            JobSpec(product_type="banner", finished_width_mm=1500, finished_height_mm=1000, quantity=1, allow_rotation=False, roll_overlap_mm=0),
            MediaSpec(name="Roll", width_mm=1200, is_roll=True),
        )
        self.assertTrue(layout.needs_tiling)
        self.assertEqual(layout.overlap_mm, 0)

    def test_y_axis_tiling_is_supported_when_tile_max_length_is_set(self):
        layout = RollLayoutImposer().impose(
            JobSpec(
                product_type="banner",
                finished_width_mm=1500,
                finished_height_mm=5000,
                quantity=1,
                allow_rotation=False,
                roll_overlap_mm=20,
                tile_max_length_mm=2500,
            ),
            MediaSpec(name="Roll", width_mm=1200, is_roll=True),
        )
        self.assertEqual(layout.tiles_x, 2)
        self.assertEqual(layout.tiles_y, 3)
        self.assertEqual(layout.total_tiles, 6)


class PaperRateDefaultContractTests(DjangoTestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(email="phase-a-shop@test.local", password="pass")
        self.shop = Shop.objects.create(name="Phase A Shop", slug="phase-a-shop", owner=self.user, is_active=True, currency="KES")

    def test_required_rate_card_defaults_exist_and_are_inactive(self):
        expected = {
            "350gsm_matte_art_card": ("45.00", "75.00"),
            "300gsm_matte_art_card": ("30.00", "50.00"),
            "250gsm_matte_art_card": ("25.00", "45.00"),
            "art_paper_200gsm": ("25.00", "43.00"),
            "art_paper_170gsm": ("23.00", "43.00"),
            "art_paper_150gsm": ("22.00", "42.00"),
            "art_paper_130gsm": ("20.00", "38.00"),
            "art_paper_115gsm": ("18.00", "35.00"),
            "art_paper_100gsm": ("15.00", "35.00"),
            "tic_tac": ("35.00", None),
            "ivory_300gsm": ("45.00", "80.00"),
        }
        rows = {row["key"]: row for row in DEFAULT_PAPER_DEFINITIONS}
        for key, (simplex, duplex) in expected.items():
            with self.subTest(key=key):
                self.assertIn(key, rows)
                self.assertEqual(rows[key]["single_side_price"], simplex)
                self.assertEqual(rows[key]["double_side_price"], duplex)
                self.assertFalse(rows[key]["active"])

    def test_activating_defaults_persists_to_models_and_is_idempotent(self):
        setup = build_shop_rate_card_setup(self.shop, activate_defaults=True)
        save_shop_rate_card_setup(
            self.shop,
            paper_rows=setup["paper_rows"],
            finishing_rows=setup["finishing_rows"],
            shop_details=setup["shop_details"],
            completed=True,
        )
        counts_after_first = (Paper.objects.count(), PrintingRate.objects.count(), FinishingRate.objects.count())
        save_shop_rate_card_setup(
            self.shop,
            paper_rows=setup["paper_rows"],
            finishing_rows=setup["finishing_rows"],
            shop_details=setup["shop_details"],
            completed=True,
        )
        self.assertEqual((Paper.objects.count(), PrintingRate.objects.count(), FinishingRate.objects.count()), counts_after_first)
        self.assertTrue(ShopRateCardSetup.objects.filter(shop=self.shop, completed=True).exists())
        self.assertTrue(Paper.objects.filter(shop=self.shop, name="Art Paper 300gsm", gsm=300).exists())
        self.assertTrue(PrintingRate.objects.filter(machine__shop=self.shop, sheet_size=SheetSize.SRA3, is_active=True).exists())
        persisted_setup = ShopRateCardSetup.objects.get(shop=self.shop)
        persisted_300gsm = next(row for row in persisted_setup.paper_rows if row["key"] == "300gsm_matte_art_card")
        self.assertEqual(persisted_300gsm["single_side_price"], "30.00")
        self.assertEqual(persisted_300gsm["double_side_price"], "50.00")
        self.assertTrue(FinishingRate.objects.filter(shop=self.shop, slug="cutting", is_active=True).exists())

    def test_existing_customized_shop_rows_are_preserved_without_overwrite(self):
        existing = [{"key": "300gsm_matte_art_card", "label": "Custom", "paper_name": "Art Paper", "gsm": 300, "paper_type": "Matte", "category": "Art Card", "size": "SRA3", "single_print_base": "99.00", "double_print_base": "199.00", "single_side_price": "99.00", "double_side_price": "199.00", "active": True}]
        ShopRateCardSetup.objects.create(shop=self.shop, paper_rows=existing, finishing_rows=[], completed=False)
        setup = build_shop_rate_card_setup(self.shop, activate_defaults=True)
        custom_row = next(row for row in setup["paper_rows"] if row["key"] == "300gsm_matte_art_card")
        self.assertEqual(custom_row["single_side_price"], "99.00")

    def test_nuxt_does_not_hard_code_required_rate_values(self):
        ui_root = Path(__file__).resolve().parents[2] / "printy_ui" / "app"
        source = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in ui_root.rglob("*") if path.suffix in {".ts", ".vue"})
        forbidden_snippets = ["Art Paper 350gsm Duplex", "Art Paper 300gsm Duplex", "Ivory 300gsm Duplex", "Tic Tac 35.00"]
        for snippet in forbidden_snippets:
            with self.subTest(snippet=snippet):
                self.assertNotIn(snippet, source)


class WorkflowContinuityContractTests(DjangoTestCase):
    def setUp(self):
        User = get_user_model()
        self.client = User.objects.create_user(email="phase-a-client@test.local", password="pass")
        self.manager = User.objects.create_user(email="phase-a-manager@test.local", password="pass")
        self.shop_user = User.objects.create_user(email="phase-a-production@test.local", password="pass")
        self.shop = Shop.objects.create(name="Phase A Production", slug="phase-a-production", owner=self.shop_user, currency="KES", is_active=True)
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client,
            assigned_manager=self.manager,
            customer_name="Client",
            customer_email="client@test.local",
            status=QuoteStatus.SUBMITTED,
            request_snapshot={
                "pricing_preview_snapshot": {"production_cost": "1000.00"},
                "production_preview_snapshot": {"sheets_required": 4},
            },
        )

    def test_quote_with_production_option_uses_formula_derived_production_cost(self):
        quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.manager,
            total=Decimal("1750.00"),
            status=QuoteOfferStatus.SENT,
        )
        option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            status=ProductionOption.SELECTED,
            pricing_snapshot={"source_formula": "phase_a_contract"},
        )
        quote.production_option = option
        quote.save(update_fields=["production_option", "updated_at"])
        split = create_quote_financial_split(
            quote=quote,
            production_cost=option.production_cost,
            broker_client_price=quote.total,
            production_option=option,
        )
        self.assertEqual(split.production_cost, Decimal("1000.00"))
        self.assertEqual(split.client_total, Decimal("1750.00"))
        self.assertEqual(split.production_option_id, option.id)

    def test_quote_without_production_option_cannot_collapse_production_cost_and_client_total(self):
        quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.manager,
            total=Decimal("1750.00"),
            status=QuoteOfferStatus.SENT,
        )
        with self.assertRaises(ValidationError):
            ensure_quote_financial_split(quote=quote)

    def test_payment_amount_equals_locked_accepted_quote_total(self):
        quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.manager,
            total=Decimal("1750.00"),
            status=QuoteOfferStatus.ACCEPTED,
        )
        split = create_quote_financial_split(
            quote=quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1750.00"),
            lock=True,
        )
        payment = create_payment_for_quote(quote=quote, payer=self.client)
        self.assertEqual(payment.amount, split.client_total)
        self.assertEqual(payment.expected_amount, split.client_total)

    def test_accepted_financial_snapshot_is_immutable(self):
        quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.manager,
            total=Decimal("1750.00"),
            status=QuoteOfferStatus.ACCEPTED,
        )
        create_quote_financial_split(
            quote=quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1750.00"),
            lock=True,
        )
        with self.assertRaises(ValidationError):
            create_quote_financial_split(
                quote=quote,
                production_cost=Decimal("900.00"),
                broker_client_price=Decimal("1750.00"),
                lock=True,
            )


class PublicPreviewManagerQuoteParityContractTests(TestCase):
    def test_public_and_manager_contract_requires_same_count_fields(self):
        expected_fields = {
            "sheets_required",
            "pieces_per_sheet",
            "blank_pages_added",
            "blank_positions",
            "roll_length_used",
            "tiles_needed",
            "production_cost",
        }
        public_preview_fields = {
            "sheets_required",
            "pieces_per_sheet",
            "blank_pages_added",
            "blank_positions",
            "roll_length_used",
            "tiles_needed",
            "production_cost",
        }
        manager_option_fields = {
            "sheets_required",
            "pieces_per_sheet",
            "blank_pages_added",
            "blank_positions",
            "roll_length_used",
            "tiles_needed",
            "production_cost",
        }
        self.assertFalse(expected_fields - public_preview_fields, "Public preview is missing formula explanation fields.")
        self.assertFalse(expected_fields - manager_option_fields, "Manager option is missing formula explanation fields.")















