from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import PrintingRate
from shops.models import Shop

from .services import get_setup_status, get_setup_status_for_shop, pricing_exists, get_product_publish_check


class SetupStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="printer@test.com", password="test1234", name="Test Printer")

    def test_no_shop_returns_shop_step(self):
        status = get_setup_status(self.user)
        self.assertFalse(status["has_shop"])
        self.assertEqual(status["next_step"], "shop")

    def test_shop_no_machines_returns_machines_step(self):
        Shop.objects.create(name="Test Shop", owner=self.user, currency="KES")
        status = get_setup_status(self.user)
        self.assertTrue(status["has_shop"])
        self.assertEqual(status["next_step"], "machines")

    def test_full_setup_returns_done(self):
        shop = Shop.objects.create(name="Test Shop", owner=self.user, currency="KES")
        machine = Machine.objects.create(name="Konica", shop=shop, machine_type="DIGITAL", max_width_mm=320, max_height_mm=450)
        Paper.objects.create(shop=shop, sheet_size="SRA3", gsm=300, paper_type="GLOSS", buying_price=Decimal("15"), selling_price=Decimal("24"), width_mm=320, height_mm=450)
        PrintingRate.objects.create(machine=machine, sheet_size="SRA3", color_mode="COLOR", single_price=Decimal("45"), double_price=Decimal("75"))
        Product.objects.create(shop=shop, name="Business Card", pricing_mode="SHEET", default_finished_width_mm=90, default_finished_height_mm=54, status="PUBLISHED")
        status = get_setup_status(self.user)
        self.assertEqual(status["next_step"], "done")
        self.assertTrue(status["pricing_ready"])

    def test_shop_status_exposes_machine_and_paper_prerequisites(self):
        shop = Shop.objects.create(name="Prereq Shop", owner=self.user, slug="prereq-shop", currency="KES")

        status = get_setup_status_for_shop(shop)
        self.assertFalse(status["has_machines"])
        self.assertFalse(status["has_papers"])
        self.assertEqual(status["next_step"], "machines")

        Machine.objects.create(name="Konica", shop=shop, machine_type="DIGITAL", max_width_mm=320, max_height_mm=450)
        status = get_setup_status_for_shop(shop)
        self.assertTrue(status["has_machines"])
        self.assertEqual(status["next_step"], "papers")


class PricingExistsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="p2@test.com", password="test1234", name="P2")
        self.shop = Shop.objects.create(name="Shop2", owner=self.user, currency="KES")

    def test_no_pricing(self):
        self.assertFalse(pricing_exists(self.shop))

    def test_machine_and_paper_and_rate(self):
        machine = Machine.objects.create(name="M1", shop=self.shop, machine_type="DIGITAL", max_width_mm=320, max_height_mm=450)
        Paper.objects.create(shop=self.shop, sheet_size="SRA3", gsm=300, paper_type="GLOSS", buying_price=Decimal("15"), selling_price=Decimal("24"), width_mm=320, height_mm=450)
        PrintingRate.objects.create(machine=machine, sheet_size="SRA3", color_mode="COLOR", single_price=Decimal("45"), double_price=Decimal("75"))
        self.assertTrue(pricing_exists(self.shop))


class PublishRulesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="p3@test.com", password="test1234", name="P3")
        self.shop = Shop.objects.create(name="Shop3", owner=self.user, currency="KES")

    def test_cannot_publish_without_pricing(self):
        product = Product.objects.create(shop=self.shop, name="Test", pricing_mode="SHEET", default_finished_width_mm=90, default_finished_height_mm=54)
        check = get_product_publish_check(product)
        self.assertFalse(check["can_publish"])
        self.assertTrue(any("printing rates" in r.lower() or "machine" in r.lower() for r in check["block_reasons"]))

    def test_can_publish_with_pricing(self):
        machine = Machine.objects.create(name="M1", shop=self.shop, machine_type="DIGITAL", max_width_mm=320, max_height_mm=450)
        Paper.objects.create(shop=self.shop, sheet_size="SRA3", gsm=300, paper_type="GLOSS", buying_price=Decimal("15"), selling_price=Decimal("24"), width_mm=320, height_mm=450)
        PrintingRate.objects.create(machine=machine, sheet_size="SRA3", color_mode="COLOR", single_price=Decimal("45"), double_price=Decimal("75"))
        product = Product.objects.create(shop=self.shop, name="Business Card", pricing_mode="SHEET", default_finished_width_mm=90, default_finished_height_mm=54)
        check = get_product_publish_check(product)
        self.assertTrue(check["can_publish"])
