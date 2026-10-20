"""Phase 3A Task A: confirmation is an explicit owner action.

D1 (Wilson, binding): a shop is COMPLETE only when its owner has CONFIRMED
its rate card (ShopRateCardSetup.is_confirmed). Confirmation stays the
visibility gate (law 11.7). Confirmation is granted only through the owner's
explicit confirm/complete action AND only when shop_rate_card_is_usable()
passes.

A2/A3: a plain draft save must NOT confirm the setup.
A4: an already-confirmed setup is never auto-downgraded by a save.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import PrintingRate, ShopRateCardSetup
from services.pricing.mvp_rate_card import (
    RateCardNotUsable,
    _build_default_paper_rows,
    build_shop_rate_card_setup,
    complete_shop_rate_card_setup,
    save_shop_rate_card_setup,
)
from shops.models import Shop


class ExplicitRateCardConfirmationTests(TestCase):
    def _shop(self, slug, *, with_pricing=True):
        owner = User.objects.create_user(
            email=f"{slug}@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        shop = Shop.objects.create(
            owner=owner,
            name=f"{slug} Press",
            slug=slug,
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        if with_pricing:
            machine = Machine.objects.create(
                shop=shop,
                name=f"{slug} machine",
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

    def test_plain_save_does_not_confirm(self):
        shop = self._shop("plain-save")
        save_shop_rate_card_setup(
            shop,
            paper_rows=_build_default_paper_rows(),
            finishing_rows=[],
            completed=False,
        )
        setup = ShopRateCardSetup.objects.get(shop=shop)
        self.assertFalse(setup.is_confirmed)

    def test_complete_action_confirms_when_usable(self):
        shop = self._shop("complete-usable")
        save_shop_rate_card_setup(
            shop,
            paper_rows=_build_default_paper_rows(),
            finishing_rows=[],
            completed=False,
        )
        self.assertFalse(ShopRateCardSetup.objects.get(shop=shop).is_confirmed)

        complete_shop_rate_card_setup(shop)

        self.assertTrue(ShopRateCardSetup.objects.get(shop=shop).is_confirmed)

    def test_complete_action_rejects_incomplete_with_structured_error(self):
        shop = self._shop("complete-incomplete", with_pricing=False)
        save_shop_rate_card_setup(
            shop,
            paper_rows=_build_default_paper_rows(),
            finishing_rows=[],
            completed=False,
        )
        with self.assertRaises(RateCardNotUsable) as ctx:
            complete_shop_rate_card_setup(shop)
        self.assertIn("missing_steps", ctx.exception.payload)

    def test_confirmed_setup_is_not_downgraded_by_plain_save(self):
        shop = self._shop("no-downgrade")
        save_shop_rate_card_setup(
            shop,
            paper_rows=_build_default_paper_rows(),
            finishing_rows=[],
            completed=False,
        )
        complete_shop_rate_card_setup(shop)
        self.assertTrue(ShopRateCardSetup.objects.get(shop=shop).is_confirmed)

        save_shop_rate_card_setup(
            shop,
            paper_rows=_build_default_paper_rows(),
            finishing_rows=[],
            completed=False,
        )
        self.assertTrue(ShopRateCardSetup.objects.get(shop=shop).is_confirmed)

    def test_build_payload_reports_confirmation_state(self):
        shop = self._shop("payload-state")
        save_shop_rate_card_setup(
            shop,
            paper_rows=_build_default_paper_rows(),
            finishing_rows=[],
            completed=False,
        )
        self.assertFalse(build_shop_rate_card_setup(shop)["is_confirmed"])
        complete_shop_rate_card_setup(shop)
        self.assertTrue(build_shop_rate_card_setup(shop)["is_confirmed"])


class ConfirmEndpointTests(TestCase):
    """A4: the owner confirm endpoint returns a structured 400 when incomplete."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            email="confirm-endpoint@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Confirm Endpoint Press",
            slug="confirm-endpoint",
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        self.client.force_authenticate(user=self.owner)

    def test_complete_endpoint_returns_structured_400_when_incomplete(self):
        response = self.client.post("/api/shops/rate-card/onboarding-complete/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        body = response.json()
        self.assertIn("missing_steps", body)
