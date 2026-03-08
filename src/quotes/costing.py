"""
Costing logic for quote items.
Pure computation — no DB writes.
"""
from decimal import Decimal

from .constants import SIDES_DUPLEX
from .finishing import cost_quote_item

__all__ = ["cost_quote_item", "compute_sheet_paper_cost", "compute_large_format_area"]
from .imposition import pieces_per_sheet, sheets_needed
from .types import FinishingLineItem, PricingResult


def _sides_count(sides: str | None) -> int:
    """Return number of sides: 1 for SIMPLEX, 2 for DUPLEX."""
    if not sides:
        return 1
    return 2 if sides == SIDES_DUPLEX else 1


def _sheet_area_sqm(paper) -> Decimal:
    """Area of one sheet in sqm."""
    w = getattr(paper, "width_mm", None) or 0
    h = getattr(paper, "height_mm", None) or 0
    if not w or not h:
        return Decimal("0")
    return (Decimal(w) / 1000) * (Decimal(h) / 1000)


def compute_sheet_paper_cost(paper, quantity: int, product=None) -> tuple[Decimal, int, int]:
    """
    Compute paper cost for SHEET mode.
    Returns (paper_cost, copies_per_sheet, sheets_needed).
    """
    if not paper or quantity <= 0:
        return Decimal("0"), 1, max(1, quantity)
    w = getattr(paper, "width_mm", None) or 0
    h = getattr(paper, "height_mm", None) or 0
    if product and w and h:
        pieces = product.get_copies_per_sheet(
            paper.sheet_size, w, h
        )
    else:
        pieces = 1
    sheets = sheets_needed(quantity, pieces)
    cost = (paper.selling_price or Decimal("0")) * sheets
    return cost, pieces, sheets


def compute_large_format_area(width_mm: int, height_mm: int, quantity: int) -> Decimal:
    """Area in m² = (w/1000) × (h/1000) × qty."""
    if not width_mm or not height_mm or quantity <= 0:
        return Decimal("0")
    return (Decimal(width_mm) / 1000) * (Decimal(height_mm) / 1000) * quantity


def compute_finishing_cost(
    finishing_rate,
    quantity: int,
    area_sqm: Decimal,
    sides_count: int,
    sheets_count: int = 0,
    price_override: Decimal | None = None,
    apply_to_sides: str = "BOTH",
) -> Decimal:
    """Compute cost for one finishing rate by charge_unit."""
    # Delegates to main quotes logic when integrated
    return Decimal("0")


def compute_finishing_line_items(
    finishings_qs,
    quantity: int,
    area_sqm: Decimal,
    sides_count: int,
    sheets_count: int,
) -> tuple[Decimal, list[FinishingLineItem]]:
    """Compute total finishing cost and line items."""
    total = Decimal("0")
    lines: list[FinishingLineItem] = []
    for qif in (finishings_qs.select_related("finishing_rate").all() if finishings_qs else []):
        fr = qif.finishing_rate
        cost = compute_finishing_cost(
            fr, quantity, area_sqm, sides_count, sheets_count,
            price_override=getattr(qif, "price_override", None),
            apply_to_sides=getattr(qif, "apply_to_sides", "BOTH") or "BOTH",
        )
        total += cost
        lines.append(FinishingLineItem(
            name=fr.name,
            charge_unit=getattr(fr, "charge_unit", ""),
            rate_price=str(getattr(fr, "price", "0")),
            computed_cost=str(cost),
        ))
    return total, lines
