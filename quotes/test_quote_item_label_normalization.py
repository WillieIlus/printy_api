"""Regression guards for the friendly label space reaching the QuoteItem model layer.

History: calculator snapshots may carry human-friendly labels such as
``print_sides="double"`` and ``color_mode="full_color"``. Serializer entry points
normalize these to the canonical codes SIMPLEX/DUPLEX and BW/COLOR
(``PrintSidesField``/``ColorModeField`` in ``api/spec_choice_fields.py``), but the
quote-item builder functions wrote the raw snapshot values straight into
``QuoteItem.sides``/``QuoteItem.color_mode``, whose Django model choices only accept
the canonical codes. Any later re-validation re-surfaced the classic
``Print sides: "double" is not a valid choice.`` /
``Color mode: "full_color" is not a valid choice.`` errors.

These tests pin the model layer to canonical codes so the friendly-label space
can never leak into ``QuoteItem`` again.
"""

from django.test import TestCase

from accounts.models import User
from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent
from quotes.models import CalculatorDraft, QuoteItem, QuoteRequest
from quotes.services_workflow import (
    _build_manager_intake_quote_item,
    _canonical_color_mode,
    _canonical_sides,
)
from shops.models import Shop


class CanonicalizerTests(TestCase):
    def test_canonical_sides_maps_friendly_labels_to_duplex(self):
        for label in ("double", "double_sided", "both", "2", "two_sided"):
            self.assertEqual(_canonical_sides(label), "DUPLEX", label)

    def test_canonical_sides_maps_friendly_labels_to_simplex(self):
        for label in ("single", "single_sided", "one_sided", "simplex", "1"):
            self.assertEqual(_canonical_sides(label), "SIMPLEX", label)

    def test_canonical_sides_passes_canonical_codes_through(self):
        self.assertEqual(_canonical_sides("SIMPLEX"), "SIMPLEX")
        self.assertEqual(_canonical_sides("DUPLEX"), "DUPLEX")

    def test_canonical_sides_falls_back_on_garbage(self):
        self.assertEqual(_canonical_sides("OCTOPUS"), "SIMPLEX")
        self.assertEqual(_canonical_sides(""), "SIMPLEX")
        self.assertEqual(_canonical_sides(None), "SIMPLEX")
        self.assertEqual(_canonical_sides("tri-fold", default="SIM"), "SIM")

    def test_canonical_color_mode_maps_friendly_labels_to_canonical(self):
        for label in ("full_color", "colour", "cmyk", "4_color"):
            self.assertEqual(_canonical_color_mode(label), "COLOR", label)
        for label in ("black_only", "grayscale", "mono", "b&w"):
            self.assertEqual(_canonical_color_mode(label), "BW", label)

    def test_canonical_color_mode_passes_canonical_codes_through(self):
        self.assertEqual(_canonical_color_mode("COLOR"), "COLOR")
        self.assertEqual(_canonical_color_mode("BW"), "BW")

    def test_canonical_color_mode_accepts_nested_payload_objects(self):
        self.assertEqual(_canonical_color_mode({"mode": "full_color"}), "COLOR")
        self.assertEqual(_canonical_sides({"sides": "double"}), "DUPLEX")

    def test_canonical_color_mode_falls_back_on_garbage(self):
        self.assertEqual(_canonical_color_mode("rgb"), "COLOR")
        self.assertEqual(_canonical_color_mode(""), "COLOR")
        self.assertEqual(_canonical_color_mode(None), "COLOR")


