"""Default paper stock price = SRA3 sheet price.

Before this change a shop activating the default rate card materialised every
`Paper` row with `selling_price = 0.00` (the default definitions hard-code
`paper_base_price: "0.00"`). Every pricing/matching path filters on
`selling_price__gt=0`, so an unpriced shop silently blocked the whole
calculator -> manager -> quote -> payment flow.

The fix: when a paper row has no explicit base price, the persisted selling
price falls back to the SRA3 sheet default (24.00, matching the seeded
"SRA3 300g Art Card Gloss"). This test pins that behaviour and proves the
production matcher can now see the shop's paper.
"""

from copy import deepcopy
from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from inventory.choices import PaperCategory
from inventory.models import Paper
from pricing.choices import ColorMode
from pricing.models import PrintingRate
from services.pricing.mvp_rate_card import (
    DEFAULT_PAPER_STOCK_PRICE,
    _activated_default_paper_rows,
    _persist_paper_rows,
)
from services.production_matching import _candidate_papers
from shops.models import Shop


class DefaultPaperStockPriceTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="default-paper-owner@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Default Paper Shop",
            slug="default-paper-shop",
            is_active=True,
            is_public=True,
        )

    def test_activated_default_rows_are_priced_at_sra3_default(self):
        _persist_paper_rows(self.shop, _activated_default_paper_rows())

        paper = Paper.objects.filter(shop=self.shop, gsm=300, category=PaperCategory.ARTCARD).get()
        self.assertEqual(paper.sheet_size, "SRA3")
        self.assertTrue(paper.is_active)
        self.assertEqual(paper.buying_price, Decimal("0.00"))
        self.assertEqual(paper.selling_price, DEFAULT_PAPER_STOCK_PRICE)
        self.assertGreater(paper.selling_price, 0)

        sra3_color = PrintingRate.objects.filter(
            machine__shop=self.shop,
            sheet_size="SRA3",
            color_mode=ColorMode.COLOR,
        ).first()
        self.assertIsNotNone(sra3_color)
        self.assertEqual(sra3_color.single_price, Decimal("45.00"))

    def test_explicit_base_price_is_never_overridden(self):
        row = deepcopy(next(r for r in _activated_default_paper_rows() if r["key"] == "300gsm_matte_art_card"))
        row["paper_base_price"] = "35.00"
        _persist_paper_rows(self.shop, [row])

        paper = Paper.objects.get(shop=self.shop, gsm=300)
        self.assertEqual(paper.selling_price, Decimal("35.00"))

    def test_sra3_default_price_unblocks_production_matching(self):
        _persist_paper_rows(self.shop, _activated_default_paper_rows())

        candidates = _candidate_papers(self.shop, {"requested_gsm": 300})

        self.assertTrue(any(p.gsm == 300 and p.selling_price > 0 for p in candidates))