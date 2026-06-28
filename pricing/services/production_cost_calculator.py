from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Any

from django.core.exceptions import ValidationError

from pricing.models import QuantityPricingTier, SetupCostPolicy, WastePolicy
from pricing.services.platform_fee_policy import calculate_financial_split


MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.0001")


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value in (None, ""):
        value = default
    return Decimal(str(value))


def _money(value: Any) -> Decimal:
    return _decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def get_active_waste_policy() -> WastePolicy:
    policy = WastePolicy.objects.filter(is_active=True).order_by("-updated_at", "-created_at").first()
    if policy:
        return policy
    return WastePolicy.objects.create()


def get_active_setup_cost_policy() -> SetupCostPolicy:
    policy = SetupCostPolicy.objects.filter(is_active=True).order_by("-updated_at", "-created_at").first()
    if policy:
        return policy
    return SetupCostPolicy.objects.create()


@dataclass(frozen=True)
class ProductionCostInput:
    quantity: int
    yield_per_sheet: int
    paper_cost_per_sheet: Decimal
    click_charge_per_sheet: Decimal
    finishing_cost: Decimal = Decimal("0.00")


def normalize_production_cost_input(spec: dict[str, Any] | ProductionCostInput) -> ProductionCostInput:
    if isinstance(spec, ProductionCostInput):
        return spec
    quantity = int(spec.get("quantity") or spec.get("requested_qty") or 0)
    yield_per_sheet = int(spec.get("yield_per_sheet") or 0)
    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    if yield_per_sheet <= 0:
        raise ValidationError("Yield per sheet must be greater than zero.")
    return ProductionCostInput(
        quantity=quantity,
        yield_per_sheet=yield_per_sheet,
        paper_cost_per_sheet=_money(spec.get("paper_cost_per_sheet")),
        click_charge_per_sheet=_money(spec.get("click_charge_per_sheet")),
        finishing_cost=_money(spec.get("finishing_cost")),
    )


def calculate_billable_sheets(
    *,
    quantity: int,
    yield_per_sheet: int,
    waste_policy: WastePolicy | None = None,
) -> dict[str, int | WastePolicy]:
    if quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    if yield_per_sheet <= 0:
        raise ValidationError("Yield per sheet must be greater than zero.")
    waste_policy = waste_policy or get_active_waste_policy()

    raw_sheets = _ceil(Decimal(quantity) / Decimal(yield_per_sheet))
    variable_waste_sheets = _ceil(Decimal(raw_sheets) * Decimal(waste_policy.variable_waste_rate))
    total_sheets_needed = raw_sheets + int(waste_policy.fixed_waste_sheets) + variable_waste_sheets
    billable_sheets = max(total_sheets_needed, int(waste_policy.minimum_billable_sheets))

    return {
        "waste_policy": waste_policy,
        "raw_sheets": raw_sheets,
        "fixed_waste_sheets": int(waste_policy.fixed_waste_sheets),
        "variable_waste_sheets": variable_waste_sheets,
        "waste_sheets_added": int(waste_policy.fixed_waste_sheets) + variable_waste_sheets,
        "total_sheets_needed": total_sheets_needed,
        "minimum_billable_sheets": int(waste_policy.minimum_billable_sheets),
        "billable_sheets": billable_sheets,
    }


def calculate_setup_cost(*, setup_policy: SetupCostPolicy | None = None) -> dict[str, Decimal | SetupCostPolicy]:
    setup_policy = setup_policy or get_active_setup_cost_policy()
    setup_labor_cost = _money(
        (Decimal(setup_policy.setup_minutes) / Decimal("60")) * Decimal(setup_policy.labor_rate_per_hour)
    )
    setup_cost = _money(
        setup_labor_cost
        + Decimal(setup_policy.machine_setup_fee)
        + Decimal(setup_policy.admin_handling_fee)
        + Decimal(setup_policy.file_check_fee)
    )
    return {
        "setup_policy": setup_policy,
        "setup_minutes": int(setup_policy.setup_minutes),
        "labor_rate_per_hour": _money(setup_policy.labor_rate_per_hour),
        "setup_labor_cost": setup_labor_cost,
        "machine_setup_fee": _money(setup_policy.machine_setup_fee),
        "admin_handling_fee": _money(setup_policy.admin_handling_fee),
        "file_check_fee": _money(setup_policy.file_check_fee),
        "setup_cost": setup_cost,
    }


