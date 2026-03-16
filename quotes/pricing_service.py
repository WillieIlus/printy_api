"""
Pricing service — all server-side price computation for quote items.

Public API:
    compute_quote_item_pricing(item) -> PricingResult
    compute_and_store_pricing(item)  -> QuoteItem (saved with snapshot)

Design:
    Product = immutable template (gallery).
    QuoteItem = tweaked instance (user's chosen options).
    This module computes pricing from the QuoteItem's FK refs and stores
    a full breakdown snapshot so the quote is auditable even if rates change.
"""
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from math import ceil
from typing import Optional

from catalog.choices import PricingMode
from catalog.imposition import (
    pieces_per_sheet as _pieces_per_sheet,
    sheets_needed as _sheets_needed,
)
from pricing.choices import ChargeUnit, Sides
from pricing.models import PrintingRate


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class FinishingLineItem:
    name: str
    charge_unit: str
    rate_price: str
    computed_cost: str

@dataclass
class PricingResult:
    can_calculate: bool = False
    pricing_mode: str = ""

    # Imposition (SHEET)
    copies_per_sheet: int = 0
    sheets_needed: int = 0

    # Area (LARGE_FORMAT)
    area_m2: str = "0"

    # Cost components
    paper_cost: str = "0"
    print_cost: str = "0"
    material_cost: str = "0"
    finishing_total: str = "0"
    services_total: str = "0"

    # Totals
    unit_price: str = "0"
    line_total: str = "0"

    # Breakdown details
    finishing_lines: list = field(default_factory=list)
    paper_label: str = ""
    machine_label: str = ""
    sides_label: str = ""
    color_label: str = ""
    material_label: str = ""

    # Missing fields (empty = fully calculable)
    missing_fields: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pure compute helpers
# ---------------------------------------------------------------------------

def compute_imposition(product, paper) -> dict:
    """
    Compute how many copies fit on one sheet.
    Returns {'copies_per_sheet': int, 'sheet_size': str}.
    """
    if not product or not paper:
        return {"copies_per_sheet": 1, "sheet_size": ""}
    w = paper.width_mm or 0
    h = paper.height_mm or 0
    if w and h and product.default_finished_width_mm and product.default_finished_height_mm:
        cps = product.get_copies_per_sheet(paper.sheet_size, w, h)
    else:
        cps = 1
    return {"copies_per_sheet": max(1, cps), "sheet_size": paper.sheet_size}


def compute_sheets_needed(quantity: int, copies_per_sheet: int) -> int:
    """ceil(quantity / copies_per_sheet), minimum 1."""
    return _sheets_needed(quantity, copies_per_sheet)


def compute_large_format_area(width_mm: int, height_mm: int, quantity: int) -> Decimal:
    """Area in m² = (w/1000) × (h/1000) × qty."""
    if not width_mm or not height_mm or quantity <= 0:
        return Decimal("0")
    return (Decimal(width_mm) / 1000) * (Decimal(height_mm) / 1000) * quantity


def compute_print_cost(machine, paper, sheets_count: int, sides: str, color_mode: str) -> Decimal:
    """Resolve PrintingRate and compute cost = rate × sheets."""
    if not machine or not paper or not sides or not color_mode:
        return Decimal("0")
    _, price = PrintingRate.resolve(machine, paper.sheet_size, color_mode, sides)
    if price is None:
        return Decimal("0")
    return price * sheets_count


def _sides_count(sides: str) -> int:
    return 2 if sides == Sides.DUPLEX else 1


def compute_single_finishing_cost(
    finishing_rate,
    quantity: int,
    area_sqm: Decimal,
    sides_count: int,
    sheets_count: int,
    price_override=None,
    apply_to_sides: str = "BOTH",
) -> Decimal:
    """Compute cost for one finishing rate."""
    if price_override is not None:
        p_single = Decimal(str(price_override))
        p_double = p_single * 2
    else:
        p_single = finishing_rate.price
        p_double = (
            finishing_rate.double_side_price
            if finishing_rate.double_side_price is not None
            else finishing_rate.price * 2
        )

    if apply_to_sides == "SINGLE":
        eff_sides = 1
    elif apply_to_sides == "DOUBLE":
        eff_sides = 2
    else:
        eff_sides = sides_count

    cost = Decimal("0")
    cu = finishing_rate.charge_unit
    sheet_count = sheets_count or max(1, quantity)
    if cu == ChargeUnit.PER_PIECE:
        cost = (p_double if eff_sides == 2 else p_single) * quantity
    elif cu == ChargeUnit.PER_SIDE:
        cost = p_single * quantity * eff_sides
    elif cu == ChargeUnit.PER_SHEET:
        # Flat per sheet (cutting, folding) — no single/double distinction
        cost = finishing_rate.price * sheet_count
        if finishing_rate.setup_fee:
            cost += finishing_rate.setup_fee
    elif cu == ChargeUnit.PER_SIDE_PER_SHEET:
        # Per side per sheet: price × sheets × sides (e.g. lamination)
        cost = p_single * sheet_count * eff_sides
        if finishing_rate.setup_fee:
            cost += finishing_rate.setup_fee
    elif cu == ChargeUnit.PER_SQM:
        cost = finishing_rate.price * area_sqm
    elif cu == ChargeUnit.FLAT:
        cost = p_double if eff_sides == 2 else p_single
        if finishing_rate.setup_fee:
            cost += finishing_rate.setup_fee
    return cost


