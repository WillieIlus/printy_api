"""Phase 2 — rate card persistence for printers without an owned Shop.

Phase 0 finding: a printer (PRODUCTION role) with no owned Shop could not
load or save the MVP rate card — GET/PATCH /api/shops/rate-card/setup/ and
POST /api/shops/rate-card/onboarding-complete/ returned a bare 404.

Fix: those endpoints now bootstrap a minimal Shop for shop-owner users
(consistent with the existing /api/for-shops/rate-card/save/ auto-provision),
and return an actionable error for roles that cannot own a shop.
"""

from rest_framework.test import APIClient
from django.test import TestCase

from accounts.models import User
from pricing.models import ShopRateCardSetup
from shops.models import Shop


class RateCardBootstrapAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.printer = User.objects.create_user(
            email="bootstrap-printer@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.client_user = User.objects.create_user(
            email="bootstrap-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )

    def _paper_payload(self):
        return {
            "paper_rows": [
                {
                    "key": "300gsm_matte_art_card",
                    "paper_base_price": "35.00",
                    "single_print_base": "15.00",
                    "double_print_base": "30.00",
                    "heavy_paper_surcharge": "10.00",
                    "active": True,
                }
            ],
            "finishing_rows": [{"key": "cutting", "price": "480.00", "active": True}],
            "shop_details": {
                "shop_name": "Bootstrap Print Shop",
                "whatsapp_number": "+254700000000",
                "location_area": "Nairobi",
            },
        }

    def test_printer_without_shop_get_setup_bootstraps_shop_and_returns_builder(self):
        self.client.force_authenticate(user=self.printer)

        response = self.client.get("/api/shops/rate-card/setup/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("paper_rows", data)
        self.assertIn("finishing_rows", data)
        self.assertIn("shop_details", data)
        self.assertFalse(data["completed"])
        shop = Shop.objects.get(owner=self.printer)
        self.assertTrue(shop.is_active)
        self.assertTrue(shop.is_public)
        self.assertEqual(shop.name, "Print Shop")

    def test_printer_without_shop_patch_setup_bootstraps_persists_and_reloads(self):
        self.client.force_authenticate(user=self.printer)

        response = self.client.patch("/api/shops/rate-card/setup/", self._paper_payload(), format="json")

        self.assertEqual(response.status_code, 200)
        shop = Shop.objects.get(owner=self.printer)
        self.assertEqual(shop.name, "Bootstrap Print Shop")
        self.assertEqual(shop.public_whatsapp_number, "+254700000000")
        self.assertEqual(ShopRateCardSetup.objects.filter(shop=shop).count(), 1)
        row = response.json()["paper_rows"][0]
        self.assertEqual(row["key"], "300gsm_matte_art_card")
        self.assertEqual(row["paper_base_price"], "35.00")

        reload = self.client.get("/api/shops/rate-card/setup/")
        self.assertEqual(reload.status_code, 200)
        reload_rows = reload.json()["paper_rows"]
        saved = next(r for r in reload_rows if r["key"] == "300gsm_matte_art_card")
        self.assertTrue(saved["active"])
        self.assertEqual(saved["paper_base_price"], "35.00")
        self.assertEqual(reload.json()["shop_details"]["shop_name"], "Bootstrap Print Shop")

    def test_printer_without_shop_complete_bootstraps_and_marks_complete(self):
        self.client.force_authenticate(user=self.printer)

        response = self.client.post("/api/shops/rate-card/onboarding-complete/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["completed"])
        shop = Shop.objects.get(owner=self.printer)
        self.assertTrue(shop.public_match_ready)
        self.assertTrue(shop.is_active)
        self.assertTrue(shop.is_public)
        # pricing_ready is data-driven: onboarding completes with zero active
        # paper stock / printing rates, so the shop is NOT yet print-ready.
        # This is the "paper stock is a critical printer item" rule.
        self.assertFalse(shop.pricing_ready)

    def test_client_without_shop_setup_returns_actionable_404_without_creating_shop(self):
        self.client.force_authenticate(user=self.client_user)

        get_response = self.client.get("/api/shops/rate-card/setup/")
        self.assertEqual(get_response.status_code, 404)
        self.assertIn("Complete shop setup before setting your rate card", get_response.json()["detail"])

        patch_response = self.client.patch("/api/shops/rate-card/setup/", self._paper_payload(), format="json")
        self.assertEqual(patch_response.status_code, 404)
        self.assertIn("Complete shop setup before setting your rate card", patch_response.json()["detail"])

        complete_response = self.client.post("/api/shops/rate-card/onboarding-complete/")
        self.assertEqual(complete_response.status_code, 404)

        self.assertFalse(Shop.objects.filter(owner=self.client_user).exists())

    def test_printer_with_shop_does_not_bootstrap_on_unrelated_shop_slug(self):
        other = User.objects.create_user(
            email="other-printer@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        my_shop = Shop.objects.create(owner=self.printer, name="My Shop", slug="my-shop", is_active=True)
        other_shop = Shop.objects.create(owner=other, name="Other Shop", slug="other-shop", is_active=True)
        self.client.force_authenticate(user=self.printer)

        response = self.client.get("/api/shops/rate-card/setup/", {"shop_slug": other_shop.slug})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Shop.objects.filter(owner=self.printer).count(), 1)
        self.assertEqual(Shop.objects.get(pk=my_shop.pk).id, my_shop.id)

    def test_printer_with_existing_shop_still_uses_owned_shop(self):
        Shop.objects.create(owner=self.printer, name="Established Shop", slug="established-shop", is_active=True)
        self.client.force_authenticate(user=self.printer)

        response = self.client.get("/api/shops/rate-card/setup/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Shop.objects.filter(owner=self.printer).count(), 1)
        self.assertEqual(response.json()["shop_details"]["shop_name"], "Established Shop")