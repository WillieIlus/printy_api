from __future__ import annotations

from unittest import TestCase

from services.engine.schemas.inputs import FinishingSpec, JobSpec, MediaSpec
from services.engine.services.booklet_imposer import BookletImposer
from services.engine.services.finishing_planner import FinishingPlanner
from services.engine.services.flat_sheet_imposer import FlatSheetImposer
from services.engine.services.quote_calculator import QuoteCalculator
from services.engine.services.roll_layout_imposer import RollLayoutImposer


class FlatSheetImposerTests(TestCase):
    def setUp(self) -> None:
        self.imposer = FlatSheetImposer()
        self.sra3 = MediaSpec(name="SRA3", width_mm=320, height_mm=450)
        self.sra3_with_margins = MediaSpec(
            name="SRA3",
            width_mm=320,
            height_mm=450,
            printable_margin_top_mm=40,
            printable_margin_bottom_mm=40,
        )
        self.a3 = MediaSpec(name="A3", width_mm=297, height_mm=420)

    def test_business_cards_on_sra3(self) -> None:
        result = self.imposer.impose(
            JobSpec(
                product_type="business_card",
                finished_width_mm=90,
                finished_height_mm=55,
                quantity=1000,
                bleed_mm=3,
                gap_mm=2,
                allow_rotation=True,
            ),
            self.sra3_with_margins,
        )
        self.assertTrue(result.fits)
        self.assertEqual(result.copies_per_sheet, 15)
        self.assertEqual(result.total_sheets, 67)
        self.assertEqual(result.chosen_orientation, "normal")

    def test_a5_flyers_choose_sra3_over_a3(self) -> None:
        result = self.imposer.impose_best(
            JobSpec(
                product_type="flyer",
                finished_width_mm=148,
                finished_height_mm=210,
                quantity=500,
                bleed_mm=3,
                gap_mm=2,
                allow_rotation=True,
            ),
            [self.a3, self.sra3],
        )
        self.assertTrue(result.fits)
        self.assertEqual(result.media_name, "SRA3")
        self.assertEqual(result.copies_per_sheet, 4)


class RollLayoutImposerTests(TestCase):
    def setUp(self) -> None:
        self.imposer = RollLayoutImposer()
        self.roll_1200 = MediaSpec(name="1.2m Roll", width_mm=1200, height_mm=None, is_roll=True)
        self.roll_900 = MediaSpec(name="90cm Roll", width_mm=900, height_mm=None, is_roll=True)

    def test_direct_roll_fit_for_banner(self) -> None:
        result = self.imposer.impose(
            JobSpec(
                product_type="banner",
                finished_width_mm=1100,
                finished_height_mm=3000,
                quantity=1,
                allow_rotation=False,
            ),
            self.roll_1200,
        )
        self.assertTrue(result.fits_directly)
        self.assertFalse(result.needs_tiling)
        self.assertEqual(result.items_across, 1)
        self.assertEqual(result.roll_length_mm, 3000)

    def test_a2_rotates_on_90cm_roll(self) -> None:
        result = self.imposer.impose(
            JobSpec(
                product_type="poster",
                finished_width_mm=420,
                finished_height_mm=594,
                quantity=10,
                allow_rotation=True,
                gap_mm=5,
            ),
            self.roll_900,
        )
        self.assertTrue(result.fits_directly)
        self.assertFalse(result.rotated)
        self.assertEqual(result.items_across, 2)
        self.assertEqual(result.total_rows, 5)

    def test_oversized_roll_job_tiles(self) -> None:
        result = self.imposer.impose(
            JobSpec(
                product_type="banner",
                finished_width_mm=1500,
                finished_height_mm=5000,
                quantity=1,
                allow_rotation=False,
                roll_overlap_mm=20,
                tile_max_length_mm=2500,
            ),
            self.roll_1200,
        )
        self.assertFalse(result.fits_directly)
        self.assertTrue(result.needs_tiling)
        self.assertEqual(result.tiles_x, 2)
        self.assertEqual(result.tiles_y, 3)
        self.assertEqual(result.total_tiles, 6)


class BookletAndFinishingTests(TestCase):
    def setUp(self) -> None:
        self.booklet_imposer = BookletImposer()
        self.finishing_planner = FinishingPlanner()

    def test_booklet_adjusts_to_multiple_of_four(self) -> None:
        result = self.booklet_imposer.impose(
            JobSpec(
                product_type="booklet",
                finished_width_mm=210,
                finished_height_mm=297,
                quantity=100,
                pages=10,
            )
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.adjusted_page_count, 12)
        self.assertEqual(result.blanks_added, 2)
        self.assertEqual(result.sheets_per_booklet, 3)

    def test_booklet_with_separate_cover(self) -> None:
        result = self.booklet_imposer.impose(
            JobSpec(
                product_type="booklet",
                finished_width_mm=210,
                finished_height_mm=297,
                quantity=50,
                pages=20,
                cover_pages=4,
            )
        )
        self.assertFalse(result.self_cover)
        self.assertEqual(result.cover_sheet_count, 50)
        self.assertEqual(result.inner_sheet_count, 200)

    def test_finishing_units_derive_from_layout(self) -> None:
        job = JobSpec(
            product_type="booklet",
            finished_width_mm=148,
            finished_height_mm=210,
            quantity=25,
            pages=12,
        )
        layout = self.booklet_imposer.impose(job)
        result = self.finishing_planner.plan(
            job,
            FinishingSpec(
                lamination_sides=2,
                lamination_mode="per_parent_sheet",
                cutting_mode="per_job",
            ),
            layout,
        )
        self.assertEqual(result.lamination_units, layout.total_sheet_count * 2)
        self.assertEqual(result.cut_units, 1)


class QuoteCalculatorTests(TestCase):
    def setUp(self) -> None:
        self.calculator = QuoteCalculator()

    def test_quote_calculator_routes_flat_sheet_jobs(self) -> None:
        summary = self.calculator.calculate(
            JobSpec(
                product_type="postcard",
                finished_width_mm=105,
                finished_height_mm=148,
                quantity=500,
                bleed_mm=3,
                gap_mm=2,
            ),
            [
                MediaSpec(name="A3", width_mm=297, height_mm=420),
                MediaSpec(name="SRA3", width_mm=320, height_mm=450),
            ],
            FinishingSpec(lamination_sides=2, lamination_mode="per_parent_sheet", cutting_mode="per_job"),
        )
        self.assertEqual(summary.engine_type, "flat_sheet")
        self.assertGreater(summary.parent_sheets_required, 0)
        self.assertIsNotNone(summary.finishing)

    def test_quote_calculator_routes_roll_jobs(self) -> None:
        summary = self.calculator.calculate(
            JobSpec(
                product_type="banner",
                finished_width_mm=1100,
                finished_height_mm=3000,
                quantity=2,
                allow_rotation=False,
            ),
            [MediaSpec(name="1.2m Roll", width_mm=1200, is_roll=True)],
            FinishingSpec(eyelets=4, hems=True),
        )
        self.assertEqual(summary.engine_type, "roll")
        self.assertGreater(summary.roll_length_required_mm, 0)
        self.assertEqual(summary.finishing.eyelet_units, 8)
