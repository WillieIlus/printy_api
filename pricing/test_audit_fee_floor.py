"""Audit #6: margin floor and PlatformFeePolicy.

Invariants, for every split the platform is willing to produce:

    client_total >= shop_payout + manager_payout
    printy_fee   >= 0
    shop_payout + manager_payout + printy_fee == client_total

Grid: production cost KES 100 .. 500,000 (tiers A, B and C), markups
0% .. 300%, with and without the own-client fee waiver. The test covers both
the manager-sourced path (calculate_financial_split with a manager markup)
and the direct-shop path (a fixed markup rate over the shop's cost).

A quote with too small a markup must be REJECTED. It must not produce a
negative Printy fee (the known failure: production 20,000 + markup 1,000 gave
printy_fee = -950).
"""
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from pricing.models import PlatformFeePolicy
from pricing.services import platform_fee_policy as fee_policy
from pricing.services.platform_fee_policy import calculate_financial_split, get_active_platform_fee_policy

PRODUCTION_COSTS = [
    Decimal(v)
    for v in (
        "100", "250", "640", "999", "1000", "1500", "4200", "9999", "10000",
        "10001", "20000", "37500", "75000", "160000", "500000",
    )
]
MARKUP_PERCENTS = [
    Decimal(v)
    for v in (
        "0", "1", "3", "4.99", "5", "8", "9", "9.5", "10", "12", "12.5", "13",
        "15", "20", "35", "50", "75", "100", "150", "199", "200", "250", "300",
    )
]


def _assert_invariants(test, split, *, label):
    test.assertGreaterEqual(split.printy_fee, Decimal("0"), f"{label}: negative printy_fee {split.printy_fee}")
    test.assertGreaterEqual(
        split.client_total,
        split.shop_payout + split.manager_payout,
        f"{label}: client_total {split.client_total} < shop {split.shop_payout} + manager {split.manager_payout}",
    )
    test.assertEqual(
        split.shop_payout + split.manager_payout + split.printy_fee,
        split.client_total,
        f"{label}: parts do not sum to client_total",
    )
    test.assertGreaterEqual(split.manager_payout, Decimal("0"), f"{label}: negative manager payout")
    test.assertGreaterEqual(split.shop_payout, split.production_cost, f"{label}: shop paid below production cost")


