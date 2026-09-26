"""Setup readiness tests.

Ported from the legacy `setup/tests.py`, which was permanently disabled by a
module-level skip guard and therefore never executed. Only the assertions that
still describe current behaviour were kept; the rest of that file targeted
removed `Product` fields and deleted routes and was deleted.
"""

from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.models import PrintingRate
from shops.models import Shop

from .services import get_setup_status, pricing_exists


class SetupStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="printer@test.com",
            password="test1234",
            name="Test Printer",
        )

    def test_no_shop_returns_shop_step(self):
        status = get_setup_status(self.user)
        self.assertFalse(status["has_shop"])
        self.assertEqual(status["next_step"], "shop")


class PricingExistsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="p2@test.com",
            password="test1234",
            name="P2",
        )
        self.shop = Shop.objects.create(name="Shop2", owner=self.user, currency="KES")

    def test_no_pricing(self):
        self.assertFalse(pricing_exists(self.shop))

    def test_machine_and_paper_and_rate(self):
        machine = Machine.objects.create(
            name="M1",
            shop=self.shop,
            machine_type="DIGITAL",
            max_width_mm=320,
            max_height_mm=450,
        )
        Paper.objects.create(
            shop=self.shop,
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            buying_price=Decimal("15"),
            selling_price=Decimal("24"),
            width_mm=320,
            height_mm=450,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("45"),
            double_price=Decimal("75"),
        )
        self.assertTrue(pricing_exists(self.shop))