def _iter_finishings(finishings):
    """Accept queryset or list of objects with finishing_rate, price_override, apply_to_sides."""
    if hasattr(finishings, "select_related"):
        return finishings.select_related("finishing_rate").all()
    return finishings or []


def compute_finishings_cost(
    finishings_qs,
    quantity: int,
    area_sqm: Decimal,
    sides_count: int,
    sheets_count: int,
) -> tuple[Decimal, list[FinishingLineItem]]:
    """
    Compute total finishing cost from QuoteItemFinishing queryset or list.
    Returns (total, line_items).
    """
    total = Decimal("0")
    lines = []
    for qif in _iter_finishings(finishings_qs):
        fr = qif.finishing_rate
        cost = compute_single_finishing_cost(
            fr, quantity, area_sqm, sides_count, sheets_count,
            price_override=qif.price_override,
            apply_to_sides=getattr(qif, "apply_to_sides", "BOTH") or "BOTH",
        )
        total += cost
        lines.append(FinishingLineItem(
            name=fr.name,
            charge_unit=fr.charge_unit,
            rate_price=str(fr.price),
            computed_cost=str(cost),
        ))
    return total, lines


# ---------------------------------------------------------------------------
# Main pricing computation
# ---------------------------------------------------------------------------

def compute_quote_item_pricing(item) -> PricingResult:
    """
    Compute full pricing for a QuoteItem. Does NOT save anything.
    Returns a PricingResult with all computed values and breakdown.

    Example response (as dict):
    {
        "can_calculate": true,
        "pricing_mode": "SHEET",
        "copies_per_sheet": 10,
        "sheets_needed": 10,
        "paper_cost": "240.00",
        "print_cost": "750.00",
        "finishing_total": "250.00",
        "unit_price": "12.40",
        "line_total": "1240.00",
        "paper_label": "SRA3 300gsm GLOSS",
        "finishing_lines": [{"name": "Lamination", ...}],
        ...
    }
    """
    result = PricingResult()
    product = item.product
    quantity = item.quantity or 0

    if item.item_type == "PRODUCT" and item.product_id:
        result.pricing_mode = product.pricing_mode or "SHEET"
    else:
        result.pricing_mode = item.pricing_mode or ("LARGE_FORMAT" if item.material_id else "SHEET")

    if quantity <= 0:
        result.missing_fields.append("quantity")
        result.reason = "Quantity must be > 0."
        return result

    sides_count = _sides_count(item.sides)
    result.sides_label = "Double-sided" if item.sides == Sides.DUPLEX else "Single-sided"
    result.color_label = item.color_mode or ""

    if result.pricing_mode == PricingMode.SHEET:
        return _compute_sheet_pricing(item, product, quantity, sides_count, result)
    elif result.pricing_mode == PricingMode.LARGE_FORMAT:
        return _compute_large_format_pricing(item, product, quantity, sides_count, result)

    result.reason = "Unknown pricing_mode."
    return result


def _compute_sheet_pricing(item, product, quantity, sides_count, result: PricingResult) -> PricingResult:
    if not item.paper_id:
        result.missing_fields.append("paper")
        result.reason = "Paper selection required for sheet pricing."
        return result

    paper = item.paper
    result.paper_label = f"{paper.sheet_size} {paper.gsm}gsm {paper.get_paper_type_display()}"

    imp = compute_imposition(product, paper)
    result.copies_per_sheet = imp["copies_per_sheet"]
    result.sheets_needed = compute_sheets_needed(quantity, result.copies_per_sheet)

    paper_cost = paper.selling_price * result.sheets_needed
    result.paper_cost = str(paper_cost)

    print_cost = Decimal("0")
    if item.machine_id and item.sides and item.color_mode:
        print_cost = compute_print_cost(item.machine, paper, result.sheets_needed, item.sides, item.color_mode)
        result.machine_label = item.machine.name if item.machine else ""
    else:
        if not item.machine_id:
            result.missing_fields.append("machine")
        if not item.sides:
            result.missing_fields.append("sides")
        if not item.color_mode:
            result.missing_fields.append("color_mode")
    result.print_cost = str(print_cost)

    sheet_area = Decimal("0")
    if paper.width_mm and paper.height_mm:
        sheet_area = (Decimal(paper.width_mm) / 1000) * (Decimal(paper.height_mm) / 1000)
    area_sqm = sheet_area * result.sheets_needed

    finishing_total, finishing_lines = compute_finishings_cost(
        item.finishings, quantity, area_sqm, sides_count, result.sheets_needed
    )
    result.finishing_total = str(finishing_total)
    result.finishing_lines = [asdict(fl) for fl in finishing_lines]

    services_total = _compute_services_total(item)
    result.services_total = str(services_total)

    line_total = paper_cost + print_cost + finishing_total + services_total
    result.line_total = str(line_total)
    result.unit_price = str(line_total / quantity) if quantity > 0 else "0"
    result.can_calculate = len(result.missing_fields) == 0
    if result.missing_fields:
        result.reason = f"Missing: {', '.join(result.missing_fields)}"
    result.area_m2 = str(area_sqm)
    return result


