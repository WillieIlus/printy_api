"""Stage 1B: one ordered missing-steps source for printer setup."""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import PrintingRate
from setup.services import build_rate_card_setup_status, get_setup_status_for_shop
from shops.models import Shop
from shops.services import rate_card_missing_steps


class SetupMissingStepsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="1b-setup@example.com",
            password="pass",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.user,
            name="1B Setup Press",
            slug="1b-setup-press",
            is_active=True,
            is_public=True,
        )

    def _codes(self):
        status = get_setup_status_for_shop(self.shop)
        return [step["code"] for step in status["missing_steps"]]

    def _legacy_codes(self):
        return [step["code"] for step in rate_card_missing_steps(self.shop)]

    def test_machine_is_the_first_missing_step_when_no_active_machine(self):
        Machine.objects.filter(shop=self.shop).update(is_active=False)
        codes = self._codes()
        self.assertEqual(codes[0], "machine")
        self.assertIn("standard_turnaround", codes)
        self.assertEqual(codes, self._legacy_codes())
        self.assertNotIn("confirm", codes)

    def test_paper_then_printing_rate_then_turnaround_in_order(self):
        Machine.objects.filter(shop=self.shop).update(is_active=True)
        self.assertEqual(self._codes()[0], "paper")

        Paper.objects.create(
            shop=self.shop,
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
        self.assertEqual(self._codes(), ["printing_rate", "standard_turnaround"])

        machine = Machine.objects.filter(shop=self.shop, is_active=True).first()
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode=ColorMode.COLOR,
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
        )
        self.assertEqual(self._codes(), ["standard_turnaround"])

        self.shop.standard_turnaround_hours = 48
        self.shop.save(update_fields=["standard_turnaround_hours"])
        self.assertEqual(self._codes(), [])
        status = get_setup_status_for_shop(self.shop)["setup_status"]
        self.assertEqual(status["missing_steps"], [])

    def test_plain_ordered_payload_shape(self):
        status = build_rate_card_setup_status(self.shop)
        self.assertIn("missing_steps", status)
        for step in status["missing_steps"]:
            self.assertEqual(
                set(step),
                {"code", "title", "hint", "done", "route"},
            )
            self.assertFalse(step["done"])
            self.assertTrue(step["route"].startswith("/"))

    def test_setup_status_endpoint_returns_missing_steps(self):
        Machine.objects.filter(shop=self.shop).update(is_active=False)
        api = APIClient()
        api.force_authenticate(self.user)
        response = api.get("/api/setup-status/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["missing_steps"][0]["code"], "machine")
