from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from accounts.models import User
from payments.models import Payment
from payments.services import create_payment_for_quote
from pricing.models import PlatformFeePolicy, QuantityPricingTier, SetupCostPolicy, WastePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from pricing.services.production_cost_calculator import (
    calculate_billable_sheets,
    calculate_client_price_with_waste_setup_and_quantity_tier,
    calculate_penalized_financial_split,
    calculate_setup_cost,
    find_quantity_pricing_tier,
)
from quotes.choices import QuoteOfferStatus
from quotes.models import ProductionOption, Quote, QuoteRequest
from shops.models import Shop


class WastagePricingTestCase(TestCase):
    def setUp(self):
        WastePolicy.objects.all().delete()
        SetupCostPolicy.objects.all().delete()
        QuantityPricingTier.objects.all().delete()
        PlatformFeePolicy.objects.all().delete()
        self.waste_policy = WastePolicy.objects.create(
            name="Test Waste Policy",
            fixed_waste_sheets=2,
            variable_waste_rate=Decimal("0.1000"),
            minimum_billable_sheets=3,
        )
        self.setup_policy = SetupCostPolicy.objects.create(
            name="Test Setup Policy",
            setup_minutes=10,
            labor_rate_per_hour=Decimal("500.00"),
            machine_setup_fee=Decimal("75.00"),
            admin_handling_fee=Decimal("50.00"),
            file_check_fee=Decimal("50.00"),
        )
        self.tiers = [
            QuantityPricingTier.objects.create(
                name="Tier 1",
                min_sheets=1,
                max_sheets=5,
                multiplier=Decimal("6.00"),
                minimum_order_floor=Decimal("1500.00"),
            ),
            QuantityPricingTier.objects.create(
                name="Tier 2",
                min_sheets=6,
                max_sheets=20,
                multiplier=Decimal("3.50"),
                minimum_order_floor=Decimal("1200.00"),
            ),
            QuantityPricingTier.objects.create(
                name="Tier 3",
                min_sheets=21,
                max_sheets=100,
                multiplier=Decimal("2.00"),
                minimum_order_floor=Decimal("800.00"),
            ),
            QuantityPricingTier.objects.create(
                name="Tier 4",
                min_sheets=101,
                max_sheets=None,
                multiplier=Decimal("1.50"),
                minimum_order_floor=Decimal("500.00"),
            ),
        ]
        self.platform_policy = PlatformFeePolicy.objects.create(
            name="Wastage Split Policy",
            printer_fee_rate=Decimal("0.0500"),
            broker_margin_fee_rate=Decimal("0.1500"),
            small_job_max_multiple=Decimal("8.00"),
            medium_job_max_multiple=Decimal("8.00"),
            bulk_job_max_multiple=Decimal("8.00"),
        )

    def _price(self, **overrides):
        spec = {
            "quantity": 1,
            "yield_per_sheet": 1,
            "paper_cost_per_sheet": Decimal("18.00"),
            "click_charge_per_sheet": Decimal("25.00"),
            "finishing_cost": Decimal("50.00"),
        }
        spec.update(overrides)
        return calculate_client_price_with_waste_setup_and_quantity_tier(
            spec,
            waste_policy=self.waste_policy,
            setup_policy=self.setup_policy,
        )

    def test_one_sheet_sample_order_uses_tier_one_and_floor(self):
        payload = self._price()
        self.assertEqual(payload["raw_sheets"], 1)
        self.assertEqual(payload["billable_sheets"], 4)
        self.assertEqual(payload["production_cost"], Decimal("480.33"))
        self.assertEqual(payload["volume_multiplier"], Decimal("6.00"))
        self.assertEqual(payload["final_client_price"], Decimal("2881.98"))

    def test_ten_card_order_uses_low_volume_penalty(self):
        payload = self._price(
            quantity=10,
            yield_per_sheet=18,
            paper_cost_per_sheet=Decimal("24.00"),
            click_charge_per_sheet=Decimal("45.00"),
        )
        self.assertEqual(payload["raw_sheets"], 1)
        self.assertEqual(payload["billable_sheets"], 4)
        self.assertEqual(payload["production_cost"], Decimal("584.33"))
        self.assertEqual(payload["volume_multiplier"], Decimal("6.00"))
        self.assertEqual(payload["final_client_price"], Decimal("3505.98"))

    def test_one_hundred_card_order_uses_mid_tier(self):
        payload = self._price(
            quantity=100,
            yield_per_sheet=18,
            paper_cost_per_sheet=Decimal("24.00"),
            click_charge_per_sheet=Decimal("45.00"),
        )
        self.assertEqual(payload["raw_sheets"], 6)
        self.assertEqual(payload["billable_sheets"], 9)
        self.assertEqual(payload["production_cost"], Decimal("929.33"))
        self.assertEqual(payload["volume_multiplier"], Decimal("3.50"))
        self.assertEqual(payload["final_client_price"], Decimal("3252.66"))

    def test_one_thousand_card_order_uses_lower_multiplier(self):
        payload = self._price(
            quantity=1000,
            yield_per_sheet=18,
            paper_cost_per_sheet=Decimal("24.00"),
            click_charge_per_sheet=Decimal("45.00"),
        )
        self.assertEqual(payload["raw_sheets"], 56)
        self.assertEqual(payload["billable_sheets"], 64)
        self.assertEqual(payload["production_cost"], Decimal("4724.33"))
        self.assertEqual(payload["volume_multiplier"], Decimal("2.00"))
        self.assertEqual(payload["final_client_price"], Decimal("9448.66"))

    def test_fixed_waste_sheets_are_applied(self):
        payload = calculate_billable_sheets(quantity=20, yield_per_sheet=10, waste_policy=self.waste_policy)
        self.assertEqual(payload["raw_sheets"], 2)
        self.assertEqual(payload["fixed_waste_sheets"], 2)
        self.assertEqual(payload["total_sheets_needed"], 5)

    def test_variable_waste_rate_is_applied(self):
        payload = calculate_billable_sheets(quantity=100, yield_per_sheet=10, waste_policy=self.waste_policy)
        self.assertEqual(payload["raw_sheets"], 10)
        self.assertEqual(payload["variable_waste_sheets"], 1)
        self.assertEqual(payload["total_sheets_needed"], 13)

    def test_minimum_billable_sheets_is_enforced(self):
        policy = WastePolicy.objects.create(
            name="Minimum Billable Policy",
            fixed_waste_sheets=0,
            variable_waste_rate=Decimal("0.0000"),
            minimum_billable_sheets=5,
        )
        payload = calculate_billable_sheets(quantity=1, yield_per_sheet=10, waste_policy=policy)
        self.assertEqual(payload["total_sheets_needed"], 1)
        self.assertEqual(payload["billable_sheets"], 5)

    def test_setup_time_labor_cost_is_included(self):
        payload = calculate_setup_cost(setup_policy=self.setup_policy)
        self.assertEqual(payload["setup_labor_cost"], Decimal("83.33"))
        self.assertEqual(payload["setup_cost"], Decimal("258.33"))

    def test_minimum_order_floor_is_enforced(self):
        policy = SetupCostPolicy.objects.create(
            name="Zero Setup Policy",
            setup_minutes=0,
            labor_rate_per_hour=Decimal("0.00"),
            machine_setup_fee=Decimal("0.00"),
            admin_handling_fee=Decimal("0.00"),
            file_check_fee=Decimal("0.00"),
        )
        payload = calculate_client_price_with_waste_setup_and_quantity_tier(
            {
                "quantity": 1,
                "yield_per_sheet": 1,
                "paper_cost_per_sheet": Decimal("1.00"),
                "click_charge_per_sheet": Decimal("1.00"),
                "finishing_cost": Decimal("0.00"),
            },
            waste_policy=self.waste_policy,
            setup_policy=policy,
        )
        self.assertEqual(payload["calculated_client_price"], Decimal("48.00"))
        self.assertEqual(payload["final_client_price"], Decimal("1500.00"))

    def test_quantity_tier_boundaries(self):
        expected = {
            5: Decimal("6.00"),
            6: Decimal("3.50"),
            20: Decimal("3.50"),
            21: Decimal("2.00"),
            100: Decimal("2.00"),
            101: Decimal("1.50"),
        }
        for billable_sheets, multiplier in expected.items():
            with self.subTest(billable_sheets=billable_sheets):
                tier = find_quantity_pricing_tier(billable_sheets=billable_sheets)
                self.assertEqual(tier.multiplier, multiplier)

    def test_quote_financial_split_uses_final_client_price(self):
        user = User.objects.create_user(email="waste-split@test.com", password="pass")
        shop = Shop.objects.create(owner=user, name="Wastage Split Shop", slug="wastage-split-shop")
        quote_request = QuoteRequest.objects.create(created_by=user, customer_name="Client")
        option = ProductionOption.objects.create(
            quote_request=quote_request,
            shop=shop,
            production_cost=Decimal("480.33"),
            created_by=user,
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=shop,
            production_option=option,
            created_by=user,
            status=QuoteOfferStatus.SENT,
            total=Decimal("2881.98"),
        )
        pricing = calculate_penalized_financial_split(
            {
                "quantity": 1,
                "yield_per_sheet": 1,
                "paper_cost_per_sheet": Decimal("18.00"),
                "click_charge_per_sheet": Decimal("25.00"),
                "finishing_cost": Decimal("50.00"),
            },
            waste_policy=self.waste_policy,
            setup_policy=self.setup_policy,
            platform_policy=self.platform_policy,
        )
        split = create_quote_financial_split(
            quote=quote,
            production_option=option,
            production_cost=pricing["production_cost"],
            broker_client_price=pricing["final_client_price"],
            policy=self.platform_policy,
        )
        self.assertEqual(split.production_cost, Decimal("480.33"))
        self.assertEqual(split.broker_client_price, Decimal("2881.98"))
        self.assertEqual(split.client_total, Decimal("2881.98"))
        self.assertEqual(split.shop_payout, Decimal("480.33"))
        self.assertEqual(split.printy_fee, Decimal("384.27"))

    def test_payment_does_not_calculate_pricing(self):
        user = User.objects.create_user(email="payment-no-pricing@test.com", password="pass")
        shop = Shop.objects.create(owner=user, name="Payment No Pricing Shop", slug="payment-no-pricing-shop")
        quote_request = QuoteRequest.objects.create(created_by=user, customer_name="Client")
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=shop,
            created_by=user,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1500.00"),
        )
        create_quote_financial_split(
            quote=quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            policy=self.platform_policy,
        )
        with patch("pricing.services.production_cost_calculator.calculate_client_price_with_waste_setup_and_quantity_tier") as calculator:
            payment = create_payment_for_quote(quote=quote, payer=user)
        calculator.assert_not_called()
        self.assertEqual(payment.status, Payment.STATUS_PENDING)
        self.assertFalse(hasattr(__import__("jobs.models", fromlist=["JobPayment"]), "JobPayment"))
