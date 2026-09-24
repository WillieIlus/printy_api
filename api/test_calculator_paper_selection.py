"""Client calculator paper: category + GSM requests (no paper stock field).

Flat products expose two optional fields — `requested_paper_category` (a select
filtered to the product's allowed categories) and `requested_gsm` (a number).
The `paper_stock` field and the stock/tier payloads (`paper_stocks`,
`paper_options`, ...) are gone from the config; the backend still tolerates a
legacy `paper_stock` key at pricing time so old drafts keep working.
"""

from django.test import TestCase

from services.pricing.calculator_config import get_calculator_config
from services.pricing.calculator_preview import _parse_tier_gsm

FLAT_PRODUCTS = ["business_card", "flyer", "label_sticker", "letterhead"]


class CalculatorPaperSelectionConfigTestCase(TestCase):
    def _product(self, key):
        config = get_calculator_config()
        product = next(item for item in config["products"] if item["key"] == key)
        return product

    def test_flat_products_expose_category_and_gsm_but_no_stock_field(self):
        for key in FLAT_PRODUCTS:
            product = self._product(key)
            field_keys = [field["key"] for field in product["fields"]]
            self.assertIn("requested_paper_category", field_keys, f"{key} must expose the paper category step")
            self.assertIn("requested_gsm", field_keys, f"{key} must expose the GSM step")
            self.assertNotIn("paper_stock", field_keys, f"{key} must not expose the paper stock field")

    def test_flat_category_options_are_per_product_filters(self):
        for key in FLAT_PRODUCTS:
            product = self._product(key)
            allowed = set(product["allowed_paper_categories"])
            category_field = next(field for field in product["fields"] if field["key"] == "requested_paper_category")
            option_values = {option["value"] for option in category_field["options"]}
            self.assertTrue(option_values, f"{key} must advertise at least one paper category")
            self.assertLessEqual(option_values, allowed, f"{key} category options must be within allowed categories")

    def test_config_no_longer_carries_stock_or_tier_payloads(self):
        config = get_calculator_config()
        self.assertNotIn("paper_stocks", config)
        for product in config["products"]:
            self.assertNotIn("paper_options", product, product["key"])
            self.assertNotIn("cover_paper_options", product, product["key"])
            self.assertNotIn("insert_paper_options", product, product["key"])

    def test_booklet_and_large_format_are_unchanged(self):
        booklet = self._product("booklet")
        self.assertNotIn("paper_stock", [field["key"] for field in booklet["fields"]])
        self.assertIn("requested_cover_paper_category", [field["key"] for field in booklet["fields"]])
        self.assertIn("requested_cover_gsm", [field["key"] for field in booklet["fields"]])
        large = self._product("large_format")
        self.assertIn("material_type", [field["key"] for field in large["fields"]])

    def test_legacy_tier_gsm_parser_still_normalizes_tier_ids(self):
        self.assertEqual(_parse_tier_gsm("300gsm"), 300)
        self.assertEqual(_parse_tier_gsm("130gsm"), 130)
        self.assertIsNone(_parse_tier_gsm("Premium"))
        self.assertIsNone(_parse_tier_gsm(None))