class FeeFloorInvariantTests(TestCase):
    def setUp(self):
        PlatformFeePolicy.objects.update(is_active=False)
        self.policy = PlatformFeePolicy.objects.create(name="Audit #6 policy", is_active=True)

    def test_known_negative_fee_case_is_rejected(self):
        # production_cost=20000, markup=1000 used to give printy_fee=-950.
        with self.assertRaises(ValidationError) as ctx:
            calculate_financial_split(production_cost=Decimal("20000"), manager_markup=Decimal("1000"), policy=self.policy)
        self.assertIn("minimum", str(ctx.exception).lower())

    def test_known_case_is_also_rejected_when_the_fee_is_waived(self):
        # Waived: 21,000 charged, 21,600 owed to the printer. Still a loss.
        with self.assertRaises(ValidationError):
            calculate_financial_split(
                production_cost=Decimal("20000"), manager_markup=Decimal("1000"), policy=self.policy, fee_waived=True
            )

    def test_grid_every_accepted_split_satisfies_the_invariants(self):
        accepted = rejected = 0
        for fee_waived in (False, True):
            for production_cost in PRODUCTION_COSTS:
                for percent in MARKUP_PERCENTS:
                    markup = (production_cost * percent / Decimal("100")).quantize(Decimal("1"))
                    label = f"cost={production_cost} markup={percent}% ({markup}) waived={fee_waived}"
                    try:
                        split = calculate_financial_split(
                            production_cost=production_cost,
                            manager_markup=markup,
                            policy=self.policy,
                            fee_waived=fee_waived,
                        )
                    except ValidationError:
                        rejected += 1
                        continue
                    accepted += 1
                    _assert_invariants(self, split, label=label)
                    if fee_waived:
                        self.assertEqual(split.printy_fee, Decimal("0"), f"{label}: own client must pay no Printy fee")
        self.assertGreater(accepted, 300)
        self.assertGreater(rejected, 0)

    def test_grid_markups_inside_the_band_are_accepted(self):
        """The floor must not reject reasonable quotes (13%..cap is always fine)."""
        for fee_waived in (False, True):
            for production_cost in PRODUCTION_COSTS:
                cap_percent = Decimal("100") if production_cost > Decimal("10000") else Decimal("150")
                for percent in (Decimal("13"), Decimal("20"), Decimal("50"), Decimal("75"), cap_percent):
                    markup = (production_cost * percent / Decimal("100")).quantize(Decimal("1"))
                    split = calculate_financial_split(
                        production_cost=production_cost, manager_markup=markup, policy=self.policy, fee_waived=fee_waived
                    )
                    _assert_invariants(self, split, label=f"cost={production_cost} {percent}% waived={fee_waived}")

    def test_floor_is_exact_one_shilling_below_is_rejected(self):
        for fee_waived in (False, True):
            for production_cost in PRODUCTION_COSTS:
                floor = fee_policy.minimum_manager_markup(production_cost=production_cost, policy=self.policy, fee_waived=fee_waived)
                split = calculate_financial_split(
                    production_cost=production_cost, manager_markup=floor, policy=self.policy, fee_waived=fee_waived
                )
                _assert_invariants(self, split, label=f"floor cost={production_cost} waived={fee_waived}")
                self.assertGreaterEqual(split.client_total, (production_cost * self.policy.min_markup_multiple).quantize(Decimal("1")))
                if floor >= 1:
                    with self.assertRaises(ValidationError, msg=f"cost={production_cost} markup={floor - 1}"):
                        calculate_financial_split(
                            production_cost=production_cost,
                            manager_markup=floor - 1,
                            policy=self.policy,
                            fee_waived=fee_waived,
                        )

    def test_min_markup_multiple_applies_in_tier_a(self):
        # Tier A has no printer uplift, so only MIN_MARKUP_MULTIPLE (1.05) bites.
        with self.assertRaises(ValidationError):
            calculate_financial_split(production_cost=Decimal("500"), manager_markup=Decimal("0"), policy=self.policy)
        with self.assertRaises(ValidationError):
            calculate_financial_split(production_cost=Decimal("500"), manager_markup=Decimal("24"), policy=self.policy)
        split = calculate_financial_split(production_cost=Decimal("500"), manager_markup=Decimal("25"), policy=self.policy)
        _assert_invariants(self, split, label="tier A at 1.05")

    def test_broker_client_price_entry_point_enforces_the_same_floor(self):
        with self.assertRaises(ValidationError):
            calculate_financial_split(
                production_cost=Decimal("20000"), broker_client_price=Decimal("21000"), policy=self.policy
            )

    def test_direct_shop_path_satisfies_the_invariants(self):
        from services.production_matching import get_direct_shop_standard_markup_rate

        rate = get_direct_shop_standard_markup_rate()
        for production_cost in PRODUCTION_COSTS:
            broker_client_price = (production_cost * (Decimal("1") + rate)).quantize(Decimal("0.01"))
            split = calculate_financial_split(
                production_cost=production_cost, broker_client_price=broker_client_price, policy=self.policy
            )
            _assert_invariants(self, split, label=f"direct shop cost={production_cost}")

    def test_public_estimate_path_satisfies_the_invariants(self):
        from services.pricing.marketplace_pricing import calculate_client_price

        for production_cost in PRODUCTION_COSTS:
            result = calculate_client_price(production_cost)
            self.assertGreaterEqual(result["printy_fee"], Decimal("0"), f"public estimate cost={production_cost}")
            self.assertGreaterEqual(
                result["client_price"], result["shop_payout"] + result["broker_payout"], f"public estimate cost={production_cost}"
            )


