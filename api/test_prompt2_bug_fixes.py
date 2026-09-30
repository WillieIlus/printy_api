"""Regression tests for the 8 confirmed prompt-2 bugs.

Each test names the bug it locks down so a future regression points straight at
the original defect. The imposition, waste-policy and SRA3 geometry maths are
deliberately untouched: these tests only cover input normalisation, paper
selection, projections and the money split.
"""
from __future__ import annotations

from decimal import Decimal

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from api.workflow_serializers import CalculatorConfigPreviewSerializer, PartnerQuotePreviewSerializer
from inventory.models import Paper
from pricing.models import QuantityPricingTier, Shop
from pricing.services.platform_fee_policy import calculate_financial_split
from quotes.guardrails import resolve_partner_markup_amount, validate_partner_markup_amount
from quotes.models import QuoteRequest
from services.pricing.finishing_normalization import normalize_finishing_slug


class Prompt2BugTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user(
            username="mgr", email="mgr@example.com", password="pw", role="partner"
        )
        UserProfile.objects.update_or_create(
            user=self.manager, defaults={"default_markup_rate": Decimal("0.7500")}
        )
        self.shop = Shop.objects.create(
            name="Gutenberg Press",
            slug="gutenberg-press",
            city="Nairobi",
            service_area="Nairobi",
            owner=self.manager,
        )
        for slug in ("rival", "rival-2"):
            Shop.objects.create(
                name=slug, slug=slug, city="Nairobi", service_area="Nairobi", owner=self.manager
            )
        for gsm, name, category in (
            (115, "SRA3 115g Matt", "matt"),
            (170, "SRA3 170g Matt", "matt"),
            (250, "SRA3 250g Matt", "artcard"),
            (350, "SRA3 350g Matt", "artcard"),
        ):
            Paper.objects.create(
                shop=self.shop,
                name=name,
                sheet_size="SRA3",
                gsm=gsm,
                category=category,
                paper_type="MATTE",
                is_active=True,
                buying_price=Decimal("12.00"),
                selling_price=Decimal("20.00"),
            )
        self.client.force_authenticate(user=self.manager)
        # The canonical pricing calculator requires a matching volume tier.
        QuantityPricingTier.objects.create(
            name="Default test tier",
            min_sheets=1,
            max_sheets=None,
            multiplier=Decimal("1.00"),
            minimum_order_floor=Decimal("0.00"),
        )

    def pricing_snapshot(self):
        return {
            "currency": "KES",
            "pricing_source": "instant_book",
            "selected_shops": [
                {
                    "id": self.shop.id,
                    "slug": self.shop.slug,
                    "preview": {
                        "totals": {"shop_total": "1920.00"},
                        "production_cost_inputs": {
                            "quantity": 240,
                            "yield_per_sheet": 30,
                            "paper_cost_per_sheet": "20.00",
                            "click_charge_per_sheet": "30.00",
                            "finishing_cost": "0.00",
                        },
                    },
                }
            ],
        }


class Bug1FinishingNormalizationTests(TestCase):
    """BUG 1: the manager UI sent `matt_lamination`; only the hyphenated slug matched."""

    def test_underscore_slug_from_the_manager_ui_is_normalized(self):
        self.assertEqual(normalize_finishing_slug("matt_lamination"), "matt-lamination")
        self.assertEqual(normalize_finishing_slug("matt-lamination"), "matt-lamination")

    def test_case_and_whitespace_variations_normalize(self):
        self.assertEqual(normalize_finishing_slug("  MATT_LAMINATION "), "matt-lamination")

    def test_bare_vocabulary_maps_to_the_catalogue_slug(self):
        self.assertEqual(normalize_finishing_slug("matt"), "matt-lamination")
        self.assertEqual(normalize_finishing_slug("matte"), "matt-lamination")
        self.assertEqual(normalize_finishing_slug("gloss"), "gloss-lamination")
        self.assertEqual(normalize_finishing_slug("glossy"), "gloss-lamination")

    def test_unknown_slug_is_returned_untouched(self):
        self.assertEqual(normalize_finishing_slug("foil-stamping"), "foil-stamping")


class Bug2MpesaStubModeTests(SimpleTestCase):
    """BUG 2: `.env` pinned MPESA_ENVIRONMENT=sandbox, so the documented stub
    mode could never be reached from the process environment."""

    def _stub(self, environment, force):
        from django.conf import settings
        from payments.services import _is_stub_mode

        original_env = settings.MPESA_ENVIRONMENT
        original_force = getattr(settings, "MPESA_FORCE_STUB", "")
        try:
            settings.MPESA_ENVIRONMENT = environment
            settings.MPESA_FORCE_STUB = force
            return _is_stub_mode()
        finally:
            settings.MPESA_ENVIRONMENT = original_env
            settings.MPESA_FORCE_STUB = original_force

    def test_force_flag_enables_stub_even_in_sandbox(self):
        self.assertTrue(self._stub("sandbox", "1"))
        self.assertTrue(self._stub("sandbox", "true"))

    def test_sandbox_is_still_real_without_the_flag(self):
        self.assertFalse(self._stub("sandbox", "0"))
        self.assertFalse(self._stub("sandbox", ""))

    def test_test_environments_enable_stub_without_the_flag(self):
        for environment in ("test", "testing", "disabled"):
            with self.subTest(environment=environment):
                self.assertTrue(self._stub(environment, ""))


