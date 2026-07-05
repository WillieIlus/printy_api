from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from pricing.models import PlatformFeePolicy


MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.0001")
MIN_MARKUP_MULTIPLE = Decimal("1.05")


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def get_active_platform_fee_policy() -> PlatformFeePolicy:
    policy = PlatformFeePolicy.objects.filter(is_active=True).order_by("-updated_at", "-created_at").first()
    if policy:
        return policy
    return PlatformFeePolicy.objects.create()


def calculate_financial_split(*, production_cost, broker_client_price, policy=None) -> dict:
    production_cost = money(production_cost)
    broker_client_price = money(broker_client_price)
    policy = policy or get_active_platform_fee_policy()

    if production_cost <= 0:
        raise ValidationError("Production cost must be greater than zero.")
    if broker_client_price < production_cost:
        raise ValidationError("Broker client price cannot be below production cost.")

    max_allowed_client_price = money(policy.get_max_client_price(production_cost))
    if broker_client_price > max_allowed_client_price:
        raise ValidationError("Broker client price exceeds the policy cap.")

    gross_margin = money(broker_client_price - production_cost)
    printer_side_fee = money(production_cost * policy.printer_fee_rate)
    broker_margin_fee = money(gross_margin * policy.broker_margin_fee_rate)
    printy_fee = money(printer_side_fee + broker_margin_fee)
    shop_payout = production_cost

    client_total = money(broker_client_price + printy_fee) if policy.add_platform_fee_on_top else broker_client_price
    broker_payout = money(gross_margin - printy_fee)

    if broker_payout < 0:
        raise ValidationError("Broker payout cannot be negative.")

    applied_markup_multiple = (broker_client_price / production_cost).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)
    return {
        "policy": policy,
        "production_cost": production_cost,
        "broker_client_price": broker_client_price,
        "gross_margin": gross_margin,
        "printer_side_fee": printer_side_fee,
        "broker_margin_fee": broker_margin_fee,
        "printy_fee": printy_fee,
        "shop_payout": shop_payout,
        "broker_payout": broker_payout,
        "client_total": client_total,
        "max_allowed_client_price": max_allowed_client_price,
        "applied_markup_multiple": applied_markup_multiple,
    }


@transaction.atomic
def create_quote_financial_split(
    *,
    quote,
    production_cost,
    broker_client_price,
    production_option=None,
    policy=None,
):
    from quotes.models import QuoteFinancialSplit

    payload = calculate_financial_split(
        production_cost=production_cost,
        broker_client_price=broker_client_price,
        policy=policy,
    )
    existing = QuoteFinancialSplit.objects.filter(quote=quote).first()
    if existing is not None:
        return existing
    return QuoteFinancialSplit.objects.create(
        quote=quote,
        policy_used=payload["policy"],
        production_option=production_option,
        production_cost=payload["production_cost"],
        broker_client_price=payload["broker_client_price"],
        gross_margin=payload["gross_margin"],
        printer_side_fee=payload["printer_side_fee"],
        broker_margin_fee=payload["broker_margin_fee"],
        printy_fee=payload["printy_fee"],
        shop_payout=payload["shop_payout"],
        broker_payout=payload["broker_payout"],
        client_total=payload["client_total"],
        max_allowed_client_price=payload["max_allowed_client_price"],
        applied_markup_multiple=payload["applied_markup_multiple"],
    )


def ensure_quote_financial_split(*, quote, policy=None):
    if getattr(quote, "financial_split", None):
        return quote.financial_split
    production_option = getattr(quote, "production_option", None)
    production_cost = getattr(production_option, "production_cost", None) or getattr(quote, "total", None)
    broker_client_price = getattr(quote, "total", None)
    if production_cost is None or broker_client_price is None:
        raise ValidationError("Quote needs production cost and client price before acceptance.")
    return create_quote_financial_split(
        quote=quote,
        production_cost=production_cost,
        broker_client_price=broker_client_price,
        production_option=production_option,
        policy=policy,
    )
