from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterator

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from pricing.models import PlatformFeePolicy


MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.0001")
MIN_MARKUP_MULTIPLE = Decimal("1.05")
TIER_A_MAX_PRODUCTION_COST = Decimal("1000.00")
TIER_B_MAX_PRODUCTION_COST = Decimal("10000.00")


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def rate(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)


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
        return money(self.production_cost + self.manager_markup)

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
    production_cost = money(production_cost)
    manager_markup = money(manager_markup)

    if production_cost <= 0:
        raise ValidationError("Production cost must be greater than zero.")
    if manager_markup < 0:
        raise ValidationError("Manager markup cannot be negative.")

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

    broker_client_price = money(production_cost + manager_markup)
    max_allowed_client_price = money(production_cost * max_client_price_multiple)
    if broker_client_price > max_allowed_client_price:
        raise ValidationError("Manager markup exceeds the policy cap.")

    gross_margin = money(broker_client_price - production_cost)
    shop_payout = money(production_cost * shop_floor_multiple)
    production_fee_component = money(shop_payout - production_cost)
    manager_payout = money(min(manager_markup, money(gross_margin * manager_commission_cap_rate)))
    printy_fee = money(gross_margin - production_fee_component - manager_payout)
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
    production_cost = money(production_cost)
    if manager_markup is None:
        if broker_client_price is None:
            raise ValidationError("Manager markup is required.")
        manager_markup = money(broker_client_price) - production_cost
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
    production_cost = getattr(production_option, "production_cost", None) or getattr(quote, "total", None)
    broker_client_price = getattr(quote, "total", None)
    if production_cost is None or broker_client_price is None:
        raise ValidationError("Quote needs production cost and manager markup before acceptance.")
    return create_quote_financial_split(
        quote=quote,
        production_cost=production_cost,
        broker_client_price=broker_client_price,
        production_option=production_option,
        policy=policy,
        lock=True,
    )