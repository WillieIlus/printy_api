from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.models import User
from pricing.models import ShopRateCardSetup
from shops.models import Shop


class SeedPaperCatalogCommandTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="seed-paper-owner@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Seed Paper Shop",
            slug="seed-paper-shop",
        )

    def test_seeds_requested_paper_reference_prices_into_shop_setup(self):
        out = StringIO()

        call_command("seed_paper_catalog", "--shop", self.shop.slug, stdout=out)

        setup = ShopRateCardSetup.objects.get(shop=self.shop)
        rows = {row["key"]: row for row in setup.paper_rows}
        self.assertEqual(rows["350gsm_matte_art_card"]["double_side_price"], "75.00")
        self.assertEqual(rows["350gsm_matte_art_card"]["single_side_price"], "45.00")
        self.assertEqual(rows["art_paper_100gsm"]["double_side_price"], "35.00")
        self.assertEqual(rows["art_paper_100gsm"]["single_side_price"], "15.00")
        self.assertEqual(rows["tic_tac"]["single_side_price"], "35.00")
        self.assertIsNone(rows["tic_tac"]["double_side_price"])
        self.assertEqual(rows["ivory_300gsm"]["double_side_price"], "80.00")
        self.assertEqual(rows["ivory_300gsm"]["single_side_price"], "45.00")
        self.assertFalse(rows["ivory_300gsm"]["active"])
        self.assertIn("11 added", out.getvalue())

    def test_seed_is_idempotent_and_preserves_existing_shop_prices(self):
        call_command("seed_paper_catalog", "--shop", self.shop.slug, stdout=StringIO())
        setup = ShopRateCardSetup.objects.get(shop=self.shop)
        rows = setup.paper_rows
        rows[0]["single_print_base"] = "99.00"
        rows[0]["active"] = True
        setup.paper_rows = rows
        setup.save(update_fields=["paper_rows", "updated_at"])

        out = StringIO()
        call_command("seed_paper_catalog", "--shop", self.shop.slug, stdout=out)

        setup.refresh_from_db()
        first_row = setup.paper_rows[0]
        self.assertEqual(first_row["single_print_base"], "99.00")
        self.assertEqual(first_row["single_side_price"], "99.00")
        self.assertTrue(first_row["active"])
        self.assertIn("11 preserved", out.getvalue())

    def test_overwrite_refreshes_reference_rows(self):
        call_command("seed_paper_catalog", "--shop", self.shop.slug, stdout=StringIO())
        setup = ShopRateCardSetup.objects.get(shop=self.shop)
        rows = setup.paper_rows
        rows[0]["single_print_base"] = "99.00"
        setup.paper_rows = rows
        setup.save(update_fields=["paper_rows", "updated_at"])

        call_command("seed_paper_catalog", "--shop", self.shop.slug, "--overwrite", stdout=StringIO())

        setup.refresh_from_db()
        self.assertEqual(setup.paper_rows[0]["single_print_base"], "45.00")
        self.assertEqual(setup.paper_rows[0]["single_side_price"], "45.00")


