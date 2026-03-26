from decimal import Decimal

from pricing.choices import FinishingBillingBasis, FinishingSideMode


def selected_side_count(selected_side: str | None) -> int:
    if selected_side == "both":
        return 2
    if selected_side in {"front", "back"}:
        return 1
    return 1


def compute_finishing_total(rule, *, quantity: int, good_sheets: int, group_quantity: int = 1, line_quantity: int = 1, selected_side: str = "both") -> dict:
    basis = rule.billing_basis
    side_multiplier = (
        selected_side_count(selected_side)
        if rule.side_mode == FinishingSideMode.PER_SELECTED_SIDE
        else 1
    )

    if basis == FinishingBillingBasis.PER_SHEET:
        units = good_sheets
    elif basis == FinishingBillingBasis.PER_PIECE:
        units = quantity
    elif basis == FinishingBillingBasis.FLAT_PER_GROUP:
        units = max(1, group_quantity)
    elif basis == FinishingBillingBasis.FLAT_PER_LINE:
        units = max(1, line_quantity)
    else:
        units = 1

    subtotal = Decimal(str(rule.price)) * Decimal(units) * Decimal(side_multiplier)
    minimum_charge = Decimal(str(rule.minimum_charge or "0"))
    total = max(subtotal, minimum_charge) if minimum_charge else subtotal
    return {
        "name": rule.name,
        "slug": rule.slug,
        "billing_basis": basis,
        "side_mode": rule.side_mode,
        "selected_side": selected_side,
        "selected_side_count": side_multiplier,
        "units": units,
        "rate": str(rule.price),
        "minimum_charge": str(rule.minimum_charge or "0"),
        "total": str(total),
    }