def find_quantity_pricing_tier(*, billable_sheets: int) -> QuantityPricingTier:
    if billable_sheets <= 0:
        raise ValidationError("Billable sheets must be greater than zero.")
    tier = (
        QuantityPricingTier.objects.filter(
            is_active=True,
            min_sheets__lte=billable_sheets,
        )
        .filter(max_sheets__gte=billable_sheets)
        .order_by("min_sheets", "max_sheets")
        .first()
    )
    if tier is None:
        tier = (
            QuantityPricingTier.objects.filter(
                is_active=True,
                min_sheets__lte=billable_sheets,
                max_sheets__isnull=True,
            )
            .order_by("-min_sheets")
            .first()
        )
    if tier is None:
        raise ValidationError("No active quantity pricing tier matches the billable sheets.")
    return tier


def calculate_production_cost(
    spec: dict[str, Any] | ProductionCostInput,
    *,
    waste_policy: WastePolicy | None = None,
    setup_policy: SetupCostPolicy | None = None,
) -> dict[str, Any]:
    normalized = normalize_production_cost_input(spec)
    sheet_payload = calculate_billable_sheets(
        quantity=normalized.quantity,
        yield_per_sheet=normalized.yield_per_sheet,
        waste_policy=waste_policy,
    )
    setup_payload = calculate_setup_cost(setup_policy=setup_policy)
    billable_sheets = int(sheet_payload["billable_sheets"])

    material_cost = _money(Decimal(billable_sheets) * normalized.paper_cost_per_sheet)
    click_cost = _money(Decimal(billable_sheets) * normalized.click_charge_per_sheet)
    finishing_cost = _money(normalized.finishing_cost)
    production_cost = _money(material_cost + click_cost + setup_payload["setup_cost"] + finishing_cost)

    return {
        **sheet_payload,
        **setup_payload,
        "quantity": normalized.quantity,
        "yield_per_sheet": normalized.yield_per_sheet,
        "paper_cost_per_sheet": _money(normalized.paper_cost_per_sheet),
        "click_charge_per_sheet": _money(normalized.click_charge_per_sheet),
        "material_cost": material_cost,
        "click_cost": click_cost,
        "finishing_cost": finishing_cost,
        "production_cost": production_cost,
    }


def apply_volume_penalty(*, production_cost, billable_sheets: int) -> dict[str, Any]:
    production_cost = _money(production_cost)
    tier = find_quantity_pricing_tier(billable_sheets=billable_sheets)
    calculated_client_price = _money(production_cost * Decimal(tier.multiplier))
    minimum_order_floor = _money(tier.minimum_order_floor)
    final_client_price = max(calculated_client_price, minimum_order_floor)
    return {
        "quantity_pricing_tier": tier,
        "volume_multiplier": Decimal(tier.multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "minimum_order_floor": minimum_order_floor,
        "calculated_client_price": calculated_client_price,
        "final_client_price": final_client_price,
    }


def calculate_client_price_with_waste_setup_and_quantity_tier(
    spec: dict[str, Any] | ProductionCostInput,
    *,
    waste_policy: WastePolicy | None = None,
    setup_policy: SetupCostPolicy | None = None,
) -> dict[str, Any]:
    production = calculate_production_cost(
        spec,
        waste_policy=waste_policy,
        setup_policy=setup_policy,
    )
    volume = apply_volume_penalty(
        production_cost=production["production_cost"],
        billable_sheets=int(production["billable_sheets"]),
    )
    return {**production, **volume}


def calculate_penalized_financial_split(
    spec: dict[str, Any] | ProductionCostInput,
    *,
    waste_policy: WastePolicy | None = None,
    setup_policy: SetupCostPolicy | None = None,
    platform_policy=None,
) -> dict[str, Any]:
    pricing = calculate_client_price_with_waste_setup_and_quantity_tier(
        spec,
        waste_policy=waste_policy,
        setup_policy=setup_policy,
    )
    split = calculate_financial_split(
        production_cost=pricing["production_cost"],
        broker_client_price=pricing["final_client_price"],
        policy=platform_policy,
    )
    return {**pricing, "financial_split": split}
