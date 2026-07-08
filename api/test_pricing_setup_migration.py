from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from catalog.choices import ProductStatus
from catalog.models import Product
from inventory.choices import MachineType, PaperCategory, PaperType, SheetSize
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import FinishingRate, PrintingRate
from services.pricing.mvp_rate_card import build_shop_rate_card_setup, save_shop_rate_card_setup
from services.pricing.partner_market_rates import build_partner_market_rate_payload
from shops.models import Shop


class PricingSetupMigrationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="pricing-owner@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.other = User.objects.create_user(
            email="pricing-other@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.staff = User.objects.create_user(
            email="pricing-staff@test.com",
            password="pass12345",
            role=User.Role.STAFF,
            is_staff=True,
        )
        self.partner = User.objects.create_user(
            email="pricing-partner@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Canonical Pricing Shop",
            slug="canonical-pricing-shop",
            is_active=True,
            is_public=True,
        )

    def _create_canonical_rate_card(self, shop=None, *, base_price="35.00"):
        shop = shop or self.shop
        machine = Machine.objects.create(
            shop=shop,
            name="Digital Press",
            machine_type=MachineType.DIGITAL,
            max_width_mm=450,
            max_height_mm=320,
            is_active=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size=SheetSize.SRA3,
            color_mode=ColorMode.COLOR,
            single_price=Decimal("15.00"),
            double_price=Decimal("30.00"),
            duplex_surcharge=Decimal("10.00"),
            duplex_surcharge_enabled=True,
            duplex_surcharge_min_gsm=250,
            is_active=True,
            is_default=True,
        )
        Paper.objects.create(
            shop=shop,
            name="300gsm Matte/Art Card",
            sheet_size=SheetSize.SRA3,
            gsm=300,
            category=PaperCategory.ARTCARD,
            paper_type=PaperType.MATTE,
            buying_price=Decimal(base_price),
            selling_price=Decimal(base_price),
            quantity_in_stock=500,
            is_active=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting Standard",
            slug="cutting",
            price=Decimal("480.00"),
            is_active=True,
        )
        return shop

    def test_shop_model_has_no_mvp_rate_card_field(self):
        self.assertFalse(hasattr(self.shop, "mvp_rate_card"))
        self.assertNotIn("mvp_rate_card", {field.name for field in Shop._meta.get_fields()})

    def test_rate_card_setup_projects_from_canonical_tables(self):
        self._create_canonical_rate_card()

        data = build_shop_rate_card_setup(self.shop)
        row = next(item for item in data["paper_rows"] if item["key"] == "300gsm_matte_art_card")

        self.assertTrue(row["active"])
        self.assertEqual(row["paper_base_price"], "35.00")
        self.assertEqual(row["manager_visible_single_total"], "60.00")
        self.assertEqual(row["manager_visible_double_total"], "75.00")
        self.assertTrue(data["summary"]["paper_rows_added"])

    def test_rate_card_config_and_empty_shop_setup_do_not_500_on_zero_example_total(self):
        public_response = self.client.get("/api/for-shops/rate-card/public-config/")
        self.assertEqual(public_response.status_code, 200)
        self.assertIsNone(public_response.json()["example_quote"]["pricing_breakdown"])

        self.client.force_authenticate(user=self.owner)
        setup_response = self.client.get(
            "/api/shops/rate-card/setup/",
            {"shop_slug": self.shop.slug},
        )
        self.assertEqual(setup_response.status_code, 200)
        self.assertIsNone(setup_response.json()["example_quote"]["pricing_breakdown"])

    def test_rate_card_save_persists_to_canonical_tables(self):
        payload = save_shop_rate_card_setup(
            self.shop,
            paper_rows=[
                {
                    "key": "300gsm_matte_art_card",
                    "paper_base_price": "35.00",
                    "single_print_base": "15.00",
                    "double_print_base": "30.00",
                    "heavy_paper_surcharge": "10.00",
                    "active": True,
                }
            ],
            finishing_rows=[
                {"key": "cutting", "price": "480.00", "active": True},
            ],
            shop_details={"shop_name": "Saved Canonical Shop", "whatsapp_number": "+254700000000", "location_area": "Nairobi"},
            completed=True,
        )
        self.shop.refresh_from_db()

        self.assertEqual(payload["paper_rows"][0]["key"], "300gsm_matte_art_card")
        self.assertEqual(self.shop.name, "Saved Canonical Shop")
        self.assertTrue(self.shop.pricing_ready)
        self.assertTrue(Paper.objects.filter(shop=self.shop, gsm=300, is_active=True).exists())
        self.assertTrue(PrintingRate.objects.filter(machine__shop=self.shop, sheet_size=SheetSize.SRA3, is_active=True).exists())
        self.assertTrue(FinishingRate.objects.filter(shop=self.shop, slug="cutting", is_active=True).exists())
        self.assertFalse(hasattr(self.shop, "mvp_rate_card"))

    def test_partner_market_rates_use_canonical_rows_without_shop_identity(self):
        for index, base_price in enumerate(("30.00", "35.00", "40.00"), start=1):
            owner = User.objects.create_user(email=f"market-owner-{index}@test.com", password="pass12345", role=User.Role.PRODUCTION)
            shop = Shop.objects.create(owner=owner, name=f"Market Shop {index}", slug=f"market-shop-{index}", is_active=True)
            self._create_canonical_rate_card(shop, base_price=base_price)

        data = build_partner_market_rate_payload(user=self.partner)
        row = next(item for item in data["results"] if item["key"] == "300gsm_matte_art_card")
        payload_text = str(data)

        self.assertEqual(row["shops_count"], 3)
        self.assertEqual(row["data_quality"], "good")
        self.assertEqual(row["market_single"]["median_total_100"], "300.00")
        self.assertNotIn("Market Shop 1", payload_text)
        self.assertNotIn("market-shop-1", payload_text)
        self.assertNotIn("formula_shop_visible", payload_text)

    def test_shop_owner_staff_and_other_user_pricing_permissions(self):
        machine = Machine.objects.create(
            shop=self.shop,
            name="Permission Press",
            machine_type=MachineType.DIGITAL,
            max_width_mm=450,
            max_height_mm=320,
            is_active=True,
        )

        self.client.force_authenticate(user=self.other)
        forbidden = self.client.post(
            f"/api/shops/{self.shop.slug}/papers/",
            {"name": "Blocked", "sheet_size": "SRA3", "gsm": 300, "paper_type": "MATTE", "category": "artcard", "buying_price": "1.00", "selling_price": "1.00"},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_authenticate(user=self.owner)
        allowed = self.client.post(
            f"/api/shops/{self.shop.slug}/papers/",
            {"name": "Owner Stock", "sheet_size": "SRA3", "gsm": 301, "paper_type": "MATTE", "category": "artcard", "buying_price": "1.00", "selling_price": "1.00"},
            format="json",
        )
        self.assertEqual(allowed.status_code, 201)

        self.client.force_authenticate(user=self.staff)
        staff_allowed = self.client.post(
            f"/api/machines/{machine.id}/printing-rates/",
            {"sheet_size": "SRA3", "color_mode": "COLOR", "single_price": "15.00", "double_price": "30.00", "is_active": True},
            format="json",
        )
        self.assertEqual(staff_allowed.status_code, 201)

    def test_paper_update_duplicate_identity_returns_validation_error(self):
        first = Paper.objects.create(
            shop=self.shop,
            name="Existing Matte",
            sheet_size=SheetSize.SRA3,
            gsm=300,
            paper_type=PaperType.MATTE,
            category=PaperCategory.ARTCARD,
            buying_price=Decimal("10.00"),
            selling_price=Decimal("15.00"),
        )
        second = Paper.objects.create(
            shop=self.shop,
            name="Gloss Candidate",
            sheet_size=SheetSize.SRA3,
            gsm=300,
            paper_type=PaperType.GLOSS,
            category=PaperCategory.ARTCARD,
            buying_price=Decimal("12.00"),
            selling_price=Decimal("18.00"),
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.patch(
            f"/api/shops/{self.shop.slug}/papers/{second.id}/",
            {"paper_type": PaperType.MATTE},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "VALIDATION_ERROR")
        self.assertIn("non_field_errors", response.json()["field_errors"])
        second.refresh_from_db()
        self.assertEqual(second.paper_type, PaperType.GLOSS)
        self.assertTrue(Paper.objects.filter(pk=first.pk).exists())

    def test_shop_finishing_rates_endpoint_does_not_select_removed_category_relation(self):
        FinishingRate.objects.create(
            shop=self.shop,
            name="Matte Lamination",
            slug="matte-lamination",
            price=Decimal("20.00"),
            is_active=True,
        )
        self.client.force_authenticate(user=self.owner)

        response = self.client.get(f"/api/shops/{self.shop.slug}/finishing-rates/")
        filtered = self.client.get(f"/api/shops/{self.shop.slug}/finishing-rates/", {"category": "lamination"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(filtered.status_code, 200)
        rows = filtered.json().get("results", filtered.json())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slug"], "matte-lamination")

    def test_self_service_shop_creation_is_private_until_approved(self):
        self.client.force_authenticate(user=self.other)

        response = self.client.post(
            "/api/shops/",
            {
                "name": "Self Service Shop",
                "business_email": "self-service@test.com",
                "phone_number": "+254700111222",
                "address_line": "Nairobi",
                "city": "Nairobi",
                "state": "Nairobi",
                "country": "Kenya",
                "zip_code": "00100",
            },
            format="json",
        )
        shop = Shop.objects.get(owner=self.other, name="Self Service Shop")

        self.assertEqual(response.status_code, 201)
        self.assertFalse(shop.is_public)
        self.assertFalse(shop.pricing_ready)
        self.assertFalse(shop.public_match_ready)

    def test_public_calculator_projection_hides_internal_economics(self):
        response = self.client.post(
            "/api/calculator/public-preview/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "90x55mm",
                "paper_stock": "300gsm",
                "print_sides": "DUPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )
        payload_text = str(response.json())

        self.assertEqual(response.status_code, 200)
        for forbidden in ("production_cost", "shop_payout", "broker_payout", "printy_fee", "gross_margin", "pricing_snapshot"):
            self.assertNotIn(forbidden, payload_text)

    def test_public_products_endpoint_returns_global_catalog_products_without_private_fields(self):
        public_product = Product.objects.create(
            name="Public Business Cards",
            description="Public catalog product",
            status=ProductStatus.PUBLISHED,
            is_active=True,
            is_public=True,
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            min_quantity=100,
        )
        Product.objects.create(
            name="Draft Business Cards",
            status=ProductStatus.DRAFT,
            is_active=True,
            is_public=True,
        )
        Product.objects.create(
            name="Private Business Cards",
            status=ProductStatus.PUBLISHED,
            is_active=True,
            is_public=False,
        )

        response = self.client.get("/api/public/products/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        rows = payload["products"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], public_product.id)
        for forbidden in (
            "shop",
            "production_cost",
            "shop_payout",
            "broker_payout",
            "printy_fee",
            "gross_margin",
            "pricing_snapshot",
        ):
            self.assertNotIn(forbidden, rows[0])