class QuoteItemBuilderNormalizationTests(TestCase):
    """The builder functions must store canonical codes so model choice
    validation always passes (the "surfaced as a valid-choice error" failure)."""

    def setUp(self):
        self.user = User.objects.create_user(email="labels@test.com", password="pass")
        self.shop = Shop.objects.create(
            owner=self.user, name="Label Shop", slug="label-shop", is_active=True
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.user,
            customer_name="John",
            customer_email="john@test.com",
            status="DRAFT",
        )

    def _draft(self, calculator_inputs_snapshot: dict) -> CalculatorDraft:
        return CalculatorDraft.objects.create(
            user=self.user,
            title="Friendly label draft",
            calculator_context=CalculatorDraftContext.MANAGER_DASHBOARD,
            intent=CalculatorDraftIntent.INTERNAL_ESTIMATE,
            calculator_inputs_snapshot=calculator_inputs_snapshot,
            pricing_snapshot={},
            custom_product_snapshot={},
            request_details_snapshot={"notes": ""},
        )

    def test_manager_intake_builder_stores_canonical_codes_for_friendly_labels(self):
        draft = self._draft(
            {
                "quantity": 100,
                "width_mm": 210,
                "height_mm": 297,
                "pricing_mode": "SHEET",
                "print_sides": "double",
                "color_mode": "full_color",
            }
        )
        item = _build_manager_intake_quote_item(
            quote_request=self.quote_request,
            draft=draft,
            merged_request_details={},
        )
        self.assertEqual(item.sides, "DUPLEX")
        self.assertEqual(item.color_mode, "COLOR")
        # Proves the stored values satisfy the model's choice constraints.
        item.full_clean()

    def test_manager_intake_builder_accepts_other_label_key_spellings(self):
        draft = self._draft(
            {
                "quantity": 100,
                "width_mm": 210,
                "height_mm": 297,
                "pricing_mode": "SHEET",
                "sides": "single",
                "colour_mode": "black_only",
            }
        )
        item = _build_manager_intake_quote_item(
            quote_request=self.quote_request,
            draft=draft,
            merged_request_details={},
        )
        self.assertEqual(item.sides, "SIMPLEX")
        self.assertEqual(item.color_mode, "BW")
        item.full_clean()

    def test_manager_intake_builder_falls_back_to_defaults_on_garbage(self):
        draft = self._draft(
            {
                "quantity": 100,
                "width_mm": 210,
                "height_mm": 297,
                "pricing_mode": "SHEET",
                "print_sides": "OCTOPUS",
                "color_mode": "rgb",
            }
        )
        item = _build_manager_intake_quote_item(
            quote_request=self.quote_request,
            draft=draft,
            merged_request_details={},
        )
        self.assertEqual(item.sides, "SIMPLEX")
        self.assertEqual(item.color_mode, "COLOR")
        item.full_clean()

    def test_manager_intake_builder_keeps_canonical_codes_unchanged(self):
        draft = self._draft(
            {
                "quantity": 100,
                "width_mm": 210,
                "height_mm": 297,
                "pricing_mode": "SHEET",
                "print_sides": "DUPLEX",
                "color_mode": "BW",
            }
        )
        item = _build_manager_intake_quote_item(
            quote_request=self.quote_request,
            draft=draft,
            merged_request_details={},
        )
        self.assertEqual(item.sides, "DUPLEX")
        self.assertEqual(item.color_mode, "BW")
        item.full_clean()

    def test_stored_labels_never_trigger_model_choice_validation(self):
        """The exact failure "is not a valid choice" must not recur for the
        friendly label space, wherever it enters the model layer."""
        drafts = [
            self._draft(
                {
                    "quantity": 100,
                    "width_mm": 210,
                    "height_mm": 297,
                    "pricing_mode": "SHEET",
                    "print_sides": print_sides,
                    "color_mode": color_mode,
                }
            )
            for print_sides in ("single", "double")
            for color_mode in ("full_color", "black_only")
        ]
        for draft in drafts:
            item = _build_manager_intake_quote_item(
                quote_request=self.quote_request,
                draft=draft,
                merged_request_details={},
            )
            item.full_clean()
        self.assertEqual(
            QuoteItem.objects.filter(
                quote_request=self.quote_request,
                sides__in=("SIMPLEX", "DUPLEX"),
                color_mode__in=("BW", "COLOR"),
            ).count(),
            len(drafts),
        )