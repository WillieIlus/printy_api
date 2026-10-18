from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Paper
from pricing.models import FinishingRate, PrintingRate, ShopRateCardSetup, WastePolicy
from shops.models import Shop


class RateCardTableEditorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="table-editor@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.other_owner = User.objects.create_user(
            email="other-table-editor@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.buyer = User.objects.create_user(
            email="table-buyer@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Table Editor Shop",
            slug="table-editor-shop",
            phone_number="+254700000001",
            public_whatsapp_number="+254700000001",
            service_area="Nairobi",
            city="Nairobi",
            country="Kenya",
            is_active=True,
            is_public=True,
        )
        self.other_shop = Shop.objects.create(
            owner=self.other_owner,
            name="Other Table Shop",
            slug="other-table-shop",
            is_active=True,
        )
        WastePolicy.objects.all().delete()
        WastePolicy.objects.create(
            fixed_waste_sheets=2,
            variable_waste_rate=Decimal("0.1000"),
            minimum_billable_sheets=3,
            maximum_spoilage_rate=Decimal("0.5000"),
        )
        self.client.force_authenticate(user=self.owner)

    def _paper_row(self, **overrides):
        row = {
            "key": "300gsm_matte_art_card",
            "paper_base_price": "25.00",
            "single_print_base": "15.00",
            "double_print_base": "30.00",
            "heavy_paper_surcharge": "10.00",
            "surcharge_threshold_gsm": 250,
            "active": True,
        }
        row.update(overrides)
        return row

    def _finishing_rows(self):
        return [
            {"key": "cutting", "price": "50.00", "minimum_charge": "50.00", "active": True},
            {"key": "matte_lamination_double", "price": "45.00", "minimum_charge": "50.00", "active": True},
        ]

    def _payload(self, **row_overrides):
        return {
            "paper_rows": [self._paper_row(**row_overrides)],
            "finishing_rows": self._finishing_rows(),
            "shop_details": {
                "shop_name": "Table Editor Shop",
                "whatsapp_number": "+254700000001",
                "location_area": "Nairobi",
            },
        }

    def _setup(self):
        response = self.client.get("/api/shops/rate-card/setup/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def _row_preview(self, payload, key="300gsm_matte_art_card"):
        return next(row for row in payload["rows"] if row["key"] == key)

    def test_setup_returns_canonical_table_preview_for_default_product(self):
        response = self.client.patch("/api/shops/rate-card/setup/", self._payload(), format="json")
        self.assertEqual(response.status_code, 200, response.content)

        preview = response.json()["table_preview"]
        self.assertEqual(preview["currency"], "KES")
        self.assertEqual(preview["quantity"], 100)
        self.assertEqual(preview["product"]["key"], "business-cards")
        self.assertIn("cutting", preview["finishing_keys"])
        self.assertEqual(preview["good_sheets"], 5)
        self.assertEqual(preview["billable_sheets"], 8)
        self.assertEqual(preview["waste_sheets_added"], 3)

        row = self._row_preview(preview)
        self.assertTrue(row["active"])
        self.assertTrue(row["surcharge_applies"])
        self.assertEqual(row["single"]["paper"], "200.00")
        self.assertEqual(row["single"]["printing"], "120.00")
        self.assertEqual(row["single"]["surcharge"], "80.00")
        self.assertIn("finishing", row["single"])
        self.assertEqual(row["single"]["sheets"], 8)
        self.assertEqual(row["single"]["per_sheet"], "50.00")
        self.assertEqual(row["double"]["per_sheet"], "65.00")

    def test_preview_endpoint_recalculates_for_selected_quantity_and_product(self):
        response = self.client.patch("/api/shops/rate-card/setup/", self._payload(), format="json")
        self.assertEqual(response.status_code, 200, response.content)

        preview_response = self.client.post(
            "/api/shops/rate-card/preview/",
            {
                "paper_rows": self._payload()["paper_rows"],
                "finishing_rows": self._finishing_rows(),
                "quantity": 500,
                "product_key": "flyers",
                "finishing_keys": ["matte_lamination_double"],
            },
            format="json",
        )
        self.assertEqual(preview_response.status_code, 200, preview_response.content)
        preview = preview_response.json()
        self.assertEqual(preview["quantity"], 500)
        self.assertEqual(preview["product"]["key"], "flyers")
        self.assertEqual(preview["finishing_keys"], ["matte_lamination_double"])
        self.assertGreater(preview["good_sheets"], 0)
        self.assertGreaterEqual(preview["billable_sheets"], preview["good_sheets"])
        row = self._row_preview(preview)
        self.assertEqual(row["single"]["paper"], str(Decimal("25.00") * preview["billable_sheets"]))
        self.assertEqual(row["single"]["printing"], str(Decimal("15.00") * preview["billable_sheets"]))
        self.assertEqual(row["double"]["printing"], str(Decimal("30.00") * preview["billable_sheets"]))
        self.assertNotEqual(row["single"]["finishing"], "0.00")

    def test_preview_is_read_only(self):
        before = (
            Paper.objects.filter(shop=self.shop).count(),
            PrintingRate.objects.filter(machine__shop=self.shop).count(),
            FinishingRate.objects.filter(shop=self.shop).count(),
            ShopRateCardSetup.objects.filter(shop=self.shop).count(),
        )
        response = self.client.post(
            "/api/shops/rate-card/preview/",
            {
                "paper_rows": self._payload()["paper_rows"],
                "finishing_rows": self._finishing_rows(),
                "quantity": 250,
                "product_key": "business-cards",
                "finishing_keys": ["cutting"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        after = (
            Paper.objects.filter(shop=self.shop).count(),
            PrintingRate.objects.filter(machine__shop=self.shop).count(),
            FinishingRate.objects.filter(shop=self.shop).count(),
            ShopRateCardSetup.objects.filter(shop=self.shop).count(),
        )
        self.assertEqual(before, after)

    def test_negative_and_malformed_prices_are_rejected_without_partial_writes(self):
        negative = self._payload(paper_base_price="-1.00")
        response = self.client.patch("/api/shops/rate-card/setup/", negative, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(ShopRateCardSetup.objects.filter(shop=self.shop).count(), 0)

        malformed = self._payload(paper_base_price="not-a-price")
        response = self.client.patch("/api/shops/rate-card/setup/", malformed, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(ShopRateCardSetup.objects.filter(shop=self.shop).count(), 0)
        self.assertFalse(Paper.objects.filter(shop=self.shop, is_active=True).exists())

    def test_blank_double_print_price_defaults_to_twice_one_side(self):
        response = self.client.patch(
            "/api/shops/rate-card/setup/",
            self._payload(single_print_base="20.00", double_print_base=None, heavy_paper_surcharge="0.00"),
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        row = next(
            item
            for item in response.json()["paper_rows"]
            if item["key"] == "300gsm_matte_art_card"
        )
        self.assertEqual(row["single_print_base"], "20.00")
        self.assertEqual(row["double_print_base"], "40.00")
        preview_row = self._row_preview(response.json()["table_preview"])
        self.assertEqual(preview_row["double"]["per_sheet"], "65.00")

    def test_stale_version_cannot_overwrite_newer_saved_prices(self):
        current = self._setup()
        first = self.client.patch(
            "/api/shops/rate-card/setup/",
            {**self._payload(paper_base_price="26.00"), "expected_version": current["version"]},
            format="json",
        )
        self.assertEqual(first.status_code, 200, first.content)
        version_after_first = first.json()["version"]
        self.assertNotEqual(version_after_first, current["version"])

        second = self.client.patch(
            "/api/shops/rate-card/setup/",
            {**self._payload(paper_base_price="27.00"), "expected_version": version_after_first},
            format="json",
        )
        self.assertEqual(second.status_code, 200, second.content)

        stale = self.client.patch(
            "/api/shops/rate-card/setup/",
            {**self._payload(paper_base_price="99.00"), "expected_version": version_after_first},
            format="json",
        )
        self.assertEqual(stale.status_code, 409, stale.content)
        self.assertEqual(stale.json()["code"], "rate_card_version_conflict")

        saved = ShopRateCardSetup.objects.get(shop=self.shop)
        saved_row = next(row for row in saved.paper_rows if row["key"] == "300gsm_matte_art_card")
        self.assertEqual(saved_row["paper_base_price"], "27.00")

    def test_other_shop_owner_and_buyer_cannot_use_the_table_endpoints(self):
        self.client.force_authenticate(user=self.other_owner)
        denied = self.client.get("/api/shops/rate-card/setup/", {"shop_slug": self.shop.slug})
        self.assertEqual(denied.status_code, 404)

        denied_preview = self.client.post(
            "/api/shops/rate-card/preview/",
            {**self._payload(), "shop_slug": self.shop.slug},
            format="json",
        )
        self.assertEqual(denied_preview.status_code, 404)

        self.client.force_authenticate(user=self.buyer)
        buyer_save = self.client.patch("/api/shops/rate-card/setup/", self._payload(), format="json")
        self.assertEqual(buyer_save.status_code, 404)
        buyer_preview = self.client.post(
            "/api/shops/rate-card/preview/",
            self._payload(),
            format="json",
        )
        self.assertEqual(buyer_preview.status_code, 404)

    def test_supplied_workbook_defaults_are_used(self):
        setup = self._setup()
        paper = {row["key"]: row for row in setup["paper_rows"]}
        self.assertEqual(paper["300gsm_matte_art_card"]["paper_base_price"], "25.00")
        self.assertEqual(paper["300gsm_matte_art_card"]["single_side_price"], "50.00")
        self.assertEqual(paper["300gsm_matte_art_card"]["double_side_price"], "65.00")
        self.assertEqual(paper["350gsm_matte_art_card"]["single_side_price"], "55.00")
        self.assertEqual(paper["tic_tac"]["single_side_price"], "40.00")
        finishing = {row["key"]: row for row in setup["finishing_rows"]}
        self.assertEqual(finishing["cutting"]["price"], "50.00")
        self.assertEqual(finishing["matte_lamination_double"]["price"], "45.00")
        self.assertEqual(finishing["matte_lamination_double"]["minimum_charge"], "50.00")

    def test_table_preview_does_not_expose_buyer_price_or_margin(self):
        response = self.client.patch("/api/shops/rate-card/setup/", self._payload(), format="json")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertNotIn("client_price", payload["example_quote"])
        self.assertNotIn("pricing_breakdown", payload["example_quote"])
        preview_text = str(payload["table_preview"])
        self.assertNotIn("client_price", preview_text)
        self.assertNotIn("gross_margin", preview_text)
        self.assertNotIn("printy_fee", preview_text)
