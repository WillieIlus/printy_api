import io
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import PrintingRate, ShopRateCardSetup
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from services.production_matching import build_partner_production_matches
from shops.models import Shop


class RateCardConfirmationBackfillTests(TestCase):
    """Law 11.7: an unconfirmed rate card stays hidden from find-printer.

    Bug 3: setups that predate migration 0003 default to is_confirmed=False
    with no backfill, so printers with complete, working pricing never showed
    up. The fix confirms only setups whose live pricing is usable; genuinely
    incomplete cards stay hidden.
    """

    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user(
            email="backfill-manager@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
            name="Backfill Manager",
        )
        self.end_client = User.objects.create_user(
            email="backfill-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            name="Backfill Client",
        )

    def _shop(self, name, slug, *, with_pricing=True):
        owner = User.objects.create_user(
            email=f"{slug}@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        shop = Shop.objects.create(
            owner=owner,
            name=name,
            slug=slug,
            is_active=True,
            is_public=True,
            pricing_ready=with_pricing,
            city="Nairobi",
            service_area="Westlands",
        )
        if with_pricing:
            machine = Machine.objects.create(
                shop=shop,
                name=f"{name} Press",
                max_width_mm=320,
                max_height_mm=450,
                is_active=True,
            )
            Paper.objects.create(
                shop=shop,
                name="300gsm Art Card",
                sheet_size="SRA3",
                gsm=300,
                paper_type="GLOSS",
                buying_price=Decimal("10.00"),
                selling_price=Decimal("20.00"),
                width_mm=320,
                height_mm=450,
                is_active=True,
            )
            PrintingRate.objects.create(
                machine=machine,
                sheet_size="SRA3",
                color_mode=ColorMode.COLOR,
                single_price=Decimal("35.00"),
                double_price=Decimal("70.00"),
                is_active=True,
            )
        return shop

    def _match_names(self):
        payload = build_partner_production_matches(
            {
                "quantity": 100,
                "color_mode": "COLOR",
                "print_sides": "SIMPLEX",
                "product_type": "business_card",
                "finished_size": "85x55mm",
                "requested_paper_category": "gloss",
                "requested_gsm": 300,
            }
        )
        return {row["shop_name"] for row in payload["results"]}

    def test_complete_unconfirmed_setup_hidden_until_backfill(self):
        shop = self._shop("Complete Shop", "backfill-complete")
        ShopRateCardSetup.objects.create(shop=shop, is_confirmed=False, completed=False)

        self.assertNotIn(shop.name, self._match_names())

        call_command("backfill_rate_card_confirmation", "--apply", stdout=io.StringIO())

        self.assertTrue(ShopRateCardSetup.objects.get(shop=shop).is_confirmed)
        self.assertIn(shop.name, self._match_names())

    def test_incomplete_unconfirmed_setup_stays_hidden(self):
        shop = self._shop("Incomplete Shop", "backfill-incomplete", with_pricing=False)
        ShopRateCardSetup.objects.create(shop=shop, is_confirmed=False, completed=False)

        out = io.StringIO()
        call_command("backfill_rate_card_confirmation", "--apply", stdout=out)

        self.assertFalse(ShopRateCardSetup.objects.get(shop=shop).is_confirmed)
        self.assertNotIn(shop.name, self._match_names())
        self.assertIn("incomplete", out.getvalue().lower())

    def test_backfill_defaults_to_dry_run(self):
        shop = self._shop("Dry Shop", "backfill-dry")
        ShopRateCardSetup.objects.create(shop=shop, is_confirmed=False)

        out = io.StringIO()
        call_command("backfill_rate_card_confirmation", stdout=out)

        self.assertIn("DRY RUN", out.getvalue())
        self.assertFalse(ShopRateCardSetup.objects.get(shop=shop).is_confirmed)

    def test_backfill_is_idempotent_and_never_downgrades(self):
        complete = self._shop("Complete Shop", "backfill-complete-idem")
        ShopRateCardSetup.objects.create(shop=complete, is_confirmed=False)

        call_command("backfill_rate_card_confirmation", "--apply", stdout=io.StringIO())

        out = io.StringIO()
        call_command("backfill_rate_card_confirmation", "--apply", stdout=out)
        self.assertIn("0 eligible to confirm", out.getvalue())
        self.assertTrue(ShopRateCardSetup.objects.get(shop=complete).is_confirmed)

    def test_shop_with_no_setup_row_is_unchanged(self):
        shop = self._shop("No Setup Shop", "backfill-no-setup")

        call_command("backfill_rate_card_confirmation", "--apply", stdout=io.StringIO())

        self.assertFalse(ShopRateCardSetup.objects.filter(shop=shop).exists())
        self.assertIn(shop.name, self._match_names())

    def test_seed_paper_catalog_never_downgrades_confirmed(self):
        shop = self._shop("Seeded Shop", "backfill-seeded")
        ShopRateCardSetup.objects.create(shop=shop, is_confirmed=True, completed=True)

        call_command("seed_paper_catalog", "--shop", shop.slug, stdout=io.StringIO())

        self.assertTrue(ShopRateCardSetup.objects.get(shop=shop).is_confirmed)

    def test_seed_paper_catalog_confirms_only_when_usable(self):
        usable = self._shop("Usable Seed Shop", "backfill-seed-usable")
        incomplete = self._shop("Bare Seed Shop", "backfill-seed-bare", with_pricing=False)

        call_command("seed_paper_catalog", "--shop", usable.slug, stdout=io.StringIO())
        call_command("seed_paper_catalog", "--shop", incomplete.slug, stdout=io.StringIO())

        self.assertTrue(ShopRateCardSetup.objects.get(shop=usable).is_confirmed)
        self.assertFalse(ShopRateCardSetup.objects.get(shop=incomplete).is_confirmed)

    def test_manager_shop_options_include_backfilled_shop(self):
        shop = self._shop("Backfilled Shop", "backfill-endpoint")
        ShopRateCardSetup.objects.create(shop=shop, is_confirmed=False)
        quote_request = QuoteRequest.objects.create(
            shop=None,
            created_by=self.end_client,
            assigned_manager=self.manager,
            customer_name="Backfill Client",
            customer_email=self.end_client.email,
            status=QuoteStatus.SUBMITTED,
            request_snapshot={
                "source": "manager_led_intake",
                "calculator_inputs": {
                    "product_type": "business_card",
                    "quantity": 100,
                    "finished_size": "85x55mm",
                    "requested_paper_category": "gloss",
                    "requested_gsm": 300,
                    "print_sides": "SIMPLEX",
                    "color_mode": "COLOR",
                },
            },
        )

        self.client.force_authenticate(user=self.manager)
        before = self.client.post(
            f"/api/dashboard/partner/quotes/{quote_request.id}/shop-options/",
            {},
            format="json",
        )
        self.assertNotIn(shop.name, {row["shop_name"] for row in before.json()["results"]})

        call_command("backfill_rate_card_confirmation", "--apply", stdout=io.StringIO())

        after = self.client.post(
            f"/api/dashboard/partner/quotes/{quote_request.id}/shop-options/",
            {},
            format="json",
        )
        self.assertEqual(after.status_code, 200, after.json())
        self.assertIn(shop.name, {row["shop_name"] for row in after.json()["results"]})
