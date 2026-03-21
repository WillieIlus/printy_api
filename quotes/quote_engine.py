"""
Quote pricing engine — calculates unit_price and line_total for quote items.
Uses direct FK refs: paper, material, machine, finishings.
"""
from decimal import Decimal

from django.utils import timezone

from pricing.models import PrintingRate
from inventory.choices import SheetSize

from .choices import QuoteStatus
from .models import QuoteItem, QuoteRequest


def _get_sheet_size_from_product(item: QuoteItem) -> str:
    """Infer sheet size from product or paper. Default to A4."""
    if item.paper_id:
        return item.paper.sheet_size
    # Product default dimensions might map to a sheet size; use A4 as fallback
    return SheetSize.A4


def calculate_quote_item(item: QuoteItem) -> tuple[Decimal, Decimal]:
    """
    Calculate unit_price and line_total for a QuoteItem.
    Returns (unit_price, line_total).
    """
    quantity = item.quantity or 0
    if quantity <= 0:
        return Decimal("0"), Decimal("0")

    base_cost = Decimal("0")

    # Paper cost (SHEET mode)
    if item.paper_id:
        # selling_price is per sheet; assume 1 sheet per unit for simplicity
        sheets = quantity
        base_cost += item.paper.selling_price * sheets

    # Printing cost (if machine specified)
    if item.machine_id and item.sides and item.color_mode:
        sheet_size = _get_sheet_size_from_product(item)
        _, price = PrintingRate.resolve(
            item.machine, sheet_size, item.color_mode, item.sides
        )
        if price is not None:
            base_cost += price * quantity

    # Material cost (LARGE_FORMAT mode)
    if item.material_id:
        # selling_price per unit (SQM); for simplicity use per-piece
        base_cost += item.material.selling_price * quantity

    # If no paper/material/machine, use a minimal placeholder
    if base_cost == 0:
        base_cost = Decimal("1") * quantity  # Placeholder

    # Finishing costs
    finishing_cost = Decimal("0")
    for qif in item.finishings.all():
        rate = qif.finishing_rate
        if qif.price_override is not None:
            finishing_cost += qif.price_override
        else:
            coverage = qif.coverage_qty or quantity
            finishing_cost += rate.price * coverage
        if rate.setup_fee:
            finishing_cost += rate.setup_fee

    line_total = base_cost + finishing_cost
    unit_price = line_total / quantity if quantity else Decimal("0")
    return unit_price, line_total


def recalculate_and_lock_quote_request(quote_request: QuoteRequest) -> None:
    """Calculate all items, lock prices, set status to QUOTED."""
    from quotes.services import calculate_quote_item as calc_item

    now = timezone.now()
    total = Decimal("0")
    for item in quote_request.items.all():
        unit_price, line_total = calc_item(item, force=True)
        item.unit_price = unit_price
        item.line_total = line_total
        item.pricing_locked_at = now
        item.save()
        total += line_total
    quote_request.total = total
    quote_request.status = QuoteStatus.QUOTED
    quote_request.save(update_fields=["total", "status", "updated_at"])
