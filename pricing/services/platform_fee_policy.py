from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterator

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from common.money import MONEY_QUANT, RATE_QUANT, money, rate, to_decimal, whole_kes
from pricing.models import PlatformFeePolicy


MIN_MARKUP_MULTIPLE = Decimal("1.05")
TIER_A_MAX_PRODUCTION_COST = Decimal("1000.00")
TIER_B_MAX_PRODUCTION_COST = Decimal("10000.00")

# Re-exported for the many modules that already import `money` from here. The
# definitions live in common.money so there is exactly one rounding rule.
__all__ = [
    "MONEY_QUANT",
    "RATE_QUANT",
    "MIN_MARKUP_MULTIPLE",
    "TIER_A_MAX_PRODUCTION_COST",
    "TIER_B_MAX_PRODUCTION_COST",
    "money",
    "rate",
    "whole_kes",
    "QuoteFinancialResult",
    "get_active_platform_fee_policy",
    "calculate_quote_financials",
    "calculate_financial_split",
    "create_quote_financial_split",
    "ensure_quote_financial_split",
]


@dataclass(frozen=True)
class QuoteFinancialResult:
    policy: PlatformFeePolicy
    production_cost: Decimal
    manager_markup: Decimal
    production_fee_component: Decimal
    markup_fee_component: Decimal
    printy_fee: Decimal
    shop_payout: Decimal
    manager_payout: Decimal
    client_total: Decimal
    currency: str
    pricing_tier: str
    applied_policy_version: str
    max_allowed_client_price: Decimal
    applied_markup_multiple: Decimal

    @property
    def broker_client_price(self) -> Decimal:
        # Both inputs are whole KES, so this sum is exact.
        return self.production_cost + self.manager_markup

    @property
    def gross_margin(self) -> Decimal:
        return self.manager_markup

    @property
    def printer_side_fee(self) -> Decimal:
        return self.production_fee_component

    @property
    def broker_margin_fee(self) -> Decimal:
        return self.markup_fee_component

    @property
    def broker_payout(self) -> Decimal:
        return self.manager_payout

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "production_cost": self.production_cost,
            "manager_markup": self.manager_markup,
            "production_fee_component": self.production_fee_component,
            "markup_fee_component": self.markup_fee_component,
            "printy_fee": self.printy_fee,
            "shop_payout": self.shop_payout,
            "manager_payout": self.manager_payout,
            "client_total": self.client_total,
            "currency": self.currency,
            "pricing_tier": self.pricing_tier,
            "applied_policy_version": self.applied_policy_version,
            "broker_client_price": self.broker_client_price,
            "gross_margin": self.gross_margin,
            "printer_side_fee": self.printer_side_fee,
            "broker_margin_fee": self.broker_margin_fee,
            "broker_payout": self.broker_payout,
            "max_allowed_client_price": self.max_allowed_client_price,
            "applied_markup_multiple": self.applied_markup_multiple,
        }

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]

    def get(self, key: str, default=None) -> Any:
        return self.as_dict().get(key, default)

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def keys(self):
        return self.as_dict().keys()

    def items(self):
        return self.as_dict().items()

    def values(self):
        return self.as_dict().values()


def get_active_platform_fee_policy() -> PlatformFeePolicy:
    policy = (
        PlatformFeePolicy.objects.filter(is_active=True)
        .order_by("-effective_from", "-updated_at", "-created_at", "-id")
        .first()
    )
    if policy:
        return policy
    return PlatformFeePolicy.objects.create(effective_from=timezone.now())


def _policy_value(policy: PlatformFeePolicy, field: str, default: str) -> Decimal:
    return Decimal(str(getattr(policy, field, Decimal(default))))


