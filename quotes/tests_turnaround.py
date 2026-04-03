from datetime import datetime, time

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from catalog.models import Product
from quotes.turnaround import add_working_hours, derive_product_turnaround_hours, estimate_turnaround
from shops.models import OpeningHours, Shop


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
        self.shop.opening_hours.all().delete()
        for weekday in range(1, 6):
            OpeningHours.objects.create(shop=self.shop, weekday=weekday, from_hour="08:00", to_hour="18:00", is_closed=False)
        OpeningHours.objects.create(shop=self.shop, weekday=6, from_hour="08:00", to_hour="14:00", is_closed=False)
        OpeningHours.objects.create(shop=self.shop, weekday=7, from_hour="", to_hour="", is_closed=True)

    def _aware(self, year, month, day, hour, minute=0):
        return timezone.make_aware(datetime(year, month, day, hour, minute), timezone.get_current_timezone())

    def test_add_working_hours_spans_to_saturday_half_day(self):
        ready_at = add_working_hours(self._aware(2026, 4, 3, 16, 0), 6, self.shop)
        local_ready = timezone.localtime(ready_at, timezone.get_current_timezone())
        self.assertEqual(local_ready.date().isoformat(), "2026-04-04")
        self.assertEqual(local_ready.strftime("%H:%M"), "12:00")

    def test_add_working_hours_moves_to_next_open_slot(self):
        ready_at = add_working_hours(self._aware(2026, 4, 3, 17, 0), 3, self.shop)
        local_ready = timezone.localtime(ready_at, timezone.get_current_timezone())
        self.assertEqual(local_ready.date().isoformat(), "2026-04-04")
        self.assertEqual(local_ready.strftime("%H:%M"), "10:00")

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
