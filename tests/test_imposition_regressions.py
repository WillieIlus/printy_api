"""Regression tests for the imposition audit fixes.

Three confirmed defects are pinned here:

1. A piece that cannot fit on the sheet in either orientation used to be clamped
   to ``max(1, ...)`` and priced as a 1-up job.
2. Supplied millimetre values were truncated with ``int(...)`` (106.9 -> 106),
   which shrinks the piece and can make an impossible imposition fit.
3. An arbitrary string such as ``"123x217mm"`` was looked up in the size library,
   missed, and turned into an "unavailable size" instead of being read as custom
   dimensions.

The authoritative buyer pricing path is ``services/pricing/imposition.py``; these
tests exercise that maths directly and never redefine a business rule.
"""

from decimal import Decimal

from django.test import TestCase

from services.pricing.calculator_config import (
    parse_custom_size_dimensions,
    resolve_request_finished_size,
)
from services.pricing.imposition import (
    NO_FIT_ORIENTATION,
    build_imposition_breakdown,
    compute_copies_per_sheet,
    compute_good_sheets,
)


SRA3 = (320, 450)


class NoFitMustNotBecomeOneUpTests(TestCase):
    """Defect 1: no-fit is represented explicitly, never as a fake 1-up."""

    def impose(self, *, width, height, sheet_w=SRA3[0], sheet_h=SRA3[1], bleed=0, quantity=1000):
        return build_imposition_breakdown(
            quantity=quantity,
            finished_width_mm=width,
            finished_height_mm=height,
            sheet_width_mm=sheet_w,
            sheet_height_mm=sheet_h,
            bleed_mm=bleed,
        )

    def test_piece_far_larger_than_sheet_is_no_fit_not_one_up(self):
        """The audit's example: 500x600mm on a 320x450mm sheet."""
        result = self.impose(width=500, height=600, quantity=1000)
        self.assertEqual(result.copies_per_sheet, 0)
        self.assertEqual(result.good_sheets, 0)
        self.assertFalse(result.fits)
        self.assertEqual(result.orientation, NO_FIT_ORIENTATION)
        self.assertEqual(result.cols, 0)
        self.assertEqual(result.rows, 0)
        # The audit's forbidden triple: 1 piece, 1000 sheets, priced.
        self.assertNotEqual(result.copies_per_sheet, 1)
        self.assertNotEqual(result.good_sheets, 1000)
        self.assertIn("No fit", result.explanation)

    def test_no_fit_holds_with_bleed_applied(self):
        result = self.impose(width=321, height=451, bleed=3)
        self.assertEqual(result.copies_per_sheet, 0)
        self.assertFalse(result.fits)

    def test_taller_than_sheet_but_narrower_is_still_a_real_fit(self):
        """A piece longer than the sheet is not automatically a no-fit."""
        result = self.impose(width=90, height=400, bleed=0)
        self.assertTrue(result.fits)
        self.assertEqual(result.copies_per_sheet, 3)
        self.assertEqual((result.cols, result.rows), (3, 1))

    def test_compute_copies_per_sheet_reports_none_orientation(self):
        self.assertEqual(compute_copies_per_sheet(500, 600, 320, 450), (0, NO_FIT_ORIENTATION))

    def test_compute_good_sheets_is_zero_when_nothing_fits(self):
        self.assertEqual(compute_good_sheets(1000, 0), 0)

    def test_zero_and_missing_dimensions_are_no_fit(self):
        for width, height, sheet_w, sheet_h in (
            (0, 0, 320, 450),
            (100, 100, 0, 450),
            (100, 100, 320, 0),
            (-5, 100, 320, 450),
            (None, None, 320, 450),
            ("abc", 100, 320, 450),
        ):
            with self.subTest(width=width, height=height, sheet_w=sheet_w, sheet_h=sheet_h):
                self.assertEqual(compute_copies_per_sheet(width, height, sheet_w, sheet_h), (0, NO_FIT_ORIENTATION))


