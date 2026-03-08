from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from common.utils import decimal_from_value

ZERO = Decimal("0.00")


def _safe_decimal(value) -> Decimal:
    """Safely convert to Decimal; returns ZERO on invalid input."""
    try:
        result = decimal_from_value(value)
        return result if result is not None else ZERO
    except Exception:
        return ZERO


@dataclass(frozen=True)
class QuoteCostBreakdown:
    quantity: int
    base_units: int
    paper_cost: Decimal
    printing_cost: Decimal
    finishing_cost: Decimal
    total_cost: Decimal
    unit_price: Decimal
    notes: str = ""




def _sheet_area_sqm(width_mm, height_mm) -> Decimal:
    width_m = _safe_decimal(width_mm) / Decimal("1000")
    height_m = _safe_decimal(height_mm) / Decimal("1000")
    return width_m * height_m


def get_sheet_printing_rate(item) -> Decimal:
    """
    Current printy_api-compatible lookup for sheet printing.
    Uses PrintingRate.resolve(machine, sheet_size, color_mode, sides).
    """
    if not item.machine or not item.paper or not item.sides or not item.color_mode:
        return ZERO

    from pricing.models import PrintingRate

    _, price = PrintingRate.resolve(
        item.machine,
        item.paper.sheet_size,
        item.color_mode,
        item.sides,
    )
    return _safe_decimal(price) if price is not None else ZERO


def get_large_format_material_cost(item) -> Decimal:
    """
    Cost basis for large format jobs using chosen dimensions.
    Uses current Material.selling_price * total area.
    """
    if not item.material or not item.chosen_width_mm or not item.chosen_height_mm:
        return ZERO

    area_per_piece = _sheet_area_sqm(item.chosen_width_mm, item.chosen_height_mm)
    total_area = area_per_piece * _safe_decimal(item.quantity)
    return _safe_decimal(item.material.selling_price) * total_area


def get_finishing_total(item, *, base_units: int, area_sqm: Optional[Decimal] = None) -> Decimal:
    """
    Finishing calculator adapted to current QuoteItemFinishing / FinishingRate models.

    Supported current charge units:
    - PER_PIECE
    - PER_SIDE
    - PER_SQM
    - FLAT

    Notes:
    - base_units should be 'quantity' for now.
    - later, for proper imposed sheet work, base_units can become sheets_needed.
    """
    total = ZERO
    sides_count = 2 if item.sides == "DUPLEX" else 1

    for qif in item.finishings.select_related("finishing_rate").all():
        fr = qif.finishing_rate
        price = _safe_decimal(qif.price_override) if qif.price_override is not None else _safe_decimal(fr.price)
        setup_fee = _safe_decimal(fr.setup_fee)

        if fr.charge_unit == "PER_PIECE":
            total += price * _safe_decimal(base_units)

        elif fr.charge_unit == "PER_SIDE":
            total += price * _safe_decimal(base_units) * _safe_decimal(sides_count)

        elif fr.charge_unit == "PER_SQM":
            coverage = _safe_decimal(qif.coverage_qty) if qif.coverage_qty is not None else None
            if coverage is not None and coverage > 0:
                total += price * coverage
            elif area_sqm is not None:
                total += price * area_sqm
            elif item.paper and item.paper.width_mm and item.paper.height_mm:
                # fallback: paper area * quantity
                total += price * _sheet_area_sqm(item.paper.width_mm, item.paper.height_mm) * _safe_decimal(base_units)

        elif fr.charge_unit == "FLAT":
            total += price + setup_fee

    return total


def cost_sheet_item(item) -> QuoteCostBreakdown:
    """
    Current-compatible SHEET costing.

    For now:
    - paper cost is per finished unit, matching your current model behavior
    - printing rate is per finished unit, matching current services.py behavior
    - finishing uses quantity as base_units

    Later:
    - swap base_units from quantity to sheets_needed from an imposition engine
    """
    quantity = max(int(item.quantity or 0), 0)

    if not item.paper:
        return QuoteCostBreakdown(
            quantity=quantity,
            base_units=quantity,
            paper_cost=ZERO,
            printing_cost=ZERO,
            finishing_cost=ZERO,
            total_cost=ZERO,
            unit_price=ZERO,
            notes="Missing paper.",
        )

    paper_cost_total = _safe_decimal(item.paper.selling_price) * _safe_decimal(quantity)
    printing_rate = get_sheet_printing_rate(item)
    printing_cost_total = printing_rate * _safe_decimal(quantity)
    finishing_cost_total = get_finishing_total(item, base_units=quantity)

    total_cost = paper_cost_total + printing_cost_total + finishing_cost_total
    unit_price = (total_cost / _safe_decimal(quantity)) if quantity else ZERO

    return QuoteCostBreakdown(
        quantity=quantity,
        base_units=quantity,
        paper_cost=paper_cost_total,
        printing_cost=printing_cost_total,
        finishing_cost=finishing_cost_total,
        total_cost=total_cost,
        unit_price=unit_price,
        notes="Current model-compatible sheet costing. Not yet imposition-based.",
    )


def cost_large_format_item(item) -> QuoteCostBreakdown:
    """
    Current-compatible LARGE_FORMAT costing.
    """
    quantity = max(int(item.quantity or 0), 0)

    if not item.material or not item.chosen_width_mm or not item.chosen_height_mm:
        return QuoteCostBreakdown(
            quantity=quantity,
            base_units=quantity,
            paper_cost=ZERO,
            printing_cost=ZERO,
            finishing_cost=ZERO,
            total_cost=ZERO,
            unit_price=ZERO,
            notes="Missing material or dimensions.",
        )

    area_per_piece = _sheet_area_sqm(item.chosen_width_mm, item.chosen_height_mm)
    total_area = area_per_piece * _safe_decimal(quantity)

    material_cost_total = _safe_decimal(item.material.selling_price) * total_area
    finishing_cost_total = get_finishing_total(item, base_units=quantity, area_sqm=total_area)

    total_cost = material_cost_total + finishing_cost_total
    unit_price = (total_cost / _safe_decimal(quantity)) if quantity else ZERO

    return QuoteCostBreakdown(
        quantity=quantity,
        base_units=quantity,
        paper_cost=material_cost_total,
        printing_cost=ZERO,
        finishing_cost=finishing_cost_total,
        total_cost=total_cost,
        unit_price=unit_price,
        notes="Current model-compatible large format costing.",
    )


def cost_quote_item(item) -> QuoteCostBreakdown:
    if item.pricing_mode == "SHEET":
        return cost_sheet_item(item)
    if item.pricing_mode == "LARGE_FORMAT":
        return cost_large_format_item(item)

    quantity = max(int(item.quantity or 0), 0)
    return QuoteCostBreakdown(
        quantity=quantity,
        base_units=quantity,
        paper_cost=ZERO,
        printing_cost=ZERO,
        finishing_cost=ZERO,
        total_cost=ZERO,
        unit_price=ZERO,
        notes=f"Unsupported pricing mode: {item.pricing_mode}",
    )
