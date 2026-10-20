from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from inventory.choices import MachineType, PaperCategory, PaperType, SheetSize
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import PrintingRate, ShopRateCardSetup
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from shops.models import Shop


User = get_user_model()


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ManagerProductionShopDirectoryTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user(
            email="phase4-manager@example.com",
            password="pass",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
            name="Phase 4 Manager",
        )
        self.client_user = User.objects.create_user(
            email="phase4-client@example.com",
            password="pass",
            role=User.Role.CLIENT,
            name="Phase 4 Client",
        )

    def _shop(self, name: str, slug: str, *, active=True, public=True, priced=True, city="Nairobi") -> Shop:
        owner = User.objects.create_user(
            email=f"{slug}@example.com",
            password="pass",
            role=User.Role.PRODUCTION,
            name=f"{name} Owner",
        )
        shop = Shop.objects.create(
            owner=owner,
            name=name,
            slug=slug,
            is_active=active,
            is_public=public,
            supports_custom_requests=True,
            supports_catalog_requests=True,
            description=f"{name} production shop",
            business_email=f"{slug}@shop.example.com",
            address_line="Industrial Area",
            city=city,
            state=city,
            country="Kenya",
            service_area=city,
            turnaround_statement="2 business days",
            pricing_ready=priced,
        )
        if priced:
            machine = Machine.objects.create(
                shop=shop,
                name=f"{name} Digital Press",
                machine_type=MachineType.DIGITAL,
                max_width_mm=450,
                max_height_mm=640,
                min_gsm=80,
                max_gsm=350,
                is_active=True,
            )
            Paper.objects.create(
                shop=shop,
                name="Art Card 300gsm",
                sheet_size=SheetSize.SRA3,
                gsm=300,
                paper_type=PaperType.COATED,
                category=PaperCategory.ARTCARD,
                buying_price=Decimal("20.00"),
                selling_price=Decimal("30.00"),
                is_default=True,
                is_active=True,
            )
            PrintingRate.objects.create(
                machine=machine,
                sheet_size=SheetSize.SRA3,
                color_mode=ColorMode.COLOR,
                single_price=Decimal("45.00"),
                is_default=True,
                is_active=True,
            )
        return shop

    def test_manager_directory_lists_eligible_shops_without_prior_jobs(self):
        eligible_a = self._shop("Alpha Print Works", "phase4-alpha")
        eligible_b = self._shop("Beta Production House", "phase4-beta", city="Mombasa")
        unpriced = self._shop("Unpriced Setup Shop", "phase4-unpriced", priced=False)
        inactive = self._shop("Inactive Print Shop", "phase4-inactive", active=False)
        self.assertFalse(QuoteRequest.objects.filter(managed_jobs__broker=self.manager).exists())

        self.client.force_authenticate(user=self.manager)
        response = self.client.get("/api/dashboard/partner/production-shops/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = {row["name"] for row in payload["results"]}
        self.assertEqual({eligible_a.name, eligible_b.name}, names)
        self.assertNotIn(unpriced.name, names)
        self.assertNotIn(inactive.name, names)
        for row in payload["results"]:
            self.assertTrue(row["can_price_requests"])
            self.assertTrue(row["can_receive_requests"])
            self.assertNotIn("printy_fee", row)
            self.assertNotIn("manager_markup", row)
            self.assertNotIn("manager_payout", row)

    def test_manager_directory_keeps_location_filter(self):
        self._shop("Nairobi Print Works", "phase4-nairobi", city="Nairobi")
        self._shop("Mombasa Production House", "phase4-mombasa", city="Mombasa")

        self.client.force_authenticate(user=self.manager)
        response = self.client.get("/api/dashboard/partner/production-shops/?location=mombasa")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["name"] for row in response.json()["results"]], ["Mombasa Production House"])

    def _quote_request(self) -> QuoteRequest:
        return QuoteRequest.objects.create(
            shop=None,
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="Phase 4 Client",
            customer_email=self.client_user.email,
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

    def _directory_names(self) -> set[str]:
        self.client.force_authenticate(user=self.manager)
        response = self.client.get("/api/dashboard/partner/production-shops/")
        self.assertEqual(response.status_code, 200)
        return {row["name"] for row in response.json()["results"]}

    def _shop_option_rows(self, quote_request: QuoteRequest) -> list[dict]:
        self.client.force_authenticate(user=self.manager)
        response = self.client.post(
            f"/api/dashboard/partner/quotes/{quote_request.id}/shop-options/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        return list(response.json()["results"])

    def _shop_option_ids(self, quote_request: QuoteRequest) -> set[int]:
        return {row["shop_id"] for row in self._shop_option_rows(quote_request)}

    def _priced_shop_option_ids(self, quote_request: QuoteRequest) -> set[int]:
        return {
            row["shop_id"]
            for row in self._shop_option_rows(quote_request)
            if row.get("price_available")
        }

    def test_directory_hides_unconfirmed_rate_card_and_keeps_confirmed(self):
        confirmed = self._shop("Confirmed Print Works", "phase4-confirmed")
        ShopRateCardSetup.objects.create(shop=confirmed, is_confirmed=True)
        unconfirmed = self._shop("Unconfirmed Print Works", "phase4-unconfirmed")
        ShopRateCardSetup.objects.create(shop=unconfirmed, is_confirmed=False)
        never_opened = self._shop("Never Opened Print Works", "phase4-never-opened")

        self.client.force_authenticate(user=self.manager)
        response = self.client.get("/api/dashboard/partner/production-shops/")

        self.assertEqual(response.status_code, 200)
        names = {row["name"] for row in response.json()["results"]}
        self.assertIn(confirmed.name, names)
        self.assertIn(never_opened.name, names)
        self.assertNotIn(unconfirmed.name, names)

    def test_directory_hides_confirmed_but_incomplete_shop(self):
        """A shop confirmed earlier whose live pricing data was later removed.

        The directory still requires active papers, machines and printing
        rates, so it must not be listed even though it is confirmed. shop-options
        keeps its long-standing behaviour of surfacing such a shop only as an
        ineligible diagnostic row (never as a priceable option), because that is
        a completeness outcome, not the D4 confirmation gate.
        """
        incomplete = self._shop("Incomplete Confirmed Works", "phase4-incomplete", priced=False)
        ShopRateCardSetup.objects.create(shop=incomplete, is_confirmed=True)

        self.assertNotIn(incomplete.name, self._directory_names())
        self.assertNotIn(incomplete.id, self._priced_shop_option_ids(self._quote_request()))

    def test_no_row_shop_is_treated_identically_by_both_find_printer_surfaces(self):
        """Shops with no ShopRateCardSetup row keep today's visible semantics.

        law 11.7 / D4 only hides an EXPLICITLY unconfirmed (is_confirmed=False)
        setup row. A shop that never opened the rate-card screen has no row and
        stays visible in both the directory and shop-options. Task 1b asks this
        behaviour be preserved exactly, not changed on a guess.
        """
        no_row = self._shop("No Row Works", "phase4-no-row")

        self.assertIn(no_row.name, self._directory_names())
        self.assertIn(no_row.id, self._shop_option_ids(self._quote_request()))

    def test_shop_creation_does_not_create_a_rate_card_setup_row(self):
        """Task 1b: the no-row case is reachable, so it is NOT moot.

        Nothing in shops/signals.py creates a ShopRateCardSetup on Shop
        creation, so a shop can exist with no setup row until the owner saves
        a rate card (services/pricing/mvp_rate_card.py:1837) or
        `manage.py seed_paper_catalog` runs. Such a shop stays visible.
        """
        shop = self._shop("Freshly Created Works", "phase4-fresh")

        self.assertFalse(ShopRateCardSetup.objects.filter(shop=shop).exists())
        self.assertIn(shop.name, self._directory_names())
        self.assertIn(shop.id, self._shop_option_ids(self._quote_request()))

    def test_directory_and_shop_options_return_consistent_sets_for_same_data(self):
        """Task 1c: one shared gate, so the two surfaces cannot drift apart."""
        confirmed = self._shop("Consistent Confirmed", "phase4-cons-conf")
        ShopRateCardSetup.objects.create(shop=confirmed, is_confirmed=True)
        unconfirmed = self._shop("Consistent Unconfirmed", "phase4-cons-unconf")
        ShopRateCardSetup.objects.create(shop=unconfirmed, is_confirmed=False)
        no_row = self._shop("Consistent No Row", "phase4-cons-norow")

        directory = self._directory_names()
        options = self._shop_option_ids(self._quote_request())

        self.assertEqual(
            {confirmed.name, no_row.name, unconfirmed.name} & directory,
            {confirmed.name, no_row.name},
        )
        self.assertEqual({confirmed.id, no_row.id, unconfirmed.id} & options, {confirmed.id, no_row.id})
        # The membership decision itself is identical for the same rows.
        for shop in (confirmed, unconfirmed, no_row):
            self.assertEqual(shop.name in directory, shop.id in options)

    def test_client_cannot_access_manager_production_shop_directory(self):
        self._shop("Client Hidden Shop", "phase4-client-hidden")

        self.client.force_authenticate(user=self.client_user)
        response = self.client.get("/api/dashboard/partner/production-shops/")

        self.assertEqual(response.status_code, 403)
