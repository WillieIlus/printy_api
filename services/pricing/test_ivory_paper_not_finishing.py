"""Ivory belongs to paper, not finishing."""

from django.test import SimpleTestCase

from services.pricing.mvp_rate_card import (
    DEFAULT_FINISHING_DEFINITIONS,
    DEFAULT_PAPER_DEFINITIONS,
)


class IvoryPaperNotFinishingTests(SimpleTestCase):
    def test_ivory_is_not_a_finishing_definition(self):
        for row in DEFAULT_FINISHING_DEFINITIONS:
            haystack = " ".join(
                str(row.get(field) or "")
                for field in ("key", "id", "label", "name")
            ).lower()
            self.assertNotIn(
                "ivory",
                haystack,
                msg=f"Ivory must not appear as finishing: {row}",
            )

    def test_ivory_300gsm_is_a_paper_definition(self):
        row = next(
            item for item in DEFAULT_PAPER_DEFINITIONS if item["key"] == "ivory_300gsm"
        )
        self.assertEqual(row["gsm"], 300)
        self.assertEqual(row["category"], "Ivory")
        self.assertEqual(row["label"], "Ivory 300gsm")
        self.assertTrue(row["supports_double_side"])
