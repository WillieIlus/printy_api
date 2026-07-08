from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from accounts.models import User
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import calculate_financial_split, calculate_quote_financials, create_quote_financial_split
from quotes.models import ProductionOption, Quote, QuoteRequest
from shops.models import Shop


class PlatformFeePolicyServiceTestCase(SimpleTestCase):
    def setUp(self):
        self.policy = PlatformFeePolicy()

    def assert_money(self, actual, expected):
        self.assertEqual(actual, Decimal(expected))

    def test_standard_quote_1000_plus_750_uses_approved_fee(self):
        split = calculate_quote_financials(
            production_cost=Decimal("1000.00"),
            manager_markup=Decimal("750.00"),
            policy=self.policy,
        )

        self.assert_money(split.production_fee_component, "50.00")
        self.assert_money(split.markup_fee_component, "362.50")
        self.assert_money(split.printy_fee, "362.50")
        self.assert_money(split.client_total, "1750.00")
        self.assertEqual(split.pricing_tier, "tier_b")

    def test_under_1000_has_no_markup_fee(self):
        split = calculate_financial_split(
            production_cost=Decimal("999.00"),
            manager_markup=Decimal("500.00"),
            policy=self.policy,
        )

        self.assert_money(split["production_fee_component"], "0.00")
        self.assert_money(split["markup_fee_component"], "200.00")
        self.assert_money(split["printy_fee"], "200.00")
        self.assertEqual(split["pricing_tier"], "tier_a")

    def test_1000_boundary_uses_tier_b(self):
        split = calculate_financial_split(
            production_cost=Decimal("1000.00"),
            manager_markup=Decimal("800.00"),
            policy=self.policy,
        )

        self.assert_money(split["production_fee_component"], "50.00")
        self.assert_money(split["markup_fee_component"], "390.00")
        self.assert_money(split["printy_fee"], "390.00")
        self.assertEqual(split["pricing_tier"], "tier_b")

    def test_markup_above_80_percent_still_uses_tier_b_cap(self):
        split = calculate_financial_split(
            production_cost=Decimal("1000.00"),
            manager_markup=Decimal("801.00"),
            policy=self.policy,
        )

        self.assert_money(split["markup_fee_component"], "390.55")
        self.assertEqual(split["pricing_tier"], "tier_b")

    def test_10000_does_not_use_high_production_policy(self):
        split = calculate_financial_split(
            production_cost=Decimal("10000.00"),
            manager_markup=Decimal("1000.00"),
            policy=self.policy,
        )

        self.assert_money(split["production_fee_component"], "500.00")
        self.assert_money(split["markup_fee_component"], "50.00")
        self.assertEqual(split["pricing_tier"], "tier_b")

    def test_10001_uses_high_production_policy(self):
        split = calculate_financial_split(
            production_cost=Decimal("10001.00"),
            manager_markup=Decimal("1000.00"),
            policy=self.policy,
        )

        self.assert_money(split["production_fee_component"], "800.08")
        self.assert_money(split["markup_fee_component"], "-150.08")
        self.assertEqual(split["pricing_tier"], "tier_c")

    def test_client_price_above_tier_cap_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "Manager markup exceeds the policy cap."):
            calculate_financial_split(
                production_cost=Decimal("1000.00"),
                manager_markup=Decimal("2000.01"),
                policy=self.policy,
            )

    def test_negative_and_zero_values_are_rejected(self):
        with self.assertRaisesMessage(ValidationError, "Production cost must be greater than zero."):
            calculate_financial_split(production_cost=Decimal("-0.01"), manager_markup=Decimal("0.00"), policy=self.policy)
        with self.assertRaisesMessage(ValidationError, "Manager markup cannot be negative."):
            calculate_financial_split(production_cost=Decimal("1000.00"), manager_markup=Decimal("-0.01"), policy=self.policy)
        with self.assertRaisesMessage(ValidationError, "Production cost must be greater than zero."):
            calculate_financial_split(production_cost=Decimal("0.00"), manager_markup=Decimal("0.00"), policy=self.policy)

    def test_decimal_rounding_is_half_up(self):
        split = calculate_financial_split(
            production_cost=Decimal("1000.025"),
            manager_markup=Decimal("80.025"),
            policy=self.policy,
        )

        self.assert_money(split["production_cost"], "1000.03")
        self.assert_money(split["manager_markup"], "80.03")
        self.assert_money(split["markup_fee_component"], "-5.98")

    def test_legacy_fee_fields_do_not_change_canonical_fee(self):
        policy = PlatformFeePolicy(
            printer_fee_rate=Decimal("0.0000"),
            broker_margin_fee_rate=Decimal("0.0000"),
        )
        split = calculate_quote_financials(
            production_cost=Decimal("1000.00"),
            manager_markup=Decimal("750.00"),
            policy=policy,
        )

        self.assert_money(split.printy_fee, "362.50")
        self.assert_money(split.client_total, "1750.00")