def _compute_large_format_pricing(item, product, quantity, sides_count, result: PricingResult) -> PricingResult:
    if not item.material_id:
        result.missing_fields.append("material")
    w = item.chosen_width_mm
    h = item.chosen_height_mm
    if not w:
        result.missing_fields.append("chosen_width_mm")
    if not h:
        result.missing_fields.append("chosen_height_mm")
    if result.missing_fields:
        result.reason = f"Missing: {', '.join(result.missing_fields)}"
        return result

    material = item.material
    result.material_label = f"{material.material_type} ({material.unit})"
    area_sqm = compute_large_format_area(w, h, quantity)
    result.area_m2 = str(area_sqm)

    material_cost = material.selling_price * area_sqm
    result.material_cost = str(material_cost)

    finishing_total, finishing_lines = compute_finishings_cost(
        item.finishings, quantity, area_sqm, sides_count, 0
    )
    result.finishing_total = str(finishing_total)
    result.finishing_lines = [asdict(fl) for fl in finishing_lines]

    services_total = _compute_services_total(item)
    result.services_total = str(services_total)

    line_total = material_cost + finishing_total + services_total
    result.line_total = str(line_total)
    result.unit_price = str(line_total / quantity) if quantity > 0 else "0"
    result.can_calculate = True
    return result


def _compute_services_total(item) -> Decimal:
    total = Decimal("0")
    services = getattr(item, "services", None)
    if services is None:
        return total
    if hasattr(services, "select_related"):
        iterable = services.select_related("service_rate").filter(is_selected=True)
    else:
        iterable = services if isinstance(services, (list, tuple)) else []
    for qis in iterable:
        if qis.price_override is not None:
            total += qis.price_override
        elif qis.service_rate.price is not None:
            total += qis.service_rate.price
    return total


# ---------------------------------------------------------------------------
# Gallery calculate-price (no QuoteItem)
# ---------------------------------------------------------------------------

def compute_pricing_from_spec(
    product,
    quantity: int,
    *,
    paper_id=None,
    material_id=None,
    machine_id=None,
    sides: str = "",
    color_mode: str = "COLOR",
    chosen_width_mm=None,
    chosen_height_mm=None,
    finishing_specs=None,
    finishing_rate_ids=None,
) -> PricingResult:
    """
    Compute pricing from a spec (IDs) without creating a QuoteItem.
    Used by gallery calculate-price API.
    """
    from inventory.models import Paper, Machine
    from pricing.models import Material, FinishingRate

    class VirtualFinishing:
        def __init__(self, finishing_rate, price_override=None, apply_to_sides="BOTH"):
            self.finishing_rate = finishing_rate
            self.price_override = price_override
            self.apply_to_sides = apply_to_sides or "BOTH"

    class VirtualItem:
        def __init__(self):
            self.item_type = "PRODUCT"
            self.product_id = product.id if product else None
            self.product = product
            self.quantity = quantity
            self.paper_id = paper_id
            self.paper = Paper.objects.filter(pk=paper_id).first() if paper_id else None
            self.material_id = material_id
            self.material = Material.objects.filter(pk=material_id).first() if material_id else None
            self.machine_id = machine_id
            self.machine = Machine.objects.filter(pk=machine_id).first() if machine_id else None
            self.sides = sides or ""
            self.color_mode = color_mode or "COLOR"
            self.chosen_width_mm = chosen_width_mm
            self.chosen_height_mm = chosen_height_mm
            self.pricing_mode = (product.pricing_mode or "SHEET") if product else "SHEET"
            self.services = []

            finishings = []
            specs = finishing_specs if finishing_specs is not None else [{"finishing_rate": fid, "apply_to_sides": "BOTH"} for fid in (finishing_rate_ids or [])]
            for spec in specs:
                fid = spec.get("finishing_rate") if isinstance(spec, dict) else spec
                apply_to_sides = spec.get("apply_to_sides", "BOTH") if isinstance(spec, dict) else "BOTH"
                fr = FinishingRate.objects.filter(pk=fid, is_active=True).first()
                if fr:
                    finishings.append(VirtualFinishing(finishing_rate=fr, apply_to_sides=apply_to_sides))
            self.finishings = finishings

    item = VirtualItem()
    return compute_quote_item_pricing(item)


# ---------------------------------------------------------------------------
# Compute + persist (transactional)
# ---------------------------------------------------------------------------

def compute_and_store_pricing(item) -> PricingResult:
    """
    Compute pricing and persist unit_price, line_total, and pricing_snapshot.
    Call inside transaction.atomic().
    """
    result = compute_quote_item_pricing(item)
    item.unit_price = Decimal(result.unit_price)
    item.line_total = Decimal(result.line_total)
    item.pricing_snapshot = result.to_dict()
    item.save(update_fields=["unit_price", "line_total", "pricing_snapshot", "updated_at"])
    return result
