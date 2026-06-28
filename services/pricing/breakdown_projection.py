"""Visibility-safe pricing breakdown projections."""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _money(value: Any) -> str:
    try:
        return str(Decimal(str(value or "0")).quantize(Decimal("0.01")))
    except Exception:
        return "0.00"


def _num(value: Any) -> int | str:
    try:
        amount = Decimal(str(value or "0"))
    except Exception:
        return str(value or "")
    return int(amount) if amount == amount.to_integral_value() else str(amount.normalize())


def line_items_from_preview(preview: dict[str, Any]) -> list[dict[str, Any]]:
    breakdown = _as_dict(preview.get("breakdown"))
    totals = _as_dict(preview.get("totals"))
    items: list[dict[str, Any]] = []

    paper = _as_dict(breakdown.get("paper"))
    paper_total = paper.get("total") or totals.get("paper_cost")
    if paper_total is not None:
        qty = paper.get("quantity") or preview.get("good_sheets")
        rate = paper.get("unit_price")
        if rate is None and qty:
            try:
                rate = Decimal(str(paper_total)) / Decimal(str(qty))
            except Exception:
                rate = paper_total
        items.append(
            {
                "label": paper.get("label") or paper.get("name") or "Paper",
                "qty": _num(qty),
                "unit": "sheet",
                "rate": _money(rate),
                "total": _money(paper_total),
            }
        )

    printing = _as_dict(breakdown.get("printing") or breakdown.get("print"))
    print_total = printing.get("total") or totals.get("print_cost")
    if print_total is not None:
        qty = printing.get("quantity") or preview.get("good_sheets")
        rate = printing.get("unit_price")
        if rate is None and qty:
            try:
                rate = Decimal(str(print_total)) / Decimal(str(qty))
            except Exception:
                rate = print_total
        items.append(
            {
                "label": printing.get("label") or "Printing",
                "qty": _num(qty),
                "unit": "sheet",
                "rate": _money(rate),
                "total": _money(print_total),
            }
        )

    for finishing in breakdown.get("finishings") or []:
        if not isinstance(finishing, dict):
            continue
        total = finishing.get("total")
        items.append(
            {
                "label": finishing.get("label") or finishing.get("name") or "Finishing",
                "qty": _num(finishing.get("quantity") or finishing.get("units") or 1),
                "unit": finishing.get("unit") or finishing.get("billing_basis") or "job",
                "rate": _money(finishing.get("unit_price") or finishing.get("rate") or total),
                "total": _money(total),
            }
        )
    return items


def production_breakdown_from_preview(preview: dict[str, Any]) -> dict[str, Any]:
    totals = _as_dict(preview.get("totals"))
    return {
        "pieces_per_sheet": preview.get("copies_per_sheet"),
        "sheets_needed": preview.get("good_sheets"),
        "line_items": line_items_from_preview(preview),
        "production_cost": _money(totals.get("subtotal") or totals.get("grand_total")),
    }
