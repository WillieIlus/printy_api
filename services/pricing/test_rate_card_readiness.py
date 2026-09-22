"""Rate-card readiness contract tests.

Regression guard for the reported bug: "quote readiness is 0% despite filling up
all the other settings" / the readiness tab showing `0 priced items`,
`0 products you can quote`, `0 + 0 sheets + services live` and
`Waiting for 300gsm double price, lamination, cutting...`.

These tests pin the backend contract the shop rate-card UI depends on:

1. Saving an active rate card and reloading it must NEVER report zero readiness.
2. The reloaded summary must always match the active rows it describes.
3. "Quote proof ready" specifically requires three ingredients — an active
   250gsm+ card stock with a double-sided price, an active lamination rate, and
   an active cutting rate. Anything less must be reported truthfully, not as 0%.
"""

from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from shops.models import Shop
from services.pricing.mvp_rate_card import build_shop_rate_card_setup, save_shop_rate_card_setup


ALL_PAPER_KEYS = [
    "350gsm_matte_art_card",
    "300gsm_matte_art_card",
    "250gsm_matte_art_card",
    "art_paper_200gsm",
    "art_paper_170gsm",
    "art_paper_150gsm",
    "art_paper_130gsm",
    "art_paper_115gsm",
    "art_paper_100gsm",
    "tic_tac",
    "ivory_300gsm",
]

CUTTING = "cutting"
LAMINATION = "gloss_lamination_double"
STITCHING = "stitching_booklet"
BINDING_ONLY = ["perfect_binding", "wire_o", "stitching_booklet"]


def _active_paper_setup(shop, *, paper_keys=ALL_PAPER_KEYS, finishing_keys=(CUTTING, LAMINATION, STITCHING)):
    """Mirror the UI flow: load the builder, activate rows, save the draft."""
    setup = build_shop_rate_card_setup(shop)
    paper_by_key = {row["key"]: row for row in setup["paper_rows"]}
    for key in paper_by_key:
        if key in paper_keys:
            paper_by_key[key]["active"] = True
    finishing_by_key = {row["key"]: row for row in setup["finishing_rows"]}
    for key in finishing_by_key:
        finishing_by_key[key]["active"] = key in finishing_keys
    return save_shop_rate_card_setup(
        shop,
        paper_rows=setup["paper_rows"],
        finishing_rows=setup["finishing_rows"],
        shop_details={"shop_name": "Print Shop", "whatsapp_number": "+254 700 000 000", "location_area": "Nairobi"},
        completed=False,
    )


def _make_shop(user):
    return Shop.objects.create(
        owner=user,
        name="Print Shop",
        slug="print-shop-readiness",
        phone_number="+254 700 000 000",
        public_whatsapp_number="+254 700 000 000",
        service_area="Nairobi",
        city="Nairobi",
        country="Kenya",
        is_active=True,
        is_public=True,
    )


class RateCardReadinessSaveReloadTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="readiness-owner@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = _make_shop(self.owner)

    def test_saved_full_rate_card_never_reloads_as_zero_readiness(self):
        """The reported symptom: fully filled settings must not come back as 0."""
        first = _active_paper_setup(self.shop)
        self.assertEqual(first["summary"]["pricing_items_added"], 14)
        self.assertEqual(first["summary"]["paper_rows_added"], 11)
        self.assertEqual(first["summary"]["finishing_rows_added"], 3)
        self.assertGreaterEqual(first["summary"]["products_unlocked"], 1)
        self.assertTrue(first["example_quote"]["is_complete"])

        reloaded = build_shop_rate_card_setup(self.shop)
        self.assertEqual(reloaded["summary"]["pricing_items_added"], 14)
        self.assertEqual(reloaded["summary"]["paper_rows_added"], 11)
        self.assertEqual(reloaded["summary"]["finishing_rows_added"], 3)
        self.assertGreaterEqual(reloaded["summary"]["products_unlocked"], 1)
        self.assertTrue(reloaded["example_quote"]["is_complete"])
        self.assertEqual(reloaded["example_quote"]["status_text"], "Quote proof is ready.")

    def test_reloaded_summary_always_matches_active_rows(self):
        """The readiness numbers must never disagree with the rows they describe."""
        _active_paper_setup(self.shop)
        reloaded = build_shop_rate_card_setup(self.shop)

        active_papers = [row for row in reloaded["paper_rows"] if row.get("active")]
        active_finishings = [row for row in reloaded["finishing_rows"] if row.get("active")]
        expected_items = len(active_papers) + len(active_finishings)

        self.assertEqual(reloaded["summary"]["pricing_items_added"], expected_items)
        self.assertEqual(reloaded["summary"]["paper_rows_added"], len(active_papers))
        self.assertEqual(reloaded["summary"]["finishing_rows_added"], len(active_finishings))

    def test_saving_twice_is_idempotent_and_preserves_readiness(self):
        first = _active_paper_setup(self.shop)
        second = _active_paper_setup(self.shop)
        self.assertEqual(second["summary"]["pricing_items_added"], first["summary"]["pricing_items_added"])
        self.assertEqual(second["example_quote"]["is_complete"], first["example_quote"]["is_complete"])


class RateCardQuoteProofGateTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="readiness-gate-owner@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = _make_shop(self.owner)

    def test_quote_proof_ready_when_card_double_side_lamination_and_cutting_are_active(self):
        _active_paper_setup(self.shop, paper_keys=["300gsm_matte_art_card"], finishing_keys=[CUTTING, LAMINATION])
        reloaded = build_shop_rate_card_setup(self.shop)
        self.assertTrue(reloaded["example_quote"]["is_complete"])
        self.assertEqual(reloaded["example_quote"]["status_text"], "Quote proof is ready.")

    def test_quote_proof_reports_missing_lamination_and_cutting_when_only_bindings_active(self):
        """3 finishing services that are NOT lamination/cutting must not pass the proof."""
        _active_paper_setup(self.shop, paper_keys=["300gsm_matte_art_card"], finishing_keys=BINDING_ONLY)
        reloaded = build_shop_rate_card_setup(self.shop)
        self.assertFalse(reloaded["example_quote"]["is_complete"])
        status = reloaded["example_quote"]["status_text"]
        self.assertIn("lamination", status)
        self.assertIn("cutting", status)

    def test_quote_proof_reports_missing_card_double_price_without_heavy_card_stock(self):
        _active_paper_setup(self.shop, paper_keys=["art_paper_150gsm"], finishing_keys=[CUTTING, LAMINATION])
        reloaded = build_shop_rate_card_setup(self.shop)
        self.assertFalse(reloaded["example_quote"]["is_complete"])
        self.assertIn("300gsm double price", reloaded["example_quote"]["status_text"])

    def test_sticker_stock_alone_cannot_power_the_business_card_quote_proof(self):
        _active_paper_setup(self.shop, paper_keys=["tic_tac"], finishing_keys=[CUTTING, LAMINATION])
        reloaded = build_shop_rate_card_setup(self.shop)
        self.assertFalse(reloaded["example_quote"]["is_complete"])


class RateCardProductCatalogTests(TestCase):
    """Printer-facing 'products you can provide at min 100 pieces' contract.

    Every sheet product in the catalog must carry an explicit available status and,
    when not available, the exact items the printer is missing (paper condition and/or
    finishing rates) so the shop can fix its setup instead of guessing.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            email="readiness-catalog-owner@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = _make_shop(self.owner)
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_full_rate_card_marks_sheet_products_ready_with_samples(self):
        _active_paper_setup(self.shop)
        reloaded = build_shop_rate_card_setup(self.shop)
        catalog = {row["key"]: row for row in reloaded["summary"]["product_catalog"]}

        self.assertEqual(len(catalog), 9)
        for key in ("business-cards", "laminated-business-cards", "flyers", "posters", "brochures", "booklets", "stickers"):
            self.assertTrue(catalog[key]["available"], f"{key} should be ready")
            self.assertEqual(catalog[key]["status"], "ready")
            self.assertEqual(catalog[key]["missing_items"], [])
            self.assertIsNotNone(catalog[key]["sample"]["total_production_cost"])
            self.assertEqual(catalog[key]["min_qty"], 100)

        for key in ("perfect-bound-books", "spiral-bound-reports"):
            self.assertFalse(catalog[key]["available"], f"{key} must not be ready without its binding rate")

    def test_gap_products_report_exact_missing_items(self):
        _active_paper_setup(self.shop, paper_keys=["art_paper_150gsm"], finishing_keys=[])
        reloaded = build_shop_rate_card_setup(self.shop)
        catalog = {row["key"]: row for row in reloaded["summary"]["product_catalog"]}

        self.assertTrue(catalog["flyers"]["available"])
        self.assertEqual(catalog["flyers"]["status"], "ready")

        cards = catalog["business-cards"]
        self.assertFalse(cards["available"])
        joined = " ".join(cards["missing_items"]).lower()
        self.assertIn("heavy card stock", joined)
        self.assertIn("cutting", joined)

        laminated = catalog["laminated-business-cards"]
        self.assertFalse(laminated["available"])
        self.assertIn("lamination", " ".join(laminated["missing_items"]).lower())

        self.assertIsNone(cards["sample"])

    def test_api_setup_returns_product_catalog(self):
        _active_paper_setup(self.shop)
        resp = self.client.get("/api/shops/rate-card/setup/")
        self.assertEqual(resp.status_code, 200)
        catalog = resp.json()["summary"]["product_catalog"]
        self.assertEqual(len(catalog), 9)
        self.assertTrue(any(row["available"] and row["sample"] for row in catalog))


class RateCardReadinessAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.printer = User.objects.create_user(
            email="readiness-api-printer@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.client.force_authenticate(user=self.printer)

    def _activate_all_rows(self, data):
        for row in data["paper_rows"]:
            row["active"] = True
        for row in data["finishing_rows"]:
            row["active"] = row["key"] in {CUTTING, LAMINATION, STITCHING}
        return data

    def test_bootstrap_patch_summary_matches_rows_and_reload_stays_nonzero(self):
        get_resp = self.client.get("/api/shops/rate-card/setup/")
        self.assertEqual(get_resp.status_code, 200)
        data = self._activate_all_rows(get_resp.json())

        patch_resp = self.client.patch(
            "/api/shops/rate-card/setup/",
            {
                "paper_rows": data["paper_rows"],
                "finishing_rows": data["finishing_rows"],
                "shop_details": {
                    "shop_name": "Print Shop",
                    "whatsapp_number": "+254 700 000 000",
                    "location_area": "Nairobi",
                },
            },
            format="json",
        )
        self.assertEqual(patch_resp.status_code, 200)
        patched = patch_resp.json()
        self.assertGreaterEqual(patched["summary"]["pricing_items_added"], 14)
        self.assertTrue(patched["example_quote"]["is_complete"])

        reload_resp = self.client.get("/api/shops/rate-card/setup/")
        self.assertEqual(reload_resp.status_code, 200)
        reloaded = reload_resp.json()
        self.assertGreaterEqual(reloaded["summary"]["pricing_items_added"], 14)
        self.assertEqual(reloaded["summary"]["pricing_items_added"], patched["summary"]["pricing_items_added"])
        self.assertTrue(reloaded["example_quote"]["is_complete"])