class PlatformFeePolicyIsHonouredTests(TestCase):
    """Admin edits to the active policy must change the split at runtime."""

    def setUp(self):
        PlatformFeePolicy.objects.update(is_active=False)

    def _policy(self, **overrides):
        PlatformFeePolicy.objects.update(is_active=False)
        return PlatformFeePolicy.objects.create(name="Edited policy", is_active=True, **overrides)

    def test_defaults_reproduce_the_current_tiers(self):
        policy = self._policy()
        a = calculate_financial_split(production_cost=Decimal("999"), manager_markup=Decimal("600"), policy=policy)
        b = calculate_financial_split(production_cost=Decimal("5000"), manager_markup=Decimal("2000"), policy=policy)
        c = calculate_financial_split(production_cost=Decimal("20000"), manager_markup=Decimal("8000"), policy=policy)
        self.assertEqual((a.pricing_tier, a.shop_payout, a.manager_payout, a.printy_fee), ("tier_a", Decimal("999"), Decimal("360"), Decimal("240")))
        self.assertEqual((b.pricing_tier, b.shop_payout, b.manager_payout, b.printy_fee), ("tier_b", Decimal("5250"), Decimal("900"), Decimal("850")))
        self.assertEqual((c.pricing_tier, c.shop_payout, c.manager_payout, c.printy_fee), ("tier_c", Decimal("21600"), Decimal("2800"), Decimal("3600")))

    def test_threshold_edits_move_the_tier_boundary(self):
        policy = self._policy(under_production_threshold=Decimal("2000.00"), high_production_threshold=Decimal("30000.00"))
        self.assertEqual(
            calculate_financial_split(production_cost=Decimal("1500"), manager_markup=Decimal("500"), policy=policy).pricing_tier,
            "tier_a",
        )
        self.assertEqual(
            calculate_financial_split(production_cost=Decimal("20000"), manager_markup=Decimal("8000"), policy=policy).pricing_tier,
            "tier_b",
        )

    def test_rate_edits_change_payouts(self):
        policy = self._policy(
            tier_b_shop_floor_multiple=Decimal("1.10"),
            tier_b_manager_commission_cap_rate=Decimal("0.50"),
        )
        split = calculate_financial_split(production_cost=Decimal("5000"), manager_markup=Decimal("2000"), policy=policy)
        self.assertEqual(split.shop_payout, Decimal("5500"))
        self.assertEqual(split.manager_payout, Decimal("1000"))
        self.assertEqual(split.printy_fee, Decimal("500"))

    def test_cap_edits_change_the_maximum_client_price(self):
        policy = self._policy(tier_c_max_client_price_multiple=Decimal("1.50"))
        with self.assertRaises(ValidationError):
            calculate_financial_split(production_cost=Decimal("20000"), manager_markup=Decimal("12000"), policy=policy)
        split = calculate_financial_split(production_cost=Decimal("20000"), manager_markup=Decimal("10000"), policy=policy)
        self.assertEqual(split.max_allowed_client_price, Decimal("30000"))

    def test_min_markup_multiple_edit_moves_the_floor(self):
        policy = self._policy(min_markup_multiple=Decimal("1.20"))
        with self.assertRaises(ValidationError):
            calculate_financial_split(production_cost=Decimal("500"), manager_markup=Decimal("99"), policy=policy)
        calculate_financial_split(production_cost=Decimal("500"), manager_markup=Decimal("100"), policy=policy)

    def test_runtime_reads_the_active_policy_when_none_is_passed(self):
        self._policy(tier_b_shop_floor_multiple=Decimal("1.10"))
        split = calculate_financial_split(production_cost=Decimal("5000"), manager_markup=Decimal("2000"))
        self.assertEqual(split.shop_payout, Decimal("5500"))
        self.assertEqual(get_active_platform_fee_policy().tier_b_shop_floor_multiple, Decimal("1.10"))

    def test_policy_validation_rejects_a_floor_that_cannot_be_met(self):
        policy = PlatformFeePolicy(name="Bad", is_active=False, tier_c_manager_commission_cap_rate=Decimal("1.00"))
        with self.assertRaises(ValidationError):
            policy.full_clean()
        policy = PlatformFeePolicy(name="Bad", is_active=False, min_markup_multiple=Decimal("0.90"))
        with self.assertRaises(ValidationError):
            policy.full_clean()


