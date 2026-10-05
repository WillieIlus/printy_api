"""HTTP-path regression tests for the imposition audit fixes.

These do not call the imposition helpers. They POST to the real public endpoint
``POST /api/calculator/public-preview/`` and assert on the response the buyer
actually receives, which is the only way to prove the defects are fixed end to
end (serializer -> size resolution -> matching -> authoritative imposition).

Every case pins the ten fields the audit asks to be reported:

    input dimensions, sheet dimensions, bleed, effective dimensions,
    normal cols x rows, rotated cols x rows, selected orientation,
    pieces per sheet, sheets required, can_calculate, no-fit reason.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from shops.models import Shop


ENDPOINT = "/api/calculator/public-preview/"
QUANTITY = 1000

# Finished size -> (expected pieces/sheet, expected orientation) on the sheet
# below, with the 3mm bleed the authoritative path applies when no product
# default_bleed_mm is supplied.
SRA3 = (320, 450)
EXPECTED_ON_SRA3 = {
    "123x217": (4, "normal"),
    "150x217": (4, "normal"),
    "155x217": (2, "normal"),
    "177x263": (2, "rotated"),
    "400x90": (3, "rotated"),
    "500x600": (0, "none"),
    "106.9x148.9": (6, "rotated"),
}


class PublicPreviewImpositionHttpTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Imposition audit policy",
            is_active=True,
            printer_fee_rate=Decimal("0.05"),
            broker_margin_fee_rate=Decimal("0.15"),
            add_platform_fee_on_top=False,
        )

    def _only_shop(self, slug, *, width_mm, height_mm, max_width_mm, max_height_mm, sheet_size="SRA3"):
        """Create exactly one public shop holding a paper of the given physical size."""
        Shop.objects.all().delete()
        owner = User.objects.create_user(
            email=f"{slug}-owner@test.com", password="pass12345", role=User.Role.PRODUCTION
        )
        shop = Shop.objects.create(
            owner=owner,
            name=f"Press {slug}",
            slug=slug,
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        machine = Machine.objects.create(
            shop=shop,
            name=f"{slug} Press",
            max_width_mm=max_width_mm,
            max_height_mm=max_height_mm,
            is_active=True,
        )
        Paper.objects.create(
            shop=shop,
            name=f"{width_mm}x{height_mm} Card",
            sheet_size=sheet_size,
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("1.00"),
            selling_price=Decimal("20.00"),
            width_mm=width_mm,
            height_mm=height_mm,
            is_active=True,
            is_default=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size=sheet_size,
            color_mode="COLOR",
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
            is_default=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting",
            slug=f"cutting-{slug}",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )
        return shop

    def _sra3_shop(self, slug="imposition-audit"):
        return self._only_shop(
            slug,
            width_mm=SRA3[0],
            height_mm=SRA3[1],
            max_width_mm=SRA3[0],
            max_height_mm=SRA3[1],
        )

    def preview(self, **overrides):
        payload = {
            "product_type": "business_card",
            "quantity": QUANTITY,
            "finished_size": "custom",
            "print_sides": "SIMPLEX",
            "color_mode": "COLOR",
            "requested_paper_category": "gloss",
            "requested_gsm": 300,
        }
        payload.update(overrides)
        return self.client.post(ENDPOINT, payload, format="json")

    def assert_imposition(self, response, *, width_mm, height_mm, expected_pps, expected_orientation):
        """Assert the ten reported fields on a priced response."""
        self.assertEqual(response.status_code, 200, response.json())
        payload = response.json()

        self.assertTrue(payload.get("can_calculate"), payload)
        production = payload.get("production_preview") or {}
        self.assertTrue(production, payload)

        self.assertEqual(Decimal(str(production.get("bleed_mm"))), Decimal("3"), payload)

        press_sheet = production.get("press_sheet") or {}
        self.assertEqual((press_sheet.get("width_mm"), press_sheet.get("height_mm")), (width_mm, height_mm), payload)

        layout = production.get("layout") or {}
        self.assertEqual(layout.get("orientation"), expected_orientation, payload)
        self.assertEqual(production.get("pieces_per_sheet"), expected_pps, payload)
        self.assertEqual(
            (layout.get("cols") or 0) * (layout.get("rows") or 0), expected_pps, payload
        )
        self.assertEqual(production.get("good_sheets"), -(-QUANTITY // expected_pps), payload)
        self.assertEqual(production.get("sheets_required"), production.get("good_sheets"), payload)
        return payload

    # ------------------------------------------------------------------ the seven audit cases

    def test_custom_fit_123x217(self):
        self._sra3_shop()
        response = self.preview(width_mm=123, height_mm=217)
        self.assert_imposition(
            response, width_mm=320, height_mm=450, expected_pps=4, expected_orientation="normal"
        )

    def test_boundary_change_150x217_then_155x217(self):
        self._sra3_shop()
        narrow = self.assert_imposition(
            self.preview(width_mm=150, height_mm=217),
            width_mm=320, height_mm=450, expected_pps=4, expected_orientation="normal",
        )
        wide = self.assert_imposition(
            self.preview(width_mm=155, height_mm=217),
            width_mm=320, height_mm=450, expected_pps=2, expected_orientation="normal",
        )
        self.assertGreater(
            narrow["production_preview"]["pieces_per_sheet"],
            wide["production_preview"]["pieces_per_sheet"],
        )

    def test_rotation_177x263(self):
        self._sra3_shop()
        self.assert_imposition(
            self.preview(width_mm=177, height_mm=263),
            width_mm=320, height_mm=450, expected_pps=2, expected_orientation="rotated",
        )

    def test_rotation_only_fit_400x90(self):
        """400x90mm does not fit a 320mm sheet unrotated; it must still price."""
        self._sra3_shop()
        self.assert_imposition(
            self.preview(width_mm=400, height_mm=90),
            width_mm=320, height_mm=450, expected_pps=3, expected_orientation="rotated",
        )

    def test_no_fit_500x600_is_not_priced_as_one_up(self):
        """The audit's critical case, over the real endpoint."""
        self._sra3_shop()
        response = self.preview(width_mm=500, height_mm=600)
        self.assertEqual(response.status_code, 200, response.json())
        payload = response.json()

        self.assertFalse(payload.get("can_calculate"), payload)
        self.assertFalse(payload.get("matches"), payload)

        reasons = " ".join(payload.get("unsupported_reasons") or [])
        self.assertIn("does not fit", reasons, payload)
        self.assertIn("500", reasons, payload)

        production = payload.get("production_preview") or {}
        self.assertNotEqual(production.get("pieces_per_sheet"), 1, payload)
        self.assertNotEqual(production.get("sheets_required"), QUANTITY, payload)
        self.assertIsNone(payload.get("total"), payload)

    def test_decimal_106_9x148_9_keeps_its_precision(self):
        self._sra3_shop()
        payload = self.assert_imposition(
            self.preview(width_mm="106.9", height_mm="148.9"),
            width_mm=320, height_mm=450, expected_pps=6, expected_orientation="rotated",
        )
        self.assertEqual(payload["production_preview"]["size_label"], "Business Card 106.9 x 148.9 mm", payload)

    def test_decimal_dimensions_arrive_as_a_dimension_string_too(self):
        self._sra3_shop()
        self.assert_imposition(
            self.preview(finished_size="106.9x148.9mm"),
            width_mm=320, height_mm=450, expected_pps=6, expected_orientation="rotated",
        )

    def test_size_mode_custom_with_dimensions_is_priced(self):
        self._sra3_shop()
        self.assert_imposition(
            self.preview(finished_size="", size_mode="custom", width_mm=123, height_mm=217),
            width_mm=320, height_mm=450, expected_pps=4, expected_orientation="normal",
        )

    def test_different_configured_sheets_produce_different_impositions(self):
        """SRA3 is the default sheet, not a hardcoded constant."""
        results = {}
        for label, (sheet_w, sheet_h, max_w, max_h, sheet_size) in {
            "320x450": (320, 450, 320, 450, "SRA3"),
            "300x420": (300, 420, 320, 450, "A3"),
            "640x900": (640, 900, 700, 950, "CUSTOM"),
        }.items():
            self._only_shop(
                f"sheet-{sheet_w}x{sheet_h}",
                width_mm=sheet_w,
                height_mm=sheet_h,
                max_width_mm=max_w,
                max_height_mm=max_h,
                sheet_size=sheet_size,
            )
            payload = self.assert_imposition(
                self.preview(width_mm=123, height_mm=217),
                width_mm=sheet_w,
                height_mm=sheet_h,
                expected_pps={"320x450": 4, "300x420": 3, "640x900": 16}[label],
                expected_orientation={"320x450": "normal", "300x420": "rotated", "640x900": "normal"}[label],
            )
            results[label] = payload["production_preview"]["pieces_per_sheet"]

        self.assertEqual(len(set(results.values())), 3, results)

    def test_changing_only_the_sheet_label_does_not_change_the_geometry(self):
        """Two papers with identical millimetres but different labels must impose
        identically -- the label must never stand in for the physical size."""
        yields = {}
        for sheet_size in ("SRA3", "A3"):
            self._only_shop(
                f"label-{sheet_size}",
                width_mm=320,
                height_mm=450,
                max_width_mm=320,
                max_height_mm=450,
                sheet_size=sheet_size,
            )
            payload = self.assert_imposition(
                self.preview(width_mm=123, height_mm=217),
                width_mm=320,
                height_mm=450,
                expected_pps=4,
                expected_orientation="normal",
            )
            yields[sheet_size] = payload["production_preview"]["pieces_per_sheet"]
        self.assertEqual(yields["SRA3"], yields["A3"], yields)

    # ------------------------------------------------------------------ size-resolution contract over HTTP

    def test_dimension_string_is_accepted_instead_of_being_unavailable(self):
        self._sra3_shop()
        response = self.preview(finished_size="123x217mm")
        payload = self.assert_imposition(
            response, width_mm=320, height_mm=450, expected_pps=4, expected_orientation="normal"
        )
        self.assertTrue(payload.get("can_calculate"), payload)
        self.assertNotIn("finished_size", payload.get("missing_fields") or [])

    def test_predefined_library_size_still_prices(self):
        self._sra3_shop()
        response = self.preview(finished_size="85x55mm")
        self.assertEqual(response.status_code, 200, response.json())
        self.assertTrue(response.json().get("can_calculate"), response.json())

    def test_unknown_size_string_is_reported_as_missing_not_invented(self):
        """An arbitrary label is not a size and must never become a library size."""
        self._sra3_shop()
        response = self.preview(finished_size="Premium Matt Card")
        self.assertEqual(response.status_code, 200, response.json())
        payload = response.json()
        self.assertFalse(payload.get("can_calculate"), payload)
        self.assertIn("finished_size", payload.get("missing_fields") or [], payload)

    def test_explicit_dimensions_win_over_an_unrecognised_size_label(self):
        self._sra3_shop()
        response = self.preview(finished_size="Premium Matt Card", width_mm=123, height_mm=217)
        self.assert_imposition(
            response, width_mm=320, height_mm=450, expected_pps=4, expected_orientation="normal"
        )

    def test_custom_request_without_dimensions_is_reported_as_missing(self):
        self._sra3_shop()
        response = self.preview(finished_size="custom")
        self.assertEqual(response.status_code, 200, response.json())
        payload = response.json()
        self.assertFalse(payload.get("can_calculate"), payload)
        self.assertEqual(
            sorted(payload.get("missing_fields") or []),
            ["custom_height_mm", "custom_width_mm"],
            payload,
        )