def calculate_quote_financials(*, production_cost, manager_markup, policy: PlatformFeePolicy) -> QuoteFinancialResult:
    """The single rounding point for the client-facing money chain.

    M-Pesa STK Push only accepts whole shillings, so every amount that reaches
    the client — production cost, markup, shop payout, manager payout, the
    Printy fee and the client total — is rounded to whole KES here, once, with
    ROUND_HALF_UP, and stored as that integer. Downstream code (quote display,
    preview payloads, STK, the callback mismatch guard) then propagates the
    same integer without rounding again.

    ``printy_fee`` is computed as the residual rather than independently
    rounded, so ``shop_payout + manager_payout + printy_fee`` always equals
    ``client_total`` exactly. Rounding the three independently is the classic
    rounding-split bug where the parts are off by a shilling.
    """
    production_cost = to_decimal(production_cost)
    manager_markup = to_decimal(manager_markup)
    raw_production_cost = production_cost
    raw_manager_markup = manager_markup

    # Validate the raw inputs BEFORE rounding. A negative that rounds to zero
    # (e.g. -0.01) would otherwise slip past the guard below and become a
    # legitimate-looking 0.
    if production_cost <= 0:
        raise ValidationError("Production cost must be greater than zero.")
    if manager_markup < 0:
        raise ValidationError("Manager markup cannot be negative.")

    production_cost = whole_kes(production_cost)
    manager_markup = whole_kes(manager_markup)

    # A cost under one shilling rounds to 0, which M-Pesa cannot charge and
    # which would make the markup multiple undefined.
    if production_cost <= 0:
        raise ValidationError("Production cost must be at least KES 1.")

    if production_cost < TIER_A_MAX_PRODUCTION_COST:
        pricing_tier = "tier_a"
        shop_floor_multiple = Decimal("1.00")
        manager_commission_cap_rate = Decimal("0.60")
        max_client_price_multiple = Decimal("3.00")
    elif production_cost <= TIER_B_MAX_PRODUCTION_COST:
        pricing_tier = "tier_b"
        shop_floor_multiple = Decimal("1.05")
        manager_commission_cap_rate = Decimal("0.45")
        max_client_price_multiple = Decimal("2.50")
    else:
        pricing_tier = "tier_c"
        shop_floor_multiple = Decimal("1.08")
        manager_commission_cap_rate = Decimal("0.35")
        max_client_price_multiple = Decimal("2.00")

    # Both inputs are whole KES now, so the sum is exact and needs no rounding.
    broker_client_price = production_cost + manager_markup
    max_allowed_client_price = whole_kes(production_cost * max_client_price_multiple)
    # Guard the cap on the raw input *and* on the rounded price, so rounding can
    # never let a quote through that policy would have rejected.
    if broker_client_price > max_allowed_client_price or (
        raw_production_cost + raw_manager_markup
    ) > (raw_production_cost * max_client_price_multiple):
        raise ValidationError("Manager markup exceeds the policy cap.")

    gross_margin = whole_kes(broker_client_price - production_cost)
    shop_payout = whole_kes(production_cost * shop_floor_multiple)
    production_fee_component = whole_kes(shop_payout - production_cost)
    manager_payout = whole_kes(min(manager_markup, whole_kes(gross_margin * manager_commission_cap_rate)))
    # Residual: guarantees the three payout components sum to client_total.
    printy_fee = whole_kes(broker_client_price - shop_payout - manager_payout)
    markup_fee_component = printy_fee
    client_total = broker_client_price
    applied_markup_multiple = rate(manager_markup / production_cost) if production_cost else Decimal("0.0000")

    return QuoteFinancialResult(
        policy=policy,
        production_cost=production_cost,
        manager_markup=manager_markup,
        production_fee_component=production_fee_component,
        markup_fee_component=markup_fee_component,
        printy_fee=printy_fee,
        shop_payout=shop_payout,
        manager_payout=manager_payout,
        client_total=client_total,
        currency=getattr(policy, "currency", "KES") or "KES",
        pricing_tier=pricing_tier,
        applied_policy_version=getattr(policy, "policy_version", "printy-fees-v1") or "printy-fees-v1",
        max_allowed_client_price=max_allowed_client_price,
        applied_markup_multiple=applied_markup_multiple,
    )


def calculate_financial_split(*, production_cost, manager_markup=None, broker_client_price=None, policy=None) -> QuoteFinancialResult:
    policy = policy or get_active_platform_fee_policy()
    if manager_markup is None:
        if broker_client_price is None:
            raise ValidationError("Manager markup is required.")
        # Derive the markup from the raw inputs and let calculate_quote_financials
        # do the rounding, so its cap guard sees the cent-level value the caller
        # actually asked for. Rounding here would silently accept an over-cap
        # price such as 2500.01 against a 2500 cap.
        manager_markup = to_decimal(broker_client_price) - to_decimal(production_cost)
    return calculate_quote_financials(
        production_cost=production_cost,
        manager_markup=manager_markup,
        policy=policy,
    )