class OrientationRegressionTests(TestCase):
    """Normal fit, rotated fit, exact edge, slightly larger, reversed."""

    def impose(self, *, width, height, sheet_w=320, sheet_h=450, bleed=0, quantity=100):
        return build_imposition_breakdown(
            quantity=quantity,
            finished_width_mm=width,
            finished_height_mm=height,
            sheet_width_mm=sheet_w,
            sheet_height_mm=sheet_h,
            bleed_mm=bleed,
        )

    def test_normal_fit_is_chosen_when_it_imposes_more(self):
        # floor(320/100)=3 x floor(450/150)=3 => 9 normal; rotated 2 x 4 => 8.
        result = self.impose(width=100, height=150)
        self.assertTrue(result.fits)
        self.assertEqual(result.orientation, "normal")
        self.assertEqual(result.copies_per_sheet, 9)
        self.assertEqual((result.cols, result.rows), (3, 3))

    def test_rotated_fit_is_chosen_when_normal_is_worse(self):
        result = self.impose(width=150, height=100)
        self.assertTrue(result.fits)
        self.assertEqual(result.orientation, "rotated")
        self.assertEqual(result.copies_per_sheet, 9)
        self.assertEqual((result.cols, result.rows), (3, 3))

    def test_exact_edge_fit_is_one_up_not_no_fit(self):
        """320x450mm on a 320x450mm sheet: exactly one copy, and it fits."""
        result = self.impose(width=320, height=450, quantity=3)
        self.assertTrue(result.fits)
        self.assertEqual(result.copies_per_sheet, 1)
        self.assertEqual(result.good_sheets, 3)
        self.assertEqual(result.orientation, "normal")
        self.assertEqual((result.cols, result.rows), (1, 1))

    def test_exact_edge_fit_rotated_still_fits(self):
        result = self.impose(width=450, height=320)
        self.assertTrue(result.fits)
        self.assertEqual(result.copies_per_sheet, 1)

    def test_piece_one_millimetre_too_large_is_no_fit(self):
        """320.1mm wide cannot fit a 320mm sheet: 1 copy is not printed."""
        result = self.impose(width="320.1", height=450)
        self.assertFalse(result.fits)
        self.assertEqual(result.copies_per_sheet, 0)

    def test_piece_slightly_larger_in_both_axes_is_no_fit(self):
        result = self.impose(width=321, height=451)
        self.assertFalse(result.fits)
        self.assertEqual(result.copies_per_sheet, 0)

    def test_width_height_reversed_produces_the_same_yield(self):
        """The maximum yield is symmetric: swapping the axes cannot change it."""
        portrait = self.impose(width=100, height=150, quantity=250)
        landscape = self.impose(width=150, height=100, quantity=250)
        self.assertTrue(portrait.fits)
        self.assertTrue(landscape.fits)
        self.assertEqual(portrait.copies_per_sheet, landscape.copies_per_sheet)
        self.assertEqual(portrait.good_sheets, landscape.good_sheets)
        self.assertNotEqual(portrait.orientation, landscape.orientation)

    def test_rotation_only_fit_is_detected(self):
        """400x90mm fits a 320x450mm sheet only when rotated (normal yields 0)."""
        result = self.impose(width=400, height=90)
        self.assertTrue(result.fits)
        self.assertEqual(result.orientation, "rotated")
        self.assertEqual((result.cols, result.rows), (3, 1))
        self.assertEqual(result.copies_per_sheet, 3)

        normal_only = compute_copies_per_sheet(400, 90, 320, 450)
        self.assertEqual(normal_only, (3, "rotated"))

    def test_yield_never_increases_as_the_piece_grows(self):
        yields = [self.impose(width=w, height=217).copies_per_sheet for w in (150, 155, 160, 165)]
        self.assertEqual(yields, sorted(yields, reverse=True))
        self.assertEqual(yields, [4, 4, 4, 2])


