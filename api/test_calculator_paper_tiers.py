"""Client calculator paper: tiers only (Premium / Standard / Budget).

Clients do not know paper names. The calculator therefore surfaces a single
"Paper quality" step whose options are exactly Premium / Standard / Budget
(per product), and drops the technical `requested_paper_category` /
`requested_gsm` fields from the client field definitions. Grammage and paper
category still travel with the spec (the tier id encodes the grammage), so the
pricing engine and manager recommendation keep working unchanged.
"""

from django.test import TestCase

from services.pricing.calculator_config import get_calculator_config
from services.pricing.calculator_preview import _parse_tier_gsm


class CalculatorPaperTierConfigTestCase(TestCase):
    def _product(self, key):
        config = get_calculator_config()
        product = next(item for item in config["products"] if item["key"] == key)
        return product

    def test_client_fields_are_tier_based_not_technical(self):
        for key in ["business_card", "flyer", "label_sticker", "letterhead"]:
            product = self._product(key)
            field_keys = [field["key"] for field in product["fields"]]
            self.assertIn("paper_stock", field_keys, f"{key} must expose the paper tier step")
            self.assertNotIn("requested_paper_category", field_keys, f"{key} must not show paper categories")
            self.assertNotIn("requested_gsm", field_keys, f"{key} must not show raw grammage")

    def test_paper_quality_labels_are_only_premium_standard_budget(self):
        for key in ["business_card", "flyer", "label_sticker", "letterhead"]:
            product = self._product(key)
            labels = [option["label"] for option in product["paper_options"]]
            self.assertEqual(
                sorted(labels),
                ["Budget", "Premium", "Standard"],
                f"{key} paper tiers must be exactly Budget/Standard/Premium",
            )

    def test_tier_ids_encode_grammage_for_the_engine(self):
        product = self._product("business_card")
        tiers = {option["label"]: option for option in product["paper_options"]}
        self.assertEqual(_parse_tier_gsm(tiers["Standard"]["id"]), 300)
        self.assertEqual(_parse_tier_gsm(tiers["Premium"]["id"]), 350)

    def test_generic_fallback_covers_future_products(self):
        from services.pricing.calculator_config import GENERIC_PAPER_TIER_DEFINITIONS

        self.assertEqual(sorted(tier["label"] for tier in GENERIC_PAPER_TIER_DEFINITIONS), ["Budget", "Premium", "Standard"])

    def test_booklet_and_large_format_are_unchanged(self):
        booklet = self._product("booklet")
        self.assertNotIn("paper_stock", [field["key"] for field in booklet["fields"]])
        self.assertIn("requested_cover_gsm", [field["key"] for field in booklet["fields"]])
        large = self._product("large_format")
        self.assertIn("material_type", [field["key"] for field in large["fields"]])