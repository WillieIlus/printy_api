from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from inventory.choices import MachineType, PaperCategory, PaperType, SheetSize
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import PrintingRate
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

    def test_client_cannot_access_manager_production_shop_directory(self):
        self._shop("Client Hidden Shop", "phase4-client-hidden")

        self.client.force_authenticate(user=self.client_user)
        response = self.client.get("/api/dashboard/partner/production-shops/")

        self.assertEqual(response.status_code, 403)