class DecimalDimensionPrecisionTests(TestCase):
    """Defect 2: supplied millimetre precision survives to the imposition maths."""

    def impose(self, *, width, height, sheet_w=320, sheet_h=450, bleed=0, quantity=100):
        return build_imposition_breakdown(
            quantity=quantity,
            finished_width_mm=width,
            finished_height_mm=height,
            sheet_width_mm=sheet_w,
            sheet_height_mm=sheet_h,
            bleed_mm=bleed,
        )

    def test_decimal_dimensions_are_not_truncated_to_integers(self):
        result = self.impose(width=106.9, height=148.9)
        self.assertEqual(result.finished_width_mm, 106.9)
        self.assertEqual(result.finished_height_mm, 148.9)
        self.assertNotEqual(result.finished_width_mm, 106)
        self.assertNotEqual(result.finished_height_mm, 148)

    def test_decimal_dimensions_from_strings_keep_precision(self):
        result = self.impose(width="106.9", height="148.9")
        self.assertEqual(result.finished_width_mm, 106.9)
        self.assertEqual(result.finished_height_mm, 148.9)

    def test_decimal_dimension_at_imposition_boundary_does_not_gain_a_column(self):
        """106.9mm pieces on a 320mm sheet impose 2 x 4 = 8 rotated, not 9.

        Truncating 106.9 -> 106 would give floor(320/106)=3 x floor(450/148)=3
        and wrongly report a 9-up sheet.
        """
        result = self.impose(width=106.9, height=148.9, bleed=0)
        self.assertEqual(result.orientation, "rotated")
        self.assertEqual((result.cols, result.rows), (2, 4))
        self.assertEqual(result.copies_per_sheet, 8)

        truncated_would_be = compute_copies_per_sheet(106, 148, 320, 450, bleed_mm=0)
        self.assertEqual(truncated_would_be, (9, "normal"))

    def test_decimal_just_over_a_boundary_is_no_fit_not_a_fit(self):
        """A sheet one decimal place too small must not be rounded away."""
        result = self.impose(width="320.5", height=450)
        self.assertFalse(result.fits)
        self.assertEqual(result.copies_per_sheet, 0)

    def test_decimal_bleed_is_added_without_rounding(self):
        without = self.impose(width=90, height=55, bleed=0)
        with_bleed = self.impose(width=90, height=55, bleed=Decimal("2.5"))
        self.assertEqual(without.copies_per_sheet, 25)
        self.assertEqual(with_bleed.copies_per_sheet, 21)

    def test_decimal_dimensions_on_different_sheets_stay_different(self):
        on_sra3 = self.impose(width=106.9, height=148.9, sheet_w=320, sheet_h=450)
        on_a3 = self.impose(width=106.9, height=148.9, sheet_w=300, sheet_h=420)
        self.assertNotEqual(on_sra3.copies_per_sheet, on_a3.copies_per_sheet)


class ConfiguredSheetDimensionTests(TestCase):
    """Defect 5: the calculation uses configured paper millimetres, not an SRA3 constant."""

    def impose(self, *, width, height, sheet_w, sheet_h, bleed=0, quantity=100):
        return build_imposition_breakdown(
            quantity=quantity,
            finished_width_mm=width,
            finished_height_mm=height,
            sheet_width_mm=sheet_w,
            sheet_height_mm=sheet_h,
            bleed_mm=bleed,
        )

    def test_different_configured_sheets_produce_different_results(self):
        sra3 = self.impose(width=123, height=217, sheet_w=320, sheet_h=450)
        a3 = self.impose(width=123, height=217, sheet_w=300, sheet_h=420)
        sra3_double = self.impose(width=123, height=217, sheet_w=640, sheet_h=900)
        self.assertNotEqual(sra3.copies_per_sheet, a3.copies_per_sheet)
        self.assertNotEqual(sra3.copies_per_sheet, sra3_double.copies_per_sheet)
        self.assertEqual(sra3.copies_per_sheet, 4)
        self.assertEqual(a3.copies_per_sheet, 3)
        self.assertEqual(sra3_double.copies_per_sheet, 20)

    def test_sheet_millimetres_are_reported_verbatim(self):
        result = self.impose(width=123, height=217, sheet_w=300, sheet_h=420)
        self.assertEqual(result.sheet_width_mm, 300)
        self.assertEqual(result.sheet_height_mm, 420)

    def test_a_no_fit_on_one_configured_sheet_can_fit_on_a_bigger_one(self):
        """123x217mm on 320x450 fits (2-up); on 200x200 it cannot."""
        small = self.impose(width=123, height=217, sheet_w=200, sheet_h=200)
        large = self.impose(width=123, height=217, sheet_w=320, sheet_h=450)
        self.assertFalse(small.fits)
        self.assertTrue(large.fits)