def _split_matches_result(split, result: QuoteFinancialResult) -> bool:
    fields = (
        "production_cost",
        "manager_markup",
        "production_fee_component",
        "markup_fee_component",
        "printy_fee",
        "shop_payout",
        "manager_payout",
        "client_total",
        "currency",
        "pricing_tier",
        "applied_policy_version",
    )
    return all(getattr(split, field) == getattr(result, field) for field in fields)


@transaction.atomic
def create_quote_financial_split(
    *,
    quote,
    production_cost,
    manager_markup=None,
    broker_client_price=None,
    production_option=None,
    policy=None,
    lock: bool | None = None,
):
    from quotes.models import QuoteFinancialSplit

    result = calculate_financial_split(
        production_cost=production_cost,
        manager_markup=manager_markup,
        broker_client_price=broker_client_price,
        policy=policy,
    )
    existing = QuoteFinancialSplit.objects.select_for_update().filter(quote=quote).first()
    should_lock = bool(lock) or getattr(quote, "status", "") == "accepted" or getattr(quote, "accepted_at", None) is not None
    if existing is not None:
        if existing.locked or getattr(quote, "status", "") == "accepted":
            if _split_matches_result(existing, result):
                return existing
            raise ValidationError("Accepted quote financial snapshots are immutable.")
        for field, value in {
            "policy_used": result.policy,
            "production_option": production_option,
            "production_cost": result.production_cost,
            "manager_markup": result.manager_markup,
            "production_fee_component": result.production_fee_component,
            "markup_fee_component": result.markup_fee_component,
            "broker_client_price": result.broker_client_price,
            "gross_margin": result.gross_margin,
            "printer_side_fee": result.printer_side_fee,
            "broker_margin_fee": result.broker_margin_fee,
            "printy_fee": result.printy_fee,
            "shop_payout": result.shop_payout,
            "manager_payout": result.manager_payout,
            "broker_payout": result.broker_payout,
            "client_total": result.client_total,
            "currency": result.currency,
            "pricing_tier": result.pricing_tier,
            "applied_policy_version": result.applied_policy_version,
            "max_allowed_client_price": result.max_allowed_client_price,
            "applied_markup_multiple": result.applied_markup_multiple,
            "locked": should_lock,
        }.items():
            setattr(existing, field, value)
        existing.save()
        return existing
    return QuoteFinancialSplit.objects.create(
        quote=quote,
        policy_used=result.policy,
        production_option=production_option,
        production_cost=result.production_cost,
        manager_markup=result.manager_markup,
        production_fee_component=result.production_fee_component,
        markup_fee_component=result.markup_fee_component,
        broker_client_price=result.broker_client_price,
        gross_margin=result.gross_margin,
        printer_side_fee=result.printer_side_fee,
        broker_margin_fee=result.broker_margin_fee,
        printy_fee=result.printy_fee,
        shop_payout=result.shop_payout,
        manager_payout=result.manager_payout,
        broker_payout=result.broker_payout,
        client_total=result.client_total,
        currency=result.currency,
        pricing_tier=result.pricing_tier,
        applied_policy_version=result.applied_policy_version,
        max_allowed_client_price=result.max_allowed_client_price,
        applied_markup_multiple=result.applied_markup_multiple,
        locked=should_lock,
    )


def ensure_quote_financial_split(*, quote, policy=None):
    existing = getattr(quote, "financial_split", None)
    if existing:
        if getattr(quote, "status", "") == "accepted" and not existing.locked:
            existing.locked = True
            existing.save(update_fields=["locked"])
        return existing
    production_option = getattr(quote, "production_option", None)
    if production_option is None or getattr(production_option, "production_cost", None) is None:
        raise ValidationError("Quote needs a selected production option or an explicit financial split before acceptance.")
    production_cost = production_option.production_cost
    broker_client_price = getattr(quote, "total", None)
    if broker_client_price is None:
        raise ValidationError("Quote needs a client total before acceptance.")
    return create_quote_financial_split(
        quote=quote,
        production_cost=production_cost,
        broker_client_price=broker_client_price,
        production_option=production_option,
        policy=policy,
        lock=True,
    )
