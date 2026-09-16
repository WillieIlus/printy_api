from dataclasses import asdict, dataclass
from decimal import Decimal

from pricing.choices import FinishingBillingBasis, FinishingSideMode


@dataclass
class FinishingChargeLine:
    name: str
    slug: str
    billing_basis: str
    side_mode: str
    selected_side: str
    side_count: int
    good_sheets: int
    units: str
    units_count: str
    rate: str
    formula: str
    calculation_basis: str
    minimum_charge: str
    total: str
    explanation: str

    def to_dict(self) -> dict:
        return asdict(self)


def selected_side_count(selected_side: str | None) -> int:
    if selected_side == "both":
        return 2
    if selected_side in {"front", "back"}:
        return 1
    return 1


def _resolve_units(
    *,
    billing_basis: str,
    quantity: int,
    good_sheets: int,
    area_sqm: Decimal,
    group_quantity: int,
    line_quantity: int,
) -> tuple[Decimal, str]:
    if billing_basis == FinishingBillingBasis.PER_SHEET:
        return Decimal(good_sheets), f"{good_sheets} sheet(s)"
    if billing_basis == FinishingBillingBasis.PER_PIECE:
        return Decimal(quantity), f"{quantity} piece(s)"
    if billing_basis == FinishingBillingBasis.FLAT_PER_GROUP:
        groups = max(1, group_quantity)
        return Decimal(groups), f"{groups} group(s)"
    if billing_basis == FinishingBillingBasis.FLAT_PER_LINE:
        lines = max(1, line_quantity)
        return Decimal(lines), f"{lines} line(s)"
    if billing_basis == FinishingBillingBasis.FLAT_PER_JOB:
        return Decimal("1"), "1 job"
    return area_sqm, f"{area_sqm.normalize()} sqm"


def _format_unit_label(billing_basis: str, units: Decimal) -> str:
    normalized_units = int(units) if units == units.to_integral_value() else units.normalize()
    if billing_basis == FinishingBillingBasis.PER_SHEET:
        return f"{normalized_units} sheets"
    if billing_basis == FinishingBillingBasis.PER_PIECE:
        return f"{normalized_units} pieces"
    if billing_basis == FinishingBillingBasis.FLAT_PER_GROUP:
        return f"{normalized_units} groups"
    if billing_basis == FinishingBillingBasis.FLAT_PER_LINE:
        return f"{normalized_units} lines"
    if billing_basis == FinishingBillingBasis.FLAT_PER_JOB:
        return "1 job"
    return f"{normalized_units} sqm"


def compute_finishing_line(
    rule,
    *,
    quantity: int,
    good_sheets: int,
    area_sqm: Decimal = Decimal("0"),
    group_quantity: int = 1,
    line_quantity: int = 1,
    selected_side: str = "both",
) -> FinishingChargeLine:
    units, units_label = _resolve_units(
        billing_basis=rule.billing_basis,
        quantity=quantity,
        good_sheets=good_sheets,
        area_sqm=area_sqm,
        group_quantity=group_quantity,
        line_quantity=line_quantity,
    )
    side_count = selected_side_count(selected_side) if rule.side_mode == FinishingSideMode.PER_SELECTED_SIDE else 1
    side_multiplier = Decimal(side_count)
    is_lamination = bool(
        getattr(rule, "is_lamination_rule", None) and rule.is_lamination_rule()
    )
    use_both_side_override = bool(
        is_lamination
        and selected_side == "both"
        and getattr(rule, "double_side_price", None) is not None
    )
    base_rate = Decimal(str(
        getattr(rule, "double_side_price", rule.price)
        if use_both_side_override
        else rule.price
    ))
    subtotal = base_rate * units
    if not use_both_side_override:
        subtotal *= side_multiplier
    minimum_charge = Decimal(str(rule.minimum_charge or "0"))
    total = max(subtotal, minimum_charge) if minimum_charge else subtotal
    human_units_label = _format_unit_label(rule.billing_basis, units)
    explanation = f"{rule.name}: {human_units_label} x {base_rate}"
    formula = "units x rate"
    calculation_basis = f"{units} x {base_rate}"
    if side_count > 1 and not use_both_side_override:
        formula += " x side_count"
        calculation_basis += f" x {side_count}"
        explanation += f" x {int(side_multiplier)} sides"
    if is_lamination:
        formula = "good_sheets x rate"
        calculation_basis = f"{good_sheets} x {base_rate}"
        explanation = f"{rule.name}: {good_sheets} sheets x {base_rate}"
        if use_both_side_override:
            formula = "good_sheets x both_side_rate"
            explanation += " both-side rate"
        elif side_count > 1:
            formula += " x side_count"
            calculation_basis += f" x {side_count}"
            explanation += f" x {side_count} sides"
        elif selected_side in {"front", "back"}:
            explanation += " x 1 side"
    if minimum_charge and total == minimum_charge and minimum_charge > subtotal:
        explanation += f" (minimum {minimum_charge} applied)"

    return FinishingChargeLine(
        name=rule.name,
        slug=rule.slug,
        billing_basis=rule.billing_basis,
        side_mode=rule.side_mode,
        selected_side=selected_side,
        side_count=side_count,
        good_sheets=good_sheets,
        units=str(units),
        units_count=str(units),
        rate=str(base_rate),
        formula=formula,
        calculation_basis=calculation_basis,
        minimum_charge=str(rule.minimum_charge or "0"),
        total=str(total),
        explanation=explanation,
    )


def compute_finishing_total(
    selections: list[dict] | None,
    *,
    quantity: int,
    good_sheets: int,
    area_sqm: Decimal = Decimal("0"),
) -> tuple[Decimal, list[dict]]:
    lines: list[dict] = []
    total = Decimal("0")
    for selection in selections or []:
        line = compute_finishing_line(
            selection["rule"],
            quantity=quantity,
            good_sheets=good_sheets,
            area_sqm=area_sqm,
            group_quantity=selection.get("group_quantity", 1),
            line_quantity=selection.get("line_quantity", 1),
            selected_side=selection.get("selected_side", "both"),
        )
        total += Decimal(line.total)
        lines.append(line.to_dict())
    return total, lines
