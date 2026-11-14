"""Phase 3A Task B: additive owner guidance (setup_status).

D2 (Wilson, binding): shop owners must always be shown what is missing and
what to do next to complete and confirm their setup. D3: no model changes,
no migrations — everything is computed from existing data.

The new `setup_status` block is ADDITIVE: it never removes or renames the
existing setup-status fields.
"""

from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import PrintingRate, ShopRateCardSetup
from services.pricing.mvp_rate_card import complete_shop_rate_card_setup
from setup.services import (
    READINESS_STEPS,
    get_setup_status_for_shop,
    _product_turnaround_configured,
)
from shops.models import Shop

STEP_CODES = {"machine", "paper", "printing_rate", "standard_turnaround", "confirm"}


class RateCardSetupStatusTests(TestCase):
    def _shop(self, slug, *, paper=False, rate=False):
        user = User.objects.create_user(
            email=f"{slug}@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        shop = Shop.objects.create(
            owner=user,
            name=f"{slug} Press",
            slug=slug,
            is_active=True,
            is_public=True,
            # LAW 17: a complete shop also declares its standard turnaround.
            standard_turnaround_hours=48 if (paper and rate) else None,
        )
        # Every shop is auto-created with a default machine (shops/signals.py),
        # so the machine step is done unless we explicitly deactivate it.
        if paper:
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
        if rate:
            machine = Machine.objects.filter(shop=shop).first()
            PrintingRate.objects.create(
                machine=machine,
                sheet_size="SRA3",
                color_mode=ColorMode.COLOR,
                single_price=Decimal("35.00"),
                double_price=Decimal("70.00"),
                is_active=True,
            )
        return shop

    def test_empty_shop_not_ready_and_next_step_present(self):
        shop = self._shop("b-status-empty")
        status = get_setup_status_for_shop(shop)["setup_status"]

        self.assertFalse(status["is_confirmed"])
        self.assertFalse(status["is_ready_to_confirm"])
        self.assertEqual(status["total_steps"], 5)
        self.assertEqual(status["completed_steps"], 1)  # machine auto-created
        self.assertIsNotNone(status["next_step"])
        self.assertIn(status["next_step"]["code"], STEP_CODES)
        self.assertTrue(status["next_step"]["hint"])

    def test_steps_are_stable_codes_with_routes(self):
        shop = self._shop("b-status-codes")
        status = get_setup_status_for_shop(shop)["setup_status"]

        codes = {step["code"] for step in status["steps"]}
        self.assertEqual(codes, STEP_CODES)
        for step in status["steps"]:
            self.assertIn("title", step)
            self.assertIn("hint", step)
            self.assertIn("done", step)
            self.assertTrue(step["route"].startswith("/"))

    def test_shop_with_pricing_is_ready_to_confirm_but_not_confirmed(self):
        shop = self._shop("b-status-ready", paper=True, rate=True)
        status = get_setup_status_for_shop(shop)["setup_status"]

        self.assertTrue(status["is_ready_to_confirm"])
        self.assertFalse(status["is_confirmed"])
        self.assertEqual(status["completed_steps"], 4)
        self.assertEqual(status["next_step"]["code"], "confirm")

    def test_shop_with_pricing_but_no_standard_turnaround_is_not_ready(self):
        shop = self._shop("b-status-no-turnaround", paper=True, rate=True)
        Shop.objects.filter(pk=shop.pk).update(standard_turnaround_hours=None)
        shop.refresh_from_db()

        status = get_setup_status_for_shop(shop)["setup_status"]

        self.assertFalse(status["is_ready_to_confirm"])
        self.assertEqual(status["completed_steps"], 3)
        self.assertEqual(status["next_step"]["code"], "standard_turnaround")
        turnaround_step = next(
            step for step in status["steps"] if step["code"] == "standard_turnaround"
        )
        self.assertFalse(turnaround_step["done"])
        self.assertTrue(turnaround_step["route"].startswith("/app/printer/rates"))

    def test_confirmed_shop_reports_confirmed(self):
        shop = self._shop("b-status-confirmed", paper=True, rate=True)
        complete_shop_rate_card_setup(shop)
        status = get_setup_status_for_shop(shop)["setup_status"]

        self.assertTrue(status["is_confirmed"])
        # Already live: there is nothing left to confirm.
        self.assertFalse(status["is_ready_to_confirm"])
        self.assertEqual(status["completed_steps"], 5)
        self.assertIsNone(status["next_step"])

    def test_confirmed_shop_that_became_unusable_still_reports_confirmed(self):
        shop = self._shop("b-status-became-unusable", paper=True, rate=True)
        complete_shop_rate_card_setup(shop)

        # Paper stock is pulled; the card is no longer usable.
        Paper.objects.filter(shop=shop).update(is_active=False)

        status = get_setup_status_for_shop(shop)["setup_status"]
        self.assertTrue(status["is_confirmed"])
        self.assertFalse(status["is_ready_to_confirm"])
        self.assertIsNotNone(status["next_step"])

    def test_additive_never_removes_existing_fields(self):
        shop = self._shop("b-status-additive")
        status = get_setup_status_for_shop(shop)

        # Pre-existing fields must remain present and unchanged in meaning.
        for key in ("has_shop", "has_materials", "next_step", "steps", "setup_percent"):
            self.assertIn(key, status)
        self.assertIsInstance(status["next_step"], str)
        self.assertIsInstance(status["setup_status"], dict)


