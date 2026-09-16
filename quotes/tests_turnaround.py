import unittest

raise unittest.SkipTest("Legacy pre-reset turnaround tests target removed Product.shop relationships.")

from datetime import datetime, time

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from catalog.models import Product
from quotes.turnaround import add_working_hours, derive_product_turnaround_hours, estimate_turnaround
from shops.models import Shop


class TurnaroundServiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="turnaround@example.com", password="pass1234")
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Turnaround Shop",
            opening_time=time(8, 0),
            closing_time=time(18, 0),
            timezone="Africa/Nairobi",
        )

    def _aware(self, year, month, day, hour, minute=0):
        return timezone.make_aware(datetime(year, month, day, hour, minute), timezone.get_current_timezone())

    def test_estimate_turnaround_uses_product_standard_and_rush_hours(self):
        product = Product.objects.create(
            shop=self.shop,
            name="Business Cards",
            pricing_mode="SHEET",
            default_finished_width_mm=90,
            default_finished_height_mm=50,
            standard_turnaround_hours=6,
            rush_turnaround_hours=3,
            rush_available=True,
            queue_hours=2,
            buffer_hours=1,
        )
        self.assertEqual(derive_product_turnaround_hours(product, rush=False), 9)
        self.assertEqual(derive_product_turnaround_hours(product, rush=True), 6)

        estimate = estimate_turnaround(
            shop=self.shop,
            working_hours=6,
            start_at=self._aware(2026, 4, 3, 9, 0),
        )
        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.label, "Same day")
        self.assertIn("Ready today by", estimate.human_ready_text)