class ManagerPrepareAndSendRespectTheFloorTests(TestCase):
    """The manager's prepare/send flow reports a below-floor markup as a 400.

    It must not return a 500 or store a negative split. When the markup is
    accepted, the total, markup and Printy fee in the response match the
    stored split.
    """

    def setUp(self):
        from rest_framework.test import APIClient

        from accounts.models import User, UserProfile
        from quotes.choices import QuoteOfferStatus, QuoteStatus
        from quotes.models import Quote, QuoteRequest
        from shops.models import Shop

        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(name="Audit #6 policy", is_active=True)
        self.api = APIClient()
        self.partner = User.objects.create_user(
            email="floor-partner@test.com", password="x", role="broker", partner_profile_enabled=True
        )
        UserProfile.objects.update_or_create(
            user=self.partner, defaults={"manager_verified": True, "broker_profile_active": True}
        )
        self.end_client = User.objects.create_user(email="floor-client@test.com", password="x", role="client")
        owner = User.objects.create_user(email="floor-shop@test.com", password="x", role="shop_owner")
        shop = Shop.objects.create(owner=owner, name="Floor Shop", slug="floor-shop", is_active=True)
        self.quote_request = QuoteRequest.objects.create(
            shop=shop,
            created_by=self.partner,
            on_behalf_of=self.end_client,
            customer_name="Floor Client",
            customer_email=self.end_client.email,
            status=QuoteStatus.DRAFT,
            request_snapshot={"source": "partner_quote_builder"},
        )
        # No production_cost_inputs, so send-to-client uses the manager's
        # explicit margin on top of the 5,000 production estimate (tier B).
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=shop,
            created_by=self.partner,
            status=QuoteOfferStatus.PENDING,
            total=Decimal("5000.00"),
            response_snapshot={},
        )
        self.api.force_authenticate(user=self.partner)

    def _send(self, margin):
        return self.api.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/send-to-client/",
            {"broker_margin_type": "fixed", "broker_margin_value": str(margin)},
            format="json",
        )

    def test_markup_between_min_multiple_and_tier_floor_is_a_400_not_a_500(self):
        from quotes.models import QuoteFinancialSplit

        # 300 on 5,000 is 6%: above MIN_MARKUP_MULTIPLE (5%) but below the
        # tier B floor (5,000 x 0.05 / 0.55 = 455), so the fee would be negative.
        response = self._send(300)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("minimum", response.content.decode().lower())
        self.assertFalse(QuoteFinancialSplit.objects.filter(quote=self.quote).exists())

    def test_accepted_markup_shows_numbers_that_match_the_stored_split(self):
        self.quote_request.on_behalf_of = None
        self.quote_request.save(update_fields=["on_behalf_of"])
        with self.captureOnCommitCallbacks(execute=True):
            response = self._send(1000)
        self.assertEqual(response.status_code, 200, response.content)
        self.quote.refresh_from_db()
        split = self.quote.financial_split
        self.assertGreaterEqual(split.printy_fee, Decimal("0"))
        self.assertEqual(split.shop_payout + split.manager_payout + split.printy_fee, split.client_total)
        self.assertEqual(self.quote.total, split.client_total)
        pricing = response.json().get("pricing") or {}
        text = str(pricing)
        self.assertIn(str(split.client_total).split(".")[0], text)


class PartnerPreviewFloorTests(TestCase):
    def setUp(self):
        from accounts.models import User
        from shops.models import Shop

        PlatformFeePolicy.objects.update(is_active=False)
        self.policy = PlatformFeePolicy.objects.create(name="Audit #6 policy", is_active=True)
        owner = User.objects.create_user(email="preview-floor-shop@test.com", password="x", role="shop_owner")
        self.shop = Shop.objects.create(owner=owner, name="Preview Floor Shop", slug="preview-floor-shop", is_active=True)

    def _preview(self, production, markup, fee_waived=False):
        from quotes.partner_services import build_partner_quote_preview

        snapshot = {"totals": {"subtotal": str(production)}}
        return build_partner_quote_preview(
            pricing_snapshot=snapshot, shop=self.shop, partner_markup=Decimal(markup), fee_waived=fee_waived
        )

    def test_suggested_minimum_is_the_policy_floor_not_bare_production(self):
        preview = self._preview("5000", "1000")
        self.assertEqual(Decimal(preview["production_estimate"]), Decimal("5000.00"))
        floor = fee_policy.minimum_manager_markup(production_cost=Decimal("5000"), policy=self.policy)
        self.assertEqual(Decimal(preview["suggested_selling_range"]["min"]), Decimal("5000") + floor)
        self.assertEqual(
            Decimal(preview["client_price"]), Decimal(preview["production_estimate"]) + Decimal("1000")
        )
        self.assertGreaterEqual(Decimal(preview["printy_fee"]), Decimal("0"))

    def test_preview_below_the_floor_raises_a_value_error_for_the_view_to_report(self):
        with self.assertRaises(ValueError):
            self._preview("20000", "1000")

    def test_prepare_preview_numbers_match_the_split_that_send_stores(self):
        preview = self._preview("5000", "1000")
        split = fee_policy.calculate_financial_split(
            production_cost=Decimal("5000"), broker_client_price=Decimal("6000")
        )
        self.assertEqual(Decimal(preview["client_price"]), split["client_total"])
        self.assertEqual(Decimal(preview["gross_margin"]), split["manager_markup"])
        self.assertEqual(Decimal(preview["printy_fee"]), split["printy_fee"])
        self.assertEqual(Decimal(preview["broker_payout"]), split["manager_payout"])
        self.assertEqual(
            Decimal(preview["printer_payout"]) + Decimal(preview["broker_payout"]) + Decimal(preview["printy_fee"]),
            Decimal(preview["client_price"]),
        )

    def test_own_client_preview_has_no_printy_fee_and_a_lower_floor(self):
        # 300 on 5,000 is below the charged tier B floor (455) but above the
        # waived floor (5% = 250): the manager's own client pays no Printy fee.
        with self.assertRaises(ValueError):
            self._preview("5000", "300")
        preview = self._preview("5000", "300", fee_waived=True)
        self.assertTrue(preview["fee_waived"])
        self.assertEqual(Decimal(preview["printy_fee"]), Decimal("0"))
        self.assertEqual(Decimal(preview["client_price"]), Decimal("5300.00"))
        self.assertEqual(Decimal(preview["suggested_selling_range"]["min"]), Decimal("5250.00"))
        self.assertEqual(
            Decimal(preview["printer_payout"]) + Decimal(preview["broker_payout"]), Decimal(preview["client_price"])
        )


