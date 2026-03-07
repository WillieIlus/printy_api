"""
Quote item summary layer — structured data for admin, debugging, and frontend.

Design:
- build_quote_item_summary(item) gathers data from pricing_service and imposition.
- format_quote_item_summary(summary) converts to human-readable text.
- No mixing of formatting, imposition, and pricing logic in one function.
- No silent SRA3 fallback; unresolved sheet size → notes.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

from catalog.choices import PricingMode
from quotes.pricing_service import compute_quote_item_pricing


@dataclass(frozen=True)
class FinishingSummaryLine:
    name: str
    quantity: Decimal
    unit_price: Decimal
    total: Decimal


@dataclass(frozen=True)
class QuoteItemSummary:
    item_name: str
    quantity: int
    machine_name: Optional[str]
    stock_name: Optional[str]
    sheet_name: Optional[str]
    items_per_sheet: Optional[int]
    sheets_needed: Optional[int]
    paper_cost: Decimal
    material_cost: Decimal
    print_cost: Decimal
    finishing_cost: Decimal
    total_cost: Decimal
    finishing_lines: List[FinishingSummaryLine]
    notes: List[str]


def _get_item_name(item) -> str:
    """Human-readable item name."""
    if item.item_type == "PRODUCT" and item.product_id and item.product:
        return item.product.name
    return item.title or "Custom item"


def _get_item_dimensions(item) -> tuple[int | None, int | None]:
    """Return (width_mm, height_mm) for imposition or area."""
    if item.pricing_mode == "LARGE_FORMAT" and item.chosen_width_mm and item.chosen_height_mm:
        return item.chosen_width_mm, item.chosen_height_mm
    if item.product_id and item.product:
        w = item.product.default_finished_width_mm
        h = item.product.default_finished_height_mm
        if w and h:
            return int(w), int(h)
    return None, None


def _finishing_lines_from_pricing(result) -> List[FinishingSummaryLine]:
    """Convert PricingResult finishing_lines to FinishingSummaryLine."""
    lines: List[FinishingSummaryLine] = []
    for fl in getattr(result, "finishing_lines", []) or []:
        if isinstance(fl, dict):
            name = fl.get("name", "")
            total = Decimal(str(fl.get("computed_cost", 0)))
            qty = Decimal("1")
            unit_price = total
            lines.append(FinishingSummaryLine(
                name=name,
                quantity=qty,
                unit_price=unit_price,
                total=total,
            ))
    return lines


def build_quote_item_summary(item) -> QuoteItemSummary:
    """
    Build structured summary from QuoteItem.
    Reuses compute_quote_item_pricing; no recalculated imposition or costing.
    """
    notes: List[str] = []
    result = compute_quote_item_pricing(item)
    quantity = item.quantity or 0

    item_name = _get_item_name(item)
    machine_name: Optional[str] = None
    stock_name: Optional[str] = None
    sheet_name: Optional[str] = None
    items_per_sheet: Optional[int] = None
    sheets_needed_val: Optional[int] = None
    paper_cost = Decimal("0")
    material_cost = Decimal("0")
    print_cost = Decimal("0")
    finishing_cost = Decimal("0")
    total_cost = Decimal(str(result.line_total)) if result.line_total else Decimal("0")

    if result.pricing_mode == PricingMode.SHEET:
        if item.paper_id and item.paper:
            paper = item.paper
            sheet_name = paper.sheet_size or None
            stock_name = result.paper_label or f"{paper.sheet_size} {paper.gsm}gsm {paper.get_paper_type_display()}"
            items_per_sheet = result.copies_per_sheet
            sheets_needed_val = result.sheets_needed

            if not sheet_name:
                notes.append("Sheet size not resolved from paper.")

            w_mm, h_mm = paper.get_dimensions_mm() if hasattr(paper, "get_dimensions_mm") else (paper.width_mm, paper.height_mm)
            if not w_mm or not h_mm:
                notes.append("Paper dimensions missing; imposition may be inaccurate.")
        else:
            notes.append("Paper not selected; sheet size and imposition cannot be resolved.")

        if item.machine_id and item.machine:
            machine_name = item.machine.name
        paper_cost = Decimal(str(result.paper_cost)) if result.paper_cost else Decimal("0")
        print_cost = Decimal(str(result.print_cost)) if result.print_cost else Decimal("0")
        finishing_cost = Decimal(str(result.finishing_total)) if result.finishing_total else Decimal("0")

    elif result.pricing_mode == PricingMode.LARGE_FORMAT:
        if item.material_id and item.material:
            stock_name = result.material_label or item.material.material_type
        if item.machine_id and item.machine:
            machine_name = item.machine.name
        material_cost = Decimal(str(result.material_cost)) if result.material_cost else Decimal("0")
        finishing_cost = Decimal(str(result.finishing_total)) if result.finishing_total else Decimal("0")

    if result.missing_fields:
        notes.append(f"Missing for pricing: {', '.join(result.missing_fields)}")
    if result.reason and not notes:
        notes.append(result.reason)

    finishing_lines = _finishing_lines_from_pricing(result)

    return QuoteItemSummary(
        item_name=item_name,
        quantity=quantity,
        machine_name=machine_name,
        stock_name=stock_name,
        sheet_name=sheet_name,
        items_per_sheet=items_per_sheet,
        sheets_needed=sheets_needed_val,
        paper_cost=paper_cost,
        material_cost=material_cost,
        print_cost=print_cost,
        finishing_cost=finishing_cost,
        total_cost=total_cost,
        finishing_lines=finishing_lines,
        notes=notes,
    )


def format_quote_item_summary(summary: QuoteItemSummary) -> str:
    """
    Convert QuoteItemSummary to human-readable text.
    Suitable for admin, debugging, and future frontend breakdown display.
    """
    lines: List[str] = []

    # Header
    lines.append(f"{summary.item_name} × {summary.quantity}")

    if summary.sheet_name:
        lines.append(f"  Sheet: {summary.sheet_name}")
    if summary.stock_name:
        lines.append(f"  Stock: {summary.stock_name}")
    if summary.machine_name:
        lines.append(f"  Machine: {summary.machine_name}")

    if summary.items_per_sheet is not None and summary.sheets_needed is not None:
        lines.append(f"  Imposition: {summary.items_per_sheet} up/sheet → {summary.sheets_needed} sheets")

    # Cost breakdown
    if summary.paper_cost > 0:
        lines.append(f"  Paper: {summary.paper_cost:,.0f}")
    if summary.material_cost > 0:
        lines.append(f"  Material: {summary.material_cost:,.0f}")
    lines.append(f"  Print: {summary.print_cost:,.0f}")
    lines.append(f"  Finishing: {summary.finishing_cost:,.0f}")

    for fl in summary.finishing_lines:
        if fl.total > 0:
            lines.append(f"    - {fl.name}: {fl.total:,.0f}")

    lines.append(f"  Total: {summary.total_cost:,.0f}")

    if summary.notes:
        lines.append("  Notes:")
        for n in summary.notes:
            lines.append(f"    - {n}")

    return "\n".join(lines)


def summary_to_breakdown_lines(summary: QuoteItemSummary) -> List[dict]:
    """
    Convert QuoteItemSummary to {label, amount} format for preview/API.
    Compatible with build_preview_price_response lines structure.
    """
    result: List[dict] = []
    if summary.items_per_sheet is not None and summary.sheets_needed is not None:
        result.append({
            "label": f"Sheets: {summary.sheets_needed} (×{summary.items_per_sheet} up)",
            "amount": "",
        })
    if summary.paper_cost > 0 and summary.stock_name:
        result.append({"label": f"Paper: {summary.stock_name}", "amount": f"{summary.paper_cost:,.0f}"})
    elif summary.material_cost > 0 and summary.stock_name:
        result.append({"label": f"Material: {summary.stock_name}", "amount": f"{summary.material_cost:,.0f}"})
    result.append({"label": "Print", "amount": f"{summary.print_cost:,.0f}"})
    for fl in summary.finishing_lines:
        if fl.total > 0:
            result.append({"label": f"Finishing: {fl.name}", "amount": f"{fl.total:,.0f}"})
    if summary.finishing_cost > 0 and not summary.finishing_lines:
        result.append({"label": "Finishing", "amount": f"{summary.finishing_cost:,.0f}"})
    # Caller adds Total (e.g. build_preview_price_response)
    return result
