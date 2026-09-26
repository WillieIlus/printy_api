"""Harmonization of the buyer/owner calculator with the printer rate cards.

Regression tests for the reported error:

    Print sides: "double" is not a valid choice.
    Color mode: "full_color" is not a valid choice.

The calculator and the rate cards speak two vocabularies for the same concepts:

- the canonical code space stored on rate cards and used by the pricing
  engine (``print_sides``: ``SIMPLEX``/``DUPLEX``, ``color_mode``:
  ``BW``/``COLOR``),
- the human-friendly name space a client-facing calculator uses
  (``double``/``single`` sides, ``full_color``/``black_only`` colour).

Everyone in the world knows "double sided" means two sides / duplex, so a
calculator field that only accepts the printer jargon must be refused
spelling-compliant. These tests lock in that every calculator input
serializer marries both spaces and normalizes to the canonical codes the
rate cards store, while still rejecting genuinely unknown values.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from api.public_matching_serializers import PublicCalculatorPayloadSerializer
from api.workflow_serializers import CalculatorConfigPreviewSerializer
from shops.models import Shop


class CalculatorSpecHarmonizationTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.shop_a_friendly = None
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Calculator harmonization policy",
            is_active=True,
            printer_fee_rate=Decimal("0.05"),
            broker_margin_fee_rate=Decimal("0.15"),
            add_platform_fee_on_top=False,
        )
        self._create_shop_with_pricing("Calc Shop A", "calc-shop-a", single="35.00", double="70.00")
        self._create_shop_with_pricing("Calc Shop B", "calc-shop-b", single="45.00", double="90.00")

    def _create_shop_with_pricing(self, name, slug, *, single, double):
        owner = User.objects.create_user(email=f"{slug}-owner@test.com", password="pass12345", role=User.Role.PRODUCTION)
        shop = Shop.objects.create(
            owner=owner,
            name=name,
            slug=slug,
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        machine = Machine.objects.create(
            shop=shop,
            name=f"{name} Press",
            max_width_mm=320,
            max_height_mm=450,
            is_active=True,
        )
        Paper.objects.create(
            shop=shop,
            name="300gsm Gloss",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("1.00"),
            selling_price=Decimal("20.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
            is_default=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal(single),
            double_price=Decimal(double),
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

    def _public_preview_payload(self, print_sides, color_mode):
        return {
            "product_type": "business_card",
            "quantity": 100,
            "finished_size": "90x55mm",
            "requested_paper_category": "gloss",
            "requested_gsm": 300,
            "print_sides": print_sides,
            "color_mode": color_mode,
        }

    # ------------------------------------------------------------------ serializers

    def test_calculator_config_preview_serializer_marries_friendly_labels_to_rate_card_codes(self):
        friendly = CalculatorConfigPreviewSerializer(
            data={
                "product_type": "business_card",
                "quantity": 100,
                "print_sides": "double",
                "color_mode": "full_color",
            }
        )
        canonical = CalculatorConfigPreviewSerializer(
            data={
                "product_type": "business_card",
                "quantity": 100,
                "print_sides": "DUPLEX",
                "color_mode": "COLOR",
            }
        )

        self.assertTrue(friendly.is_valid(), friendly.errors)
        self.assertTrue(canonical.is_valid(), canonical.errors)
        self.assertEqual(friendly.validated_data["print_sides"], "DUPLEX")
        self.assertEqual(friendly.validated_data["color_mode"], "COLOR")
        self.assertEqual(canonical.validated_data["print_sides"], "DUPLEX")
        self.assertEqual(canonical.validated_data["color_mode"], "COLOR")

    def test_calculator_config_preview_serializer_still_rejects_unknown_sides_and_colour(self):
        serializer = CalculatorConfigPreviewSerializer(
            data={
                "product_type": "business_card",
                "quantity": 100,
                "print_sides": "three-sided",
                "color_mode": "rgb",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("not a valid choice", str(serializer.errors["print_sides"]))
        self.assertIn("not a valid choice", str(serializer.errors["color_mode"]))

    def test_public_calculator_payload_serializer_marries_friendly_labels_to_rate_card_codes(self):
        friendly = PublicCalculatorPayloadSerializer(
            data={"product_type": "business_card", "print_sides": "double", "colour_mode": "full_color"}
        )
        canonical = PublicCalculatorPayloadSerializer(
            data={"product_type": "business_card", "print_sides": "DUPLEX", "colour_mode": "COLOR"}
        )

        self.assertTrue(friendly.is_valid(), friendly.errors)
        self.assertTrue(canonical.is_valid(), canonical.errors)
        self.assertEqual(friendly.validated_data["print_sides"], "DUPLEX")
        self.assertEqual(friendly.validated_data["colour_mode"], "COLOR")
        self.assertEqual(canonical.validated_data["print_sides"], "DUPLEX")
        self.assertEqual(canonical.validated_data["colour_mode"], "COLOR")

    def test_public_calculator_payload_serializer_still_rejects_unknown_sides_and_colour(self):
        serializer = PublicCalculatorPayloadSerializer(
            data={"product_type": "business_card", "print_sides": "three-sided", "colour_mode": "rgb"}
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("not a valid choice", str(serializer.errors["print_sides"]))
        self.assertIn("not a valid choice", str(serializer.errors["colour_mode"]))

    # ------------------------------------------------------------------ endpoints

    def test_public_calculator_preview_prices_friendly_labels_despite_matching_rate_cards(self):
        """Reproduces the reported error: friendly client-facing names
        ("double" sides / "full_color") must be married to the backend rate
        card codes (DUPLEX / COLOR) instead of 400ing with
        'is not a valid choice'."""
        response = self.client.post(
            "/api/calculator/public-preview/",
            self._public_preview_payload("double", "full_color"),
            format="json",
        )

        assert response.status_code == 200, (
            f"public calculator preview returned {response.status_code} {response.json()} — "
            "the calculator's friendly labels ('double'/'full_color') are rejected by "
            "CalculatorConfigPreviewSerializer while the rate cards store DUPLEX/COLOR."
        )
        payload = response.json()
        self.assertGreaterEqual(payload["matches_count"], 1)
        self.assertIsNotNone(payload["market_range"])
        self.assertIsNotNone(payload["market_range"]["min"])

    def test_public_calculator_preview_friendly_labels_price_identically_to_codes(self):
        friendly = self.client.post(
            "/api/calculator/public-preview/",
            self._public_preview_payload("double", "full_color"),
            format="json",
        ).json()
        canonical = self.client.post(
            "/api/calculator/public-preview/",
            self._public_preview_payload("DUPLEX", "COLOR"),
            format="json",
        ).json()

        for field in ("matches_count", "min_price", "max_price", "estimate_median"):
            self.assertEqual(friendly.get(field), canonical.get(field), field)
        self.assertEqual(friendly["market_range"]["min"], canonical["market_range"]["min"])
        self.assertEqual(friendly["market_range"]["max"], canonical["market_range"]["max"])

    def test_public_calculator_preview_still_rejects_unknown_sides_and_colour(self):
        response = self.client.post(
            "/api/calculator/public-preview/",
            self._public_preview_payload("three-sided", "rgb"),
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a valid choice", str(response.json()))

    def test_public_calculator_preview_prices_without_any_paper_choice(self):
        """Buyers no longer pick a paper stock in the calculator, and a missing
        paper choice must never block pricing: the shop's closest/default stock
        prices the job (a printer prints even without holding the paper)."""
        response = self.client.post(
            "/api/calculator/public-preview/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x55mm",
                "print_sides": "DUPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )

        assert response.status_code == 200, (
            f"public calculator preview without a paper choice returned "
            f"{response.status_code} {response.json()} — paper must never block pricing."
        )
        payload = response.json()
        self.assertTrue(payload["can_calculate"])
        self.assertNotIn("paper_stock", payload["missing_fields"])
        self.assertGreaterEqual(payload["matches_count"], 1)
        self.assertIsNotNone(payload["market_range"])
        self.assertIsNotNone(payload["market_range"]["min"])
        for match in payload["matches"]:
            production = match.get("production_preview") or {}
            self.assertGreaterEqual(production.get("sheets_required", 1), 1, match)

    def test_public_calculator_preview_prices_with_no_matching_requested_stock(self):
        """Requesting a paper the shop does not hold must not exclude it: shops
        price with their closest available stock ('closest available stock')."""
        response = self.client.post(
            "/api/calculator/public-preview/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x55mm",
                "requested_paper_category": "conqueror",
                "requested_gsm": 999,
                "print_sides": "DUPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )

        assert response.status_code == 200, (
            f"public calculator preview with an unmatched paper request returned "
            f"{response.status_code} {response.json()} — the requested stock must not block pricing."
        )
        payload = response.json()
        self.assertTrue(payload["can_calculate"])
        self.assertGreaterEqual(payload["matches_count"], 1)
        self.assertIsNotNone(payload["market_range"])
        self.assertIsNotNone(payload["market_range"]["min"])

    def test_legacy_paper_stock_draft_is_mapped_to_category_and_gsm(self):
        """Pre-refactor drafts carried a `paper_stock` key. The public calculator
        must map it onto the modern paper request vocabulary (category + gsm) at
        pricing time instead of requiring a paper_stock field, so old drafts keep
        pricing on the shop's closest available SRA3 stock."""
        response = self.client.post(
            "/api/calculator/public-preview/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x55mm",
                "paper_stock": "300gsm",
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["can_calculate"], payload)
        self.assertNotIn("paper_stock", payload["missing_fields"], payload)
        self.assertGreaterEqual(payload["matches_count"], 1, payload)
        for match in payload["matches"]:
            production = match.get("production_preview") or {}
            self.assertEqual(production.get("parent_sheet"), "SRA3", match)
            self.assertGreaterEqual(production.get("sheets_required") or 0, 1, match)

    def test_public_preview_returns_imposition_preview(self):
        """The preview must return the full imposition back to the client — the
        sheet layout the price is built from: pieces per sheet, layout
        (cols x rows, orientation), bleed, press sheet, good sheets and the
        spoilage split (fixed + variable) that produce the billable sheet count.
        This locks in the 'How your sheet is laid out' disclosure so it can
        never silently drop out of the public response again."""
        response = self.client.post(
            "/api/calculator/public-preview/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x55mm",
                "requested_paper_category": "matt",
                "requested_gsm": 300,
                "print_sides": "DUPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )
        assert response.status_code == 200, response.json()
        payload = response.json()
        production = payload.get("production_preview") or {}
        self.assertGreaterEqual(production.get("pieces_per_sheet") or 0, 1, payload)
        self.assertGreaterEqual(production.get("sheets_required") or 0, 1, payload)
        self.assertGreaterEqual(production.get("good_sheets") or 0, 1, payload)
        self.assertTrue(production.get("parent_sheet"), payload)
        self.assertIn(production.get("cutting_required"), (True, False, None), payload)
        self.assertEqual(production.get("good_sheets"), production.get("sheets_required"), payload)
        self.assertEqual(
            production.get("billable_sheets"),
            (production.get("good_sheets") or 0) + (production.get("waste_sheets_added") or 0),
            payload,
        )
        self.assertGreaterEqual(production.get("billable_sheets") or 0, production.get("good_sheets") or 0, payload)
        self.assertGreaterEqual(production.get("bleed_mm") or 0, 0, payload)
        layout = production.get("layout") or {}
        self.assertEqual(
            (layout.get("cols") or 0) * (layout.get("rows") or 0),
            production.get("pieces_per_sheet"),
            payload,
        )
        self.assertIn(layout.get("orientation"), ("normal", "rotated"), payload)
        press_sheet = production.get("press_sheet") or {}
        self.assertGreaterEqual(press_sheet.get("width_mm") or 0, 1, payload)
        self.assertGreaterEqual(press_sheet.get("height_mm") or 0, 1, payload)
        for match in payload.get("matches") or []:
            row = match.get("production_preview") or {}
            self.assertGreaterEqual(row.get("pieces_per_sheet") or 0, 1, match)
            self.assertGreaterEqual(row.get("good_sheets") or 0, 1, match)
            self.assertGreaterEqual(row.get("billable_sheets") or 0, row.get("good_sheets") or 0, match)
        first_match = (payload.get("matches") or [{}])[0].get("production_preview") or {}
        for key in ("good_sheets", "waste_sheets_added", "billable_sheets", "pieces_per_sheet", "sheets_required"):
            self.assertEqual(production.get(key), first_match.get(key), f"top-level {key} must match the first match")

    def test_public_preview_imposition_discloses_waste_policy_math(self):
        """The spoilage split the price is built from must be disclosed exactly:
        good sheets + (fixed setup sheets + variable % of good) = billable
        sheets. Locked against the seeded default waste policy (2 fixed + 10%)."""
        from math import ceil

        from decimal import Decimal as D

        response = self.client.post(
            "/api/calculator/public-preview/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x55mm",
                "requested_paper_category": "gloss",
                "requested_gsm": 300,
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )
        assert response.status_code == 200, response.json()
        production = response.json()["production_preview"]

        good = production["good_sheets"]
        copies = production["pieces_per_sheet"]
        self.assertEqual(good, ceil(100 / copies))
        self.assertEqual(production["sheets_required"], good)
        self.assertEqual(production["waste_sheets_added"], production["fixed_waste_sheets"] + production["variable_waste_sheets"])
        variable_rate = D(str(production["variable_waste_rate"]))
        self.assertEqual(production["variable_waste_sheets"], ceil(good * variable_rate))
        self.assertEqual(production["billable_sheets"], good + production["waste_sheets_added"])
        self.assertGreaterEqual(production["waste_sheets_added"], production["fixed_waste_sheets"])

        same_as_first_match = self.client.post(
            "/api/calculator/public-preview/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x55mm",
                "requested_paper_category": "gloss",
                "requested_gsm": 300,
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        ).json()
        first = (same_as_first_match["matches"] or [{}])[0].get("production_preview") or {}
        for key in ("good_sheets", "fixed_waste_sheets", "variable_waste_sheets", "waste_sheets_added", "billable_sheets", "pieces_per_sheet"):
            self.assertEqual(production[key], first[key], key)

    def test_calculator_config_advertises_optional_paper_request_fields(self):
        response = self.client.get("/api/calculator/config/")
        self.assertEqual(response.status_code, 200)
        config = response.json()
        self.assertNotIn("paper_stocks", config)
        products = config["products"]
        for product in products:
            self.assertNotIn("paper_stock", product["required_fields"], product["key"])
            self.assertNotIn("cover_stock", product["required_fields"], product["key"])
            self.assertNotIn("insert_stock", product["required_fields"], product["key"])
            field_keys = [f["key"] for f in product["fields"]]
            # Paper is requested by category + gsm; technical cover/insert stock
            # stays internal and the old paper_stock field is gone.
            self.assertNotIn("paper_stock", field_keys, product["key"])
            self.assertNotIn("cover_stock", field_keys, product["key"])
            self.assertNotIn("insert_stock", field_keys, product["key"])
            if product["key"] in ("business_card", "flyer", "label_sticker", "letterhead"):
                self.assertIn("requested_paper_category", field_keys, product["key"])
                self.assertIn("requested_gsm", field_keys, product["key"])

    # ------------------------------------------------------ finished size independence

    def test_every_advertised_finished_size_resolves_to_dimensions(self):
        """Every finished-size option the calculator advertises for flat products
        must resolve to piece dimensions, so the size can be imposed onto an SRA3
        sheet and priced. A size the buyer can pick must never dead-end in a
        'missing finished size' response."""
        from services.pricing.calculator_config import SIZE_LIBRARY, resolve_finished_size

        for product_key in ("business_card", "flyer"):
            advertised = SIZE_LIBRARY[product_key]
            self.assertGreaterEqual(len(advertised), 2, product_key)
            for option in advertised:
                resolved = resolve_finished_size(product_key, option["value"])
                self.assertIsNotNone(resolved, f"{product_key} {option['value']} did not resolve")
                self.assertGreater(resolved["width_mm"], 0)
                self.assertGreater(resolved["height_mm"], 0)

    def test_finished_size_never_blocks_shops_from_pricing(self):
        """The calculator advertises business-card sizes 90x55 and 85x55 and the
        flyer A-series plus DL. Choosing any of these — none of them big enough to
        fall off an SRA3 parent sheet — must yield matched shops with a price. The
        finished size is imposed onto SRA3 and sheet count plus cutting is what is
        priced; shops must never be excluded just because of the finished size."""
        sizes_to_prices = {
            "business_card": {
                "quantity": 100,
                "print_sides": "DUPLEX",
                "color_mode": "COLOR",
            },
            "flyer": {
                "quantity": 250,
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
        }
        expected_sizes = {
            "business_card": {"90x55mm", "85x55mm"},
            "flyer": {"A6", "A5", "DL", "A4", "A3"},
        }

        for product_type, base in sizes_to_prices.items():
            with self.subTest(product_type=product_type):
                for size in expected_sizes[product_type]:
                    with self.subTest(product_type=product_type, size=size):
                        response = self.client.post(
                            "/api/calculator/public-preview/",
                            {
                                "product_type": product_type,
                                "finished_size": size,
                                "requested_paper_category": "gloss",
                                "requested_gsm": 300,
                                **base,
                            },
                            format="json",
                        )
                        assert response.status_code == 200, (
                            f"public preview for {product_type} {size} returned "
                            f"{response.status_code} {response.json()} — the finished size blocked pricing."
                        )
                        payload = response.json()
                        self.assertGreaterEqual(payload["matches_count"], 1, size)
                        self.assertIsNotNone(payload["market_range"], size)
                        self.assertIsNotNone(payload["market_range"]["min"], size)
                        self.assertIsNotNone(payload["total"], size)

                        for match in payload["matches"]:
                            production = match.get("production_preview") or {}
                            self.assertGreaterEqual(production.get("pieces_per_sheet", 1), 1, size)
                            self.assertGreaterEqual(production.get("sheets_required", 1), 1, size)
                            self.assertEqual(production.get("parent_sheet"), "SRA3", size)

    def test_cutting_is_priced_for_sheet_products_with_a_cutting_path(self):
        """Printing an imposed SRA3 sheet is only half the story — cutting the sheet
        down to the finished size must show up in the quoted total, so the price the
        shop gives is for the final pieces (sheets plus cutting), not just raw
        sheets. Shops without a cutting path are still allowed to quote."""
        from services.public_matching import get_marketplace_matches

        response = get_marketplace_matches(
            {
                "calculator_mode": "marketplace",
                "product_family": "flat",
                "product_type": "business_card",
                "pricing_mode": "custom",
                "product_pricing_mode": "SHEET",
                "quantity": 100,
                "size_mode": "standard",
                "size_label": "90x55mm",
                "width_mm": 90,
                "height_mm": 55,
                "sides": "DUPLEX",
                "color_mode": "COLOR",
                "paper_type": "gloss",
                "paper_gsm": 300,
                "finishing_slugs": [],
            }
        )
        self.assertGreaterEqual(response["matches_count"], 1)

        preview = response["matches"][0]["preview"]
        finishings = (preview.get("breakdown") or {}).get("finishings") or []
        cutting = [f for f in finishings if "cut" in (f.get("slug") or "").lower()]
        self.assertTrue(cutting, "cutting the imposed sheet must part of the business card price")
        cutting_total = Decimal(cutting[0]["total"])
        self.assertGreater(cutting_total, 0)

        good_sheets = Decimal(preview.get("good_sheets") or 0)
        per_sheet_paper_and_print = Decimal("20.00") * good_sheets + Decimal("70.00") * good_sheets
        grand_total = Decimal((preview.get("totals") or {}).get("grand_total"))
        self.assertGreaterEqual(
            grand_total,
            per_sheet_paper_and_print + cutting_total,
            "the quoted total must include cutting on top of the imposed sheets",
        )

        # A shop without any cutting path must never be blocked from quoting:
        # it still prices the imposed sheets and cutting is simply not added.
        FinishingRate.objects.update(is_active=False)
        without_cutting = get_marketplace_matches(
            {
                "calculator_mode": "marketplace",
                "product_family": "flat",
                "product_type": "business_card",
                "pricing_mode": "custom",
                "product_pricing_mode": "SHEET",
                "quantity": 100,
                "size_mode": "standard",
                "size_label": "90x55mm",
                "width_mm": 90,
                "height_mm": 55,
                "sides": "DUPLEX",
                "color_mode": "COLOR",
                "paper_type": "gloss",
                "paper_gsm": 300,
                "finishing_slugs": [],
            }
        )
        self.assertGreaterEqual(
            without_cutting["matches_count"],
            1,
            "a shop without a cutting finishing path must still be allowed to quote",
        )

    # ------------------------------------------------------ remaining boundaries

    def test_every_calculator_boundary_field_marries_friendly_labels_to_rate_card_codes(self):
        """No serializer that a manager/broker or calculator can hit may still reject
        'double'/'full_color' — the friendly client-facing names must map onto the
        canonical rate-card codes (DUPLEX/COLOR) across the whole boundary."""
        from api.serializers import MatchShopsInputSerializer, QuoteCalculatorInputSerializer, TweakAndAddSerializer
        from client_calculator.serializers import ClientCalculatorInputSerializer

        for serializer_cls in (
            MatchShopsInputSerializer,
            QuoteCalculatorInputSerializer,
            TweakAndAddSerializer,
            ClientCalculatorInputSerializer,
        ):
            instance = serializer_cls()
            sides_field = instance.fields["sides"]
            color_field = instance.fields["color_mode"]
            self.assertEqual(sides_field.run_validation("double"), "DUPLEX", serializer_cls.__name__)
            self.assertEqual(color_field.run_validation("full_color"), "COLOR", serializer_cls.__name__)

    def test_model_choice_enums_resolve_friendly_spellings(self):
        """The model layer itself owns the friendly vocabulary, so the rate card and
        quote item models know 'full_color'/'single color'/'black and white' and
        'double'/'single' sides without a serializer in between."""
        from pricing.choices import ColorMode, Sides

        self.assertEqual(ColorMode.from_friendly("full_color"), "COLOR")
        self.assertEqual(ColorMode.from_friendly("full colour"), "COLOR")
        self.assertEqual(ColorMode.from_friendly("black_only"), "BW")
        self.assertEqual(ColorMode.from_friendly("single color"), "BW")
        self.assertEqual(ColorMode.from_friendly("black and white"), "BW")
        self.assertEqual(ColorMode.from_friendly("COLOR"), "COLOR")
        self.assertEqual(ColorMode.from_friendly("BW"), "BW")
        self.assertEqual(ColorMode.from_friendly("rgb"), None)

        self.assertEqual(Sides.from_friendly("double"), "DUPLEX")
        self.assertEqual(Sides.from_friendly("double-sided"), "DUPLEX")
        self.assertEqual(Sides.from_friendly("single"), "SIMPLEX")
        self.assertEqual(Sides.from_friendly("DUPLEX"), "DUPLEX")
        self.assertEqual(Sides.from_friendly("three-sided"), None)