class StoredSplitAuditCommandTests(TestCase):
    """report_financial_split_anomalies lists bad rows and never modifies them."""

    def setUp(self):
        from accounts.models import User
        from quotes.models import Quote, QuoteFinancialSplit, QuoteRequest
        from shops.models import Shop

        PlatformFeePolicy.objects.update(is_active=False)
        self.policy = PlatformFeePolicy.objects.create(name="Audit #6 policy", is_active=True)
        owner = User.objects.create_user(email="audit-split-shop@test.com", password="x", role="shop_owner")
        shop = Shop.objects.create(owner=owner, name="Audit Split Shop", slug="audit-split-shop", is_active=True)
        qr = QuoteRequest.objects.create(shop=shop, customer_name="X")

        def _split(**values):
            quote = Quote.objects.create(quote_request=qr, shop=shop, total=values["client_total"])
            return QuoteFinancialSplit.objects.create(
                quote=quote,
                policy_used=self.policy,
                broker_client_price=values["client_total"],
                gross_margin=values["manager_markup"],
                printer_side_fee=values["shop_payout"] - values["production_cost"],
                broker_margin_fee=values["printy_fee"],
                broker_payout=values["manager_payout"],
                max_allowed_client_price=values["production_cost"] * 2,
                applied_markup_multiple=Decimal("0.05"),
                locked=True,
                **values,
            )

        # The pre-fix result for production 20,000 + markup 1,000.
        self.bad = _split(
            production_cost=Decimal("20000"), manager_markup=Decimal("1000"), shop_payout=Decimal("21600"),
            manager_payout=Decimal("350"), printy_fee=Decimal("-950"), client_total=Decimal("21000"),
            pricing_tier="tier_c",
        )
        self.good = _split(
            production_cost=Decimal("5000"), manager_markup=Decimal("2000"), shop_payout=Decimal("5250"),
            manager_payout=Decimal("900"), printy_fee=Decimal("850"), client_total=Decimal("7000"),
            pricing_tier="tier_b",
        )

    def test_reports_the_negative_split_and_leaves_it_untouched(self):
        from io import StringIO

        from django.core.management import CommandError, call_command

        out = StringIO()
        with self.assertRaises(CommandError):
            call_command("report_financial_split_anomalies", "--fail-if-any", stdout=out)
        text = out.getvalue()
        self.assertIn(f"split #{self.bad.id}", text)
        self.assertIn("negative_printy_fee", text)
        self.assertIn("over_allocated", text)
        self.assertNotIn(f"split #{self.good.id} ", text)
        self.bad.refresh_from_db()
        self.assertEqual(self.bad.printy_fee, Decimal("-950"))
        self.assertEqual(self.bad.client_total, Decimal("21000"))