class SizeResolutionContractTests(TestCase):
    """Defect 3: an explicit, robust finished-size resolution contract."""

    def test_predefined_library_sizes_still_resolve(self):
        size, is_custom = resolve_request_finished_size("flyer", "A5")
        self.assertFalse(is_custom)
        self.assertEqual((size["width_mm"], size["height_mm"]), (148, 210))

        size, is_custom = resolve_request_finished_size("business_card", "85x55mm")
        self.assertFalse(is_custom)
        self.assertEqual((size["width_mm"], size["height_mm"]), (85, 55))

    def test_finished_size_custom_with_dimensions(self):
        size, is_custom = resolve_request_finished_size("business_card", "custom", width_mm=123, height_mm=217)
        self.assertTrue(is_custom)
        self.assertEqual((size["width_mm"], size["height_mm"]), (Decimal("123"), Decimal("217")))

    def test_size_mode_custom_with_dimensions(self):
        size, is_custom = resolve_request_finished_size("business_card", "", width_mm=123, height_mm=217, size_mode="custom")
        self.assertTrue(is_custom)
        self.assertEqual((size["width_mm"], size["height_mm"]), (Decimal("123"), Decimal("217")))

    def test_dimension_string_is_custom_not_an_unavailable_library_size(self):
        """The reported defect: "123x217mm" used to miss the library entirely."""
        size, is_custom = resolve_request_finished_size("business_card", "123x217mm")
        self.assertTrue(is_custom)
        self.assertEqual((size["width_mm"], size["height_mm"]), (Decimal("123"), Decimal("217")))

    def test_decimal_dimension_string_keeps_precision(self):
        size, is_custom = resolve_request_finished_size("business_card", "106.9x148.9mm")
        self.assertTrue(is_custom)
        self.assertEqual(size["width_mm"], Decimal("106.9"))
        self.assertEqual(size["height_mm"], Decimal("148.9"))

    def test_dimension_string_spelling_variants(self):
        for text in ("123x217", "123 x 217", "123X217mm", "123×217", "123 x 217 mm"):
            with self.subTest(text=text):
                size, is_custom = resolve_request_finished_size("business_card", text)
                self.assertTrue(is_custom)
                self.assertEqual(size["width_mm"], Decimal("123"))
                self.assertEqual(size["height_mm"], Decimal("217"))

    def test_arbitrary_strings_are_never_treated_as_predefined_sizes(self):
        for text in ("Premium Matt Card", "whatever", "A9", "SRA3", "not-a-size", ""):
            with self.subTest(text=text):
                size, is_custom = resolve_request_finished_size("flyer", text)
                self.assertIsNone(size)
                self.assertFalse(is_custom)

    def test_invalid_custom_dimensions_are_reported_as_custom_and_missing(self):
        for kwargs in (
            {"finished_size": "custom"},
            {"finished_size": "custom", "width_mm": 0, "height_mm": 217},
            {"finished_size": "custom", "width_mm": 123, "height_mm": None},
            {"finished_size": "0x0mm"},
        ):
            with self.subTest(**kwargs):
                finished_size = kwargs.pop("finished_size")
                size, is_custom = resolve_request_finished_size("business_card", finished_size, **kwargs)
                self.assertIsNone(size)
                self.assertTrue(is_custom)

    def test_library_size_wins_over_dimensions_when_both_are_supplied(self):
        """A real library size must not be shadowed by stray dimension fields."""
        size, is_custom = resolve_request_finished_size("flyer", "A5", width_mm=10, height_mm=10)
        self.assertFalse(is_custom)
        self.assertEqual((size["width_mm"], size["height_mm"]), (148, 210))

    def test_parse_custom_size_dimensions_rejects_non_dimensions(self):
        self.assertIsNone(parse_custom_size_dimensions("A5"))
        self.assertIsNone(parse_custom_size_dimensions("Premium Matt Card"))
        self.assertIsNone(parse_custom_size_dimensions("0x0mm"))
        self.assertIsNone(parse_custom_size_dimensions(None))
        self.assertEqual(parse_custom_size_dimensions("106.9x148.9"), (Decimal("106.9"), Decimal("148.9")))