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