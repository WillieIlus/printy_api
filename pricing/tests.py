from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from accounts.models import User
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import calculate_financial_split, create_quote_financial_split
from quotes.models import ProductionOption, Quote, QuoteRequest
from shops.models import Shop


class PlatformFeePolicyServiceTestCase(SimpleTestCase):
    def setUp(self):
        self.policy = PlatformFeePolicy(
            printer_fee_rate=Decimal("0.1000"),
            broker_margin_fee_rate=Decimal("0.2000"),
        )

    def test_calculate_financial_split_uses_platform_fee_policy_formula(self):
        split = calculate_financial_split(
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            policy=self.policy,
        )

        self.assertEqual(split["production_cost"], Decimal("1000.00"))
        self.assertEqual(split["broker_client_price"], Decimal("1500.00"))
        self.assertEqual(split["gross_margin"], Decimal("500.00"))
        self.assertEqual(split["printer_side_fee"], Decimal("100.00"))
        self.assertEqual(split["broker_margin_fee"], Decimal("100.00"))
        self.assertEqual(split["printy_fee"], Decimal("200.00"))
        self.assertEqual(split["shop_payout"], Decimal("1000.00"))
        self.assertEqual(split["broker_payout"], Decimal("300.00"))
        self.assertEqual(split["client_total"], Decimal("1500.00"))

    def test_add_platform_fee_on_top_only_changes_client_total(self):
        self.policy.add_platform_fee_on_top = True

        split = calculate_financial_split(
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            policy=self.policy,
        )

        self.assertEqual(split["printy_fee"], Decimal("200.00"))
        self.assertEqual(split["broker_payout"], Decimal("300.00"))
        self.assertEqual(split["client_total"], Decimal("1700.00"))

    def test_cap_rejects_price_above_policy_max_client_price(self):
        with self.assertRaisesMessage(ValidationError, "Broker client price exceeds the policy cap."):
            calculate_financial_split(
                production_cost=Decimal("10000.00"),
                broker_client_price=Decimal("25000.00"),
                policy=self.policy,
            )

    def test_small_medium_bulk_markup_caps_use_policy_tiers(self):
        policy = PlatformFeePolicy(
            printer_fee_rate=Decimal("0.0000"),
            broker_margin_fee_rate=Decimal("0.0000"),
        )

        accepted = (
            (Decimal("1000.00"), Decimal("4000.00")),
            (Decimal("2000.00"), Decimal("6000.00")),
            (Decimal("10000.00"), Decimal("20000.00")),
        )
        rejected = (
            (Decimal("1000.00"), Decimal("4000.01")),
            (Decimal("2000.00"), Decimal("6000.01")),
            (Decimal("10000.00"), Decimal("20000.01")),
        )

        for production_cost, client_price in accepted:
            with self.subTest(production_cost=production_cost, client_price=client_price, accepted=True):
                split = calculate_financial_split(
                    production_cost=production_cost,
                    broker_client_price=client_price,
                    policy=policy,
                )
                self.assertEqual(split["client_total"], client_price)

        for production_cost, client_price in rejected:
            with self.subTest(production_cost=production_cost, client_price=client_price, accepted=False):
                with self.assertRaisesMessage(ValidationError, "Broker client price exceeds the policy cap."):
                    calculate_financial_split(
                        production_cost=production_cost,
                        broker_client_price=client_price,
                        policy=policy,
                    )

    def test_no_active_hard_coded_thirty_percent_fee_logic(self):
        root = Path(__file__).resolve().parents[1]
        offenders = []
        ignored_parts = {"env", "migrations", "__pycache__"}
        ignored_names = {"tests.py"}
        for path in root.rglob("*.py"):
            if ignored_parts.intersection(path.parts) or path.name in ignored_names or path.name.startswith("test_"):
                continue
            text = path.read_text(encoding="utf-8")
            for token in ('0.30', '30%'):
                if token in text:
                    offenders.append(f"{path.relative_to(root)} contains {token}")

        self.assertEqual(offenders, [])


class QuoteFinancialSplitSnapshotTestCase(TestCase):
    def setUp(self):
        PlatformFeePolicy.objects.all().delete()
        self.policy = PlatformFeePolicy.objects.create(
            printer_fee_rate=Decimal("0.1000"),
            broker_margin_fee_rate=Decimal("0.2000"),
        )
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
            total=Decimal("1500.00"),
        )

    def test_quote_financial_split_creation_is_idempotent(self):
        first = create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            production_option=self.production_option,
            policy=self.policy,
        )
        second = create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            production_option=self.production_option,
            policy=self.policy,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.quote.financial_split.pk, first.pk)
        self.assertEqual(first.policy_used, self.policy)
        self.assertEqual(first.calculated_at, second.calculated_at)