class Bug3PaperIdHonouredTests(Prompt2BugTestBase):
    """BUG 3: `paper_id` was accepted then ignored, silently resolving elsewhere."""

    def test_explicit_paper_id_wins_over_the_gsm_hint(self):
        from services.public_matching import _candidate_papers

        paper_350 = Paper.objects.get(shop=self.shop, gsm=350)
        rows = _candidate_papers(
            self.shop, {"paper_id": paper_350.id, "paper_gsm": 115, "paper_type": "matt"}
        )
        self.assertEqual([row.id for row in rows], [paper_350.id])

    def test_unknown_paper_id_does_not_silently_return_another_paper(self):
        from services.public_matching import _candidate_papers

        rows = _candidate_papers(self.shop, {"paper_id": 999999})
        self.assertEqual(rows, [])

    def test_inactive_paper_id_is_rejected(self):
        from services.public_matching import _candidate_papers

        paper_350 = Paper.objects.get(shop=self.shop, gsm=350)
        paper_350.is_active = False
        paper_350.save(update_fields=["is_active"])
        rows = _candidate_papers(self.shop, {"paper_id": paper_350.id})
        self.assertEqual(rows, [])


class Bug4PaperTypeVocabularyTests(Prompt2BugTestBase):
    """BUG 4: `matt` was matched with iexact only, so it never hit the `MATTE`
    finish value and a 350 GSM request silently fell back to 170."""

    def _best(self, **payload):
        from services.public_matching import _candidate_papers

        rows = _candidate_papers(self.shop, payload)
        return rows[0].gsm if rows else None

    def test_client_vocabulary_matches_the_finish_value(self):
        # "matt" is the category spelling; Paper.paper_type stores "MATTE".
        self.assertEqual(self._best(paper_type="matt", paper_gsm=350), 350)

    def test_finish_vocabulary_still_works(self):
        self.assertEqual(self._best(paper_type="MATTE", paper_gsm=350), 350)
        self.assertEqual(self._best(paper_type="matte", paper_gsm=250), 250)

    def test_gsm_window_match_is_returned(self):
        self.assertEqual(self._best(paper_type="matt", paper_gsm=170), 170)

    def test_impossible_gsm_returns_nothing_instead_of_falling_back(self):
        self.assertIsNone(self._best(paper_type="matt", paper_gsm=999))

    def test_soft_hint_still_falls_back_to_the_closest_stock(self):
        # requested_paper_category/requested_gsm are documented as "shops price
        # with their nearest available stock", so a hard-selection rule must not
        # be applied to them or unheld stock silently drops every shop.
        from services.public_matching import _candidate_papers

        rows = _candidate_papers(
            self.shop,
            {"paper_type": "conqueror", "paper_gsm": 999, "paper_request_is_soft": True},
        )
        self.assertTrue(rows, "a soft hint must still price with closest available stock")