class QuoteFinancialSplitSerializerTestCase(SimpleTestCase):
    def setUp(self):
        from types import SimpleNamespace

        self.split = SimpleNamespace(
            id=1,
            quote_id=10,
            policy_used_id=20,
            production_option_id=30,
            production_cost=Decimal("1000.00"),
            manager_markup=Decimal("750.00"),
            production_fee_component=Decimal("50.00"),
            markup_fee_component=Decimal("362.50"),
            printy_fee=Decimal("362.50"),
            shop_payout=Decimal("1050.00"),
            manager_payout=Decimal("337.50"),
            client_total=Decimal("1750.00"),
            currency="KES",
            applied_policy_version="printy-fees-v1",
            pricing_tier="tier_b",
            locked=False,
            broker_client_price=Decimal("1750.00"),
            gross_margin=Decimal("750.00"),
            printer_side_fee=Decimal("50.00"),
            broker_margin_fee=Decimal("362.50"),
            broker_payout=Decimal("337.50"),
            max_allowed_client_price=Decimal("2500.00"),
            applied_markup_multiple=Decimal("0.7500"),
            calculated_at="2026-07-07T00:00:00Z",
        )

    def test_client_serializer_hides_internal_financial_fields(self):
        from quotes.financial_split_actor_serializers import QuoteFinancialSplitClientSerializer

        self.assertEqual(QuoteFinancialSplitClientSerializer(self.split).data, {})

    def test_manager_serializer_returns_authorized_breakdown(self):
        from quotes.financial_split_actor_serializers import QuoteFinancialSplitBrokerSerializer

        payload = QuoteFinancialSplitBrokerSerializer(self.split).data
        for key in (
            "production_cost",
            "manager_markup",
            "production_fee_component",
            "markup_fee_component",
            "printy_fee",
            "manager_payout",
            "shop_payout",
            "client_total",
            "policy_version",
            "pricing_tier",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["printy_fee"], "362.50")
        self.assertEqual(payload["client_total"], "1750.00")
        self.assertEqual(payload["pricing_tier"], "tier_b")

    def test_shop_serializer_hides_manager_income_and_platform_fee(self):
        from quotes.financial_split_actor_serializers import QuoteFinancialSplitShopSerializer

        payload = QuoteFinancialSplitShopSerializer(self.split).data
        self.assertEqual(payload["shop_payout"], "1050.00")
        self.assertNotIn("manager_payout", payload)
        self.assertNotIn("printy_fee", payload)
        self.assertNotIn("client_total", payload)

    def test_admin_serializer_returns_policy_metadata(self):
        from quotes.financial_split_actor_serializers import QuoteFinancialSplitAdminSerializer

        payload = QuoteFinancialSplitAdminSerializer(self.split).data
        self.assertEqual(payload["policy_used"], 20)
        self.assertEqual(payload["policy_version"], "printy-fees-v1")
        self.assertEqual(payload["production_fee_component"], "50.00")
        self.assertEqual(payload["pricing_tier"], "tier_b")


class QuoteFinancialSplitSnapshotTestCase(TestCase):
    def setUp(self):
        PlatformFeePolicy.objects.all().delete()
        self.policy = PlatformFeePolicy.objects.create()
        self.owner = User.objects.create_user(email="split-owner@test.com", password="pass12345")
        self.shop = Shop.objects.create(owner=self.owner, name="Split Shop", slug="split-shop", is_active=True)
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.owner,
            customer_name="Snapshot Client",
            customer_email="snapshot@test.com",
            status=QuoteRequest.QUOTED,
        )
        self.production_option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.owner,
            status=Quote.SENT,
            production_option=self.production_option,
            total=Decimal("1750.00"),
        )

    def test_quote_financial_split_recalculation_is_idempotent(self):
        first = create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            manager_markup=Decimal("750.00"),
            production_option=self.production_option,
            policy=self.policy,
        )
        second = create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            manager_markup=Decimal("750.00"),
            production_option=self.production_option,
            policy=self.policy,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.quote.financial_split.pk, first.pk)
        self.assertEqual(first.printy_fee, Decimal("362.50"))
        self.assertEqual(first.client_total, Decimal("1750.00"))
        self.assertEqual(first.pricing_tier, "tier_b")

    def test_accepted_quote_snapshot_cannot_be_silently_recalculated(self):
        split = create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            manager_markup=Decimal("750.00"),
            production_option=self.production_option,
            policy=self.policy,
            lock=True,
        )
        self.quote.status = Quote.ACCEPTED
        self.quote.save(update_fields=["status", "updated_at"])

        with self.assertRaisesMessage(ValidationError, "Accepted quote financial snapshots are immutable."):
            create_quote_financial_split(
                quote=self.quote,
                production_cost=Decimal("1000.00"),
                manager_markup=Decimal("751.00"),
                production_option=self.production_option,
                policy=self.policy,
            )
        split.refresh_from_db()
        self.assertEqual(split.manager_markup, Decimal("750.00"))