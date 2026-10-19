"""The manager's shop pricing results must carry the imposed sheet (prompt).

When a manager searches printer shops and gets production prices, the pricing
result shows the imposition layout that produced that quote: the parent-sheet
outline, the cols x rows grid, the sheet margins, the designs per sheet, the
good/spoilage/billed sheet counts. These tests pin the contract:

* every priced shop-options row carries ``production_preview``;
* it is a projection of the SAME engine result (``preview_snapshot``), built
  once by the canonical extractor -- never re-derived (law 1.1);
* the margin bands are the even leftover of the engine's own fit (nothing is
  re-fitted here);
* diagnostics rows carry no sheet to draw;
* the layout is engine-derived. The prompt's example numbers (4-up A5, 25-up
  cards) are illustrations, never hardcoded expectations: A5 on SRA3 really is
  2x2 = 4, while an 85x55mm card yields whatever the engine computes (21-up on
  a 320x450 sheet with 3mm bleed), and the tests assert consistency with the
  engine rather than a fixed count.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PrintingRate
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from services.pricing.calculator_preview import production_preview_from_engine_payload
from services.pricing.imposition import build_imposition_breakdown
from shops.models import Shop


def _engine_payload(imposition_breakdown, sheet_label="SRA3", sheet_width=320, sheet_height=450):
    """The engine-shaped dict ``PricingEngineResult.to_dict()`` produces."""
    return {
        "pricing_mode": "SHEET",
        "quantity": 100,
        "currency": "KES",
        "totals": {"subtotal": "900.00"},
        "breakdown": {
            "paper": {
                "label": sheet_label,
                "sheet_size": sheet_label,
                "width_mm": sheet_width,
                "height_mm": sheet_height,
            },
            "imposition": {
                **imposition_breakdown.to_dict(),
                "layout": {
                    "cols": imposition_breakdown.cols,
                    "rows": imposition_breakdown.rows,
                },
                "good_sheets": imposition_breakdown.good_sheets,
                "billable_sheets": imposition_breakdown.good_sheets,
            },
            "printing": {},
            "finishings": [],
        },
        "explanations": [],
        "copies_per_sheet": imposition_breakdown.copies_per_sheet,
        "good_sheets": imposition_breakdown.good_sheets,
        "billable_sheets": imposition_breakdown.good_sheets,
        "parent_sheet_name": sheet_label,
        "rotated": imposition_breakdown.orientation == "rotated",
    }


class SheetProjectionMathTests(TestCase):
    """The projection is derived from the engine's own fit numbers, only."""

    def test_normal_fit_margins_split_the_leftover_evenly(self):
        # 148x210mm A5 + 3mm bleed -> 154x216mm on SRA3: 2x2 with 6/9mm bands.
        breakdown = build_imposition_breakdown(
            quantity=500,
            finished_width_mm=148,
            finished_height_mm=210,
            sheet_width_mm=320,
            sheet_height_mm=450,
            bleed_mm=3,
        )
        preview = production_preview_from_engine_payload(_engine_payload(breakdown), "flyer")

        self.assertEqual(preview["layout"], {"cols": 2, "rows": 2, "orientation": "normal"})
        self.assertEqual(preview["pieces_per_sheet"], 4)
        self.assertEqual(preview["press_sheet"], {"label": "SRA3", "width_mm": 320, "height_mm": 450})
        self.assertEqual(preview["finished_piece"], {"width_mm": 148.0, "height_mm": 210.0})
        self.assertEqual(preview["margins_mm"], {"horizontal": 6.0, "vertical": 9.0})

    def test_rotated_fit_swaps_the_axes_of_the_margin_maths(self):
        # 400x100mm does not fit upright on 320x450 but fits rotated:
        # 3 across (100mm side) x 1 down (400mm side) -> 10/25mm bands.
        breakdown = build_imposition_breakdown(
            quantity=100,
            finished_width_mm=400,
            finished_height_mm=100,
            sheet_width_mm=320,
            sheet_height_mm=450,
            bleed_mm=0,
        )
        preview = production_preview_from_engine_payload(_engine_payload(breakdown), "flyer")

        self.assertEqual(breakdown.orientation, "rotated")
        self.assertEqual(preview["layout"]["orientation"], "rotated")
        self.assertEqual(preview["layout"]["cols"], 3)
        self.assertEqual(preview["layout"]["rows"], 1)
        self.assertEqual(preview["margins_mm"], {"horizontal": 10.0, "vertical": 25.0})

    def test_no_fit_has_no_margin_band_to_draw(self):
        breakdown = build_imposition_breakdown(
            quantity=1000,
            finished_width_mm=500,
            finished_height_mm=600,
            sheet_width_mm=320,
            sheet_height_mm=450,
            bleed_mm=3,
        )
        preview = production_preview_from_engine_payload(_engine_payload(breakdown), "flyer")

        self.assertFalse(breakdown.fits)
        self.assertIsNone(preview["margins_mm"])
        # No fit is shown as an empty grid, never as a fake 1-up sheet.
        self.assertEqual(preview["layout"]["cols"], 0)
        self.assertEqual(preview["layout"]["rows"], 0)

    def test_empty_engine_payload_returns_nothing_instead_of_raising(self):
        self.assertIsNone(production_preview_from_engine_payload(None, "flyer"))
        self.assertIsNone(production_preview_from_engine_payload({}, "flyer"))