class Bug5MarkupRateTests(Prompt2BugTestBase):
    """BUG 5: only a KES amount was accepted, and the out-of-range message was
    misleading."""

    def test_preview_serializer_accepts_a_markup_rate(self):
        self.assertIn("partner_markup_rate", PartnerQuotePreviewSerializer().fields)

    def test_fraction_rate_resolves_to_the_amount(self):
        self.assertEqual(
            resolve_partner_markup_amount(base_price=Decimal("2000.00"), partner_markup_rate=Decimal("0.75")),
            Decimal("1500.00"),
        )

    def test_percent_rate_resolves_to_the_same_amount(self):
        self.assertEqual(
            resolve_partner_markup_amount(base_price=Decimal("2000.00"), partner_markup_rate=Decimal("75")),
            Decimal("1500.00"),
        )

    def test_profile_default_rate_round_trips(self):
        profile = UserProfile.objects.get(user=self.manager)
        amount = resolve_partner_markup_amount(
            base_price=Decimal("1920.00"), partner_markup_rate=profile.default_markup_rate
        )
        self.assertEqual(amount, Decimal("1440.00"))
        self.assertEqual(
            validate_partner_markup_amount(base_price=Decimal("1920.00"), markup_amount=amount),
            Decimal("0.7500"),
        )

    def test_either_field_must_be_supplied(self):
        with self.assertRaises(ValueError):
            resolve_partner_markup_amount(base_price=Decimal("2000.00"))

    def test_error_message_states_the_accepted_kes_band(self):
        with self.assertRaises(ValueError) as ctx:
            validate_partner_markup_amount(
                base_price=Decimal("2000.00"), markup_amount=Decimal("10.00")
            )
        message = str(ctx.exception)
        self.assertIn("KES", message)
        self.assertIn("partner_markup_rate", message)

    def _preview(self, **extra):
        response = self.client.post(
            "/api/partner/quotes/preview/",
            {"shop": self.shop.id, "pricing_snapshot": self.pricing_snapshot(), **extra},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_rate_is_accepted_through_the_endpoint(self):
        body = self._preview(partner_markup_rate="0.75")
        production = Decimal(body["production_estimate"])
        self.assertGreater(production, 0)
        self.assertGreater(Decimal(body["client_price"]), production)

    def test_rate_matches_the_equivalent_amount(self):
        by_rate = self._preview(partner_markup_rate="0.75")
        by_amount = self._preview(partner_markup="1440.00")
        self.assertEqual(by_rate["client_price"], by_amount["client_price"])
        self.assertEqual(by_rate["production_estimate"], by_amount["production_estimate"])

    def test_higher_rate_yields_a_higher_client_price(self):
        low = Decimal(self._preview(partner_markup_rate="0.10")["client_price"])
        high = Decimal(self._preview(partner_markup_rate="0.50")["client_price"])
        self.assertGreater(high, low)

    def test_legacy_amount_still_works(self):
        body = self._preview(partner_markup="1440.00")
        self.assertIn("client_price", body)


class Bug6FinishedSizeLabelTests(Prompt2BugTestBase):
    """BUG 6: size_label reported the paper sheet instead of the finished size."""

    def _label(self, **payload):
        from services.public_matching import _finished_size_label

        return _finished_size_label(payload, payload.get("product_type", ""))

    def test_business_card_reports_the_finished_size(self):
        self.assertEqual(
            self._label(product_type="business_card", width_mm=85, height_mm=55),
            "Business Card 85 x 55 mm",
        )

    def test_other_products_report_their_own_finished_size(self):
        self.assertEqual(
            self._label(product_type="flyer", width_mm=105, height_mm=148),
            "Flyer 105 x 148 mm",
        )
        self.assertEqual(
            self._label(product_type="poster", width_mm=500, height_mm=700),
            "Poster 500 x 700 mm",
        )

    def test_missing_dimensions_fall_back_to_a_readable_placeholder(self):
        self.assertEqual(self._label(product_type="business_card"), "Business Card")


class Bug7PreviewSplitTests(Prompt2BugTestBase):
    """BUG 7: the preview omitted the printer-side fee, so the visible figures
    did not sum to the client price."""

    def test_preview_publishes_the_full_four_way_split(self):
        response = self.client.post(
            "/api/partner/quotes/preview/",
            {
                "shop": self.shop.id,
                "pricing_snapshot": self.pricing_snapshot(),
                "partner_markup_rate": "0.75",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        for field in ("printer_side_fee", "printer_payout", "printy_fee", "broker_payout", "client_price"):
            self.assertIn(field, body, f"missing {field}")

    def test_published_split_sums_to_the_client_price(self):
        response = self.client.post(
            "/api/partner/quotes/preview/",
            {
                "shop": self.shop.id,
                "pricing_snapshot": self.pricing_snapshot(),
                "partner_markup_rate": "0.75",
            },
            format="json",
        )
        body = response.json()
        total = sum(
            Decimal(body[field])
            for field in ("broker_payout", "printy_fee", "printer_payout")
        )
        self.assertEqual(total, Decimal(body["client_price"]))

    def test_printer_side_fee_matches_the_split_calculation(self):
        split = calculate_financial_split(
            production_cost=Decimal("1920.00"), broker_client_price=Decimal("3360.00")
        )
        self.assertEqual(split["production_fee_component"], Decimal("96.00"))
        self.assertEqual(split["shop_payout"], Decimal("2016.00"))


class Bug8FixedShopSlugTests(Prompt2BugTestBase):
    """BUG 8: `fixed_shop_slug` was never declared on the serializer, so it was
    dropped and every shop came back."""

    def test_serializer_declares_the_field(self):
        self.assertIn("fixed_shop_slug", CalculatorConfigPreviewSerializer().fields)

    def test_pinned_slug_survives_serialization(self):
        serializer = CalculatorConfigPreviewSerializer(
            data={
                "product_type": "business_card",
                "width_mm": 85,
                "height_mm": 55,
                "quantity": 300,
                "fixed_shop_slug": "gutenberg-press",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["fixed_shop_slug"], "gutenberg-press")

    def test_service_pins_to_the_requested_shop(self):
        from services.production_matching import _shop_queryset

        pinned = _shop_queryset({"fixed_shop_slug": "gutenberg-press"})
        self.assertEqual([shop.slug for shop in pinned], ["gutenberg-press"])

    def test_absent_slug_returns_every_shop(self):
        from services.production_matching import _shop_queryset

        self.assertGreater(_shop_queryset({}).count(), 1)


class QuoteRequestFactoryMixin:
    def _quote_request_with_snapshot(self, markup="1440.00"):
        return QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.manager,
            customer_name="Adam Farmer",
            customer_email="adam.farmer@example.com",
            request_snapshot={
                "source": "partner_quote_builder",
                "partner_markup": markup,
                "pricing_snapshot": self.pricing_snapshot(),
            },
        )