class ProductFieldErrorWorkaroundTests(TestCase):
    """Phase 3A.1 Task 2: the Product FieldError workaround must not lie.

    `catalog.models.Product` has no `shop` relation, so a per-shop product
    count / per-shop product turnaround cannot be derived (step 2c). The old
    code answered with the silent constants `0` and `False`, which every
    consumer reads as "the shop has no products" / "turnaround not
    configured". Both are now explicit unknowns.
    """

    def _shop(self, slug, *, statement=""):
        user = User.objects.create_user(
            email=f"{slug}@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        return Shop.objects.create(
            owner=user,
            name=f"{slug} Press",
            slug=slug,
            is_active=True,
            is_public=True,
            turnaround_statement=statement,
        )

    def test_product_turnaround_probe_is_explicitly_unknown(self):
        shop = self._shop("c-product-probe")

        self.assertIsNone(_product_turnaround_configured(shop))

    def test_has_products_is_explicitly_unknown_for_a_shop(self):
        shop = self._shop("c-products-null")

        status = get_setup_status_for_shop(shop)

        self.assertIsNone(status["has_products"])
        # products_count is a local only; it was never a response field.
        self.assertNotIn("products_count", status)

    def test_product_probe_can_never_flip_turnaround_to_not_configured(self):
        """Regression: `... or _product_turnaround_configured(shop)` is gone."""
        configured = self._shop("c-turnaround-set", statement="3 working days")
        unconfigured = self._shop("c-turnaround-unset")

        self.assertTrue(
            get_setup_status_for_shop(configured)["turnaround_configured"]
        )
        self.assertFalse(
            get_setup_status_for_shop(unconfigured)["turnaround_configured"]
        )

    def test_products_is_not_a_readiness_step_so_null_is_invisible_to_the_owner(self):
        """No owner-facing step can show 'not configured' because of Task 2."""
        shop = self._shop("c-products-step")

        readiness_keys = {key for key, _label, _cta in READINESS_STEPS}
        self.assertNotIn("products", readiness_keys)

        rendered_keys = {step["key"] for step in get_setup_status_for_shop(shop)["steps"]}
        self.assertNotIn("products", rendered_keys)
        self.assertEqual(rendered_keys, readiness_keys)

    def test_null_does_not_break_percent_or_completeness(self):
        shop = self._shop("c-products-percent")

        status = get_setup_status_for_shop(shop)

        for key in ("setup_percent", "rate_card_completeness", "completed_steps", "warnings"):
            self.assertIn(key, status)
        self.assertIsInstance(status["setup_percent"], int)