class ManagerImpositionPreviewHttpTests(TestCase):
    """The shop-options response carries the sheet with every priced option."""

    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user(
            email="imposition-manager@test.com",
            password="pass12345",
            role="broker",
            partner_profile_enabled=True,
            name="Imposition Manager",
        )
        self.end_client = User.objects.create_user(
            email="imposition-client@test.com",
            password="pass12345",
            role="client",
            name="End Client",
        )
        self.priced_shop = self._create_shop("Priced Shop", "imposition-priced", paper_price="20.00", with_print_rate=True)
        self.unpriced_shop = self._create_shop("Needs Setup Shop", "imposition-needs-setup", paper_price="25.00", with_print_rate=False)
        self.quote_request = QuoteRequest.objects.create(
            shop=None,
            created_by=self.end_client,
            assigned_manager=self.manager,
            customer_name="End Client",
            customer_email=self.end_client.email,
            status=QuoteStatus.SUBMITTED,
            request_snapshot={
                "source": "manager_led_intake",
                "calculator_inputs": {
                    "product_type": "business_card",
                    "quantity": 100,
                    "finished_size": "85x55mm",
                    "requested_paper_category": "gloss",
                    "requested_gsm": 300,
                    "print_sides": "SIMPLEX",
                    "color_mode": "COLOR",
                },
            },
        )

    def _create_shop(self, name: str, slug: str, *, paper_price: str, with_print_rate: bool):
        owner = User.objects.create_user(email=f"{slug}@test.com", password="pass12345", role="shop_owner")
        shop = Shop.objects.create(
            owner=owner, name=name, slug=slug, is_active=True, city="Nairobi", service_area="Westlands"
        )
        machine = Machine.objects.create(
            shop=shop, name=f"{name} Press", max_width_mm=320, max_height_mm=450, is_active=True
        )
        Paper.objects.create(
            shop=shop,
            name="300gsm Art Card",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            buying_price=Decimal("10.00"),
            selling_price=Decimal(paper_price),
            width_mm=320,
            height_mm=450,
            is_active=True,
        )
        if with_print_rate:
            PrintingRate.objects.create(
                machine=machine,
                sheet_size="SRA3",
                color_mode="COLOR",
                single_price=Decimal("35.00"),
                double_price=Decimal("70.00"),
                is_active=True,
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

    def _shop_options(self, quote_request=None):
        self.client.force_authenticate(user=self.manager)
        return self.client.post(
            f"/api/dashboard/partner/quotes/{(quote_request or self.quote_request).id}/shop-options/",
            {},
            format="json",
        )

    def _priced_rows(self, quote_request=None):
        response = self._shop_options(quote_request)
        self.assertEqual(response.status_code, 200)
        return [row for row in response.json()["results"] if row.get("price_status") == "priced"]

    def _flyer_a5_request(self):
        return QuoteRequest.objects.create(
            shop=None,
            created_by=self.end_client,
            assigned_manager=self.manager,
            customer_name="End Client",
            customer_email=self.end_client.email,
            status=QuoteStatus.SUBMITTED,
            request_snapshot={
                "source": "manager_led_intake",
                "calculator_inputs": {
                    "product_type": "flyer",
                    "quantity": 500,
                    # The manager intake snapshot resolves WxH strings directly;
                    # the "A5" LIBRARY LABEL is not resolved on this path (a
                    # pre-existing behaviour documented in results.txt, it
                    # yields 0x0mm and prices nothing). 148x210mm is A5.
                    "finished_size": "148x210mm",
                    "requested_paper_category": "gloss",
                    "requested_gsm": 300,
                    "print_sides": "SIMPLEX",
                    "color_mode": "COLOR",
                },
            },
        )

    def test_every_priced_row_carries_the_sheet_projection(self):
        rows = self._priced_rows()
        self.assertTrue(rows, "expected at least one priced shop option")

        for row in rows:
            preview = row.get("production_preview")
            self.assertIsInstance(preview, dict, f"{row['shop_name']} has no production_preview")
            for key in (
                "pieces_per_sheet",
                "layout",
                "press_sheet",
                "imposition_label",
                "good_sheets",
                "waste_sheets_added",
                "billable_sheets",
                "finished_piece",
                "margins_mm",
            ):
                self.assertIn(key, preview, f"{row['shop_name']} projection missing {key}")
            self.assertIsInstance(preview["layout"], dict)
            self.assertTrue(preview["layout"]["cols"] >= 1)
            self.assertTrue(preview["layout"]["rows"] >= 1)

    def test_projection_is_the_same_engine_result_as_the_snapshot(self):
        for row in self._priced_rows():
            projection = row["production_preview"]
            engine = row["preview_snapshot"]
            imposition = engine["breakdown"]["imposition"]

            self.assertEqual(projection["pieces_per_sheet"], engine["copies_per_sheet"])
            self.assertEqual(projection["pieces_per_sheet"], imposition["cols"] * imposition["rows"])
            self.assertEqual(projection["good_sheets"], engine["good_sheets"])
            self.assertEqual(projection["billable_sheets"], engine["billable_sheets"])
            self.assertEqual(projection["layout"]["cols"], imposition["layout"]["cols"])
            self.assertEqual(projection["layout"]["rows"], imposition["layout"]["rows"])
            self.assertEqual(projection["press_sheet"]["width_mm"], engine["breakdown"]["paper"]["width_mm"])
            self.assertEqual(projection["press_sheet"]["height_mm"], engine["breakdown"]["paper"]["height_mm"])
            self.assertEqual(projection["imposition_label"], imposition["explanation"])

    def test_margins_are_the_even_leftover_of_the_engine_fit(self):
        for row in self._priced_rows():
            projection = row["production_preview"]
            imposition = row["preview_snapshot"]["breakdown"]["imposition"]
            margins = projection["margins_mm"]
            self.assertIsNotNone(margins, f"{row['shop_name']} has no margin band")
            self.assertGreaterEqual(margins["horizontal"], 0)
            self.assertGreaterEqual(margins["vertical"], 0)

            bleed = float(imposition["bleed_mm"])
            piece_w = float(imposition["finished_width_mm"]) + 2 * bleed
            piece_h = float(imposition["finished_height_mm"]) + 2 * bleed
            sheet_w = float(imposition["sheet_width_mm"])
            sheet_h = float(imposition["sheet_height_mm"])
            cols = projection["layout"]["cols"]
            rows = projection["layout"]["rows"]

            self.assertAlmostEqual(2 * margins["horizontal"] + cols * piece_w, sheet_w, places=2)
            self.assertAlmostEqual(2 * margins["vertical"] + rows * piece_h, sheet_h, places=2)
            self.assertEqual(
                projection["finished_piece"],
                {
                    "width_mm": float(imposition["finished_width_mm"]),
                    "height_mm": float(imposition["finished_height_mm"]),
                },
            )

    def test_a5_flyer_on_sra3_is_two_by_two_with_even_margins(self):
        """The prompt's example, produced by the engine -- not hardcoded here."""
        rows = self._priced_rows(self._flyer_a5_request())
        self.assertTrue(rows)
        preview = rows[0]["production_preview"]

        self.assertEqual(preview["layout"], {"cols": 2, "rows": 2, "orientation": "normal"})
        self.assertEqual(preview["pieces_per_sheet"], 4)
        self.assertEqual(preview["press_sheet"]["width_mm"], 320)
        self.assertEqual(preview["press_sheet"]["height_mm"], 450)
        self.assertTrue(preview["press_sheet"]["label"].startswith("SRA3"))
        self.assertEqual(preview["margins_mm"], {"horizontal": 6.0, "vertical": 9.0})

    def test_business_card_grid_is_engine_derived_not_a_fixed_example(self):
        # The prompt mentions a 25-up card example; the engine is free to
        # decide otherwise (85x55mm + 3mm bleed on 320x450 = 21-up: 3x7).
        # The contract is consistency with the engine, never a fixed count.
        rows = self._priced_rows()
        self.assertTrue(rows)
        projection = rows[0]["production_preview"]

        self.assertEqual(projection["pieces_per_sheet"], projection["layout"]["cols"] * projection["layout"]["rows"])
        self.assertGreaterEqual(projection["pieces_per_sheet"], 1)

    def test_diagnostic_rows_carry_no_sheet_to_draw(self):
        self.client.force_authenticate(user=self.manager)
        response = self._shop_options()
        self.assertEqual(response.status_code, 200)
        rows = {row["shop_name"]: row for row in response.json()["results"]}

        unpriced = rows["Needs Setup Shop"]
        self.assertEqual(unpriced["price_status"], "missing_pricing")
        self.assertIsNone(unpriced.get("production_preview"))
        self.assertIsNone(unpriced.get("preview_snapshot"))

    def test_inactive_or_unpriced_shops_never_leak_into_the_diagram(self):
        self.priced_shop.is_active = False
        self.priced_shop.save()
        try:
            response = self._shop_options()
            self.assertEqual(response.status_code, 200)
            for row in response.json()["results"]:
                self.assertFalse(row.get("eligible"))
                self.assertIsNone(row.get("production_preview"))
        finally:
            self.priced_shop.is_active = True
            self.priced_shop.save()
