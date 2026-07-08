from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from pricing.services.platform_fee_policy import calculate_financial_split


MONEY_QUANTIZER = Decimal("0.01")
PERCENT_QUANTIZER = Decimal("0.01")


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (ArithmeticError, InvalidOperation, TypeError, ValueError):
        return default


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def quantize_percent(value: Decimal) -> Decimal:
    return value.quantize(PERCENT_QUANTIZER, rounding=ROUND_HALF_UP)


def get_marketplace_margin_settings(shop=None) -> dict[str, Any]:
    default_gross_margin_percent = Decimal("75.00")
    default_printer_fee_percent = Decimal("0.00")
    return {
        "gross_margin_percent": default_gross_margin_percent,
        "printer_side_fee_percent": default_printer_fee_percent,
        "gross_margin_locked": True,
        "printer_side_fee_locked": True,
        "is_active": True,
        "scope": "platform_policy",
        "shop_id": getattr(shop, "id", None),
        "settings_id": None,
    }


def calculate_client_price(
    base_price: Any,
    gross_margin_percent: Any = None,
    printer_side_fee_percent: Any = None,
) -> dict[str, Decimal]:
    default_gross_margin_percent = Decimal("75.00")
    default_printer_fee_percent = Decimal("0.00")
    base_amount = quantize_money(_decimal(base_price))
    gross_margin_percent = quantize_percent(_decimal(gross_margin_percent, default_gross_margin_percent))
    printer_side_fee_percent = quantize_percent(_decimal(printer_side_fee_percent, default_printer_fee_percent))
    gross_margin = quantize_money(base_amount * gross_margin_percent / Decimal("100"))
    split = calculate_financial_split(
        production_cost=base_amount,
        broker_client_price=base_amount + gross_margin,
    )
    multiplier = quantize_percent(split["applied_markup_multiple"])
    return {
        "production_cost": split["production_cost"],
        "gross_margin_percent": gross_margin_percent,
        "gross_margin": split["gross_margin"],
        "printer_side_fee_percent": printer_side_fee_percent,
        "printer_side_fee": split["printer_side_fee"],
        "broker_margin_fee": split["broker_margin_fee"],
        "printy_fee": split["printy_fee"],
        "shop_payout": split["shop_payout"],
        "broker_payout": split["broker_payout"],
        "client_price": split["client_total"],
        "multiplier": multiplier,
    }


def serialize_marketplace_pricing(summary: dict[str, Decimal], *, currency: str = "KES") -> dict[str, Any]:
    return {
        "currency": currency,
        "production_cost": str(summary["production_cost"]),
        "gross_margin_percent": str(summary["gross_margin_percent"]),
        "gross_margin": str(summary["gross_margin"]),
        "printer_side_fee_percent": str(summary["printer_side_fee_percent"]),
        "printer_side_fee": str(summary["printer_side_fee"]),
        "broker_margin_fee": str(summary["broker_margin_fee"]),
        "printy_fee": str(summary["printy_fee"]),
        "shop_payout": str(summary["shop_payout"]),
        "broker_payout": str(summary["broker_payout"]),
        "client_price": str(summary["client_price"]),
        "multiplier": str(summary["multiplier"]),
        "lines": [
            {
                "key": "production_cost",
                "label": "Your shop price",
                "amount": str(summary["production_cost"]),
            },
            {
                "key": "gross_margin",
                "label": f"Gross margin ({summary['gross_margin_percent']}%)",
                "amount": str(summary["gross_margin"]),
            },
            {
                "key": "printy_fee",
                "label": "Printy fee",
                "amount": str(summary["printy_fee"]),
            },
            {
                "key": "client_price",
                "label": "Client price",
                "amount": str(summary["client_price"]),
            },
        ],
    }


def build_marketplace_pricing_summary(*, base_price: Any, shop=None, currency: str = "KES") -> dict[str, Any]:
    settings = get_marketplace_margin_settings(shop)
    summary = calculate_client_price(
        base_price,
        gross_margin_percent=settings["gross_margin_percent"],
        printer_side_fee_percent=settings["printer_side_fee_percent"],
    )
    return {
        **serialize_marketplace_pricing(summary, currency=currency),
        "settings": {
            "gross_margin_percent": str(settings["gross_margin_percent"]),
            "printer_side_fee_percent": str(settings["printer_side_fee_percent"]),
            "gross_margin_locked": settings["gross_margin_locked"],
            "printer_side_fee_locked": settings["printer_side_fee_locked"],
            "is_active": settings["is_active"],
            "scope": settings["scope"],
            "shop_id": settings.get("shop_id"),
            "settings_id": settings.get("settings_id"),
        },
    }


def apply_marketplace_pricing_to_preview(preview: dict[str, Any], *, shop=None) -> dict[str, Any]:
    payload = deepcopy(preview)
    totals = dict(payload.get("totals") or {})
    currency = payload.get("currency") or "KES"
    base_price = totals.get("grand_total") or totals.get("subtotal") or "0.00"
    marketplace_pricing = build_marketplace_pricing_summary(
        base_price=base_price,
        shop=shop,
        currency=currency,
    )

    totals["shop_total"] = str(quantize_money(_decimal(base_price)))
    totals["production_cost"] = totals["shop_total"]
    totals["client_price"] = marketplace_pricing["client_price"]
    totals["gross_margin"] = marketplace_pricing["gross_margin"]
    totals["printy_fee"] = marketplace_pricing["printy_fee"]
    totals["broker_payout"] = marketplace_pricing["broker_payout"]
    totals["grand_total"] = marketplace_pricing["client_price"]
    payload["totals"] = totals

    breakdown = dict(payload.get("breakdown") or {})
    breakdown["marketplace_pricing"] = marketplace_pricing
    payload["breakdown"] = breakdown
    payload["marketplace_pricing"] = marketplace_pricing
    return payload
