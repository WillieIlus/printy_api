"""Visibility-safe pricing breakdown projections."""

from __future__ import annotations

from decimal import Decimal

from common.money import money
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _money(value: Any) -> str:
    try:
        return str(money(value or "0"))
    except Exception:
        return "0.00"


def _num(value: Any) -> int | str:
    try:
        amount = Decimal(str(value or "0"))
    except Exception:
        return str(value or "")
    return int(amount) if amount == amount.to_integral_value() else str(amount.normalize())


def _component_line(
    *,
    component: str,
    label: Any,
    total: Any,
    quantity: Any = None,
    unit: Any = "",
    unit_price: Any = None,
    spec: Any = "",
    source: str = "shop_rate",
) -> dict[str, Any]:
    quantity_value = _num(quantity) if quantity not in (None, "") else None
    unit_price_value = _money(unit_price if unit_price is not None else total)
    total_value = _money(total)
    return {
        "component": component,
        "label": str(label or component.replace("_", " ").title()),
        "spec": str(spec or ""),
        "quantity": quantity_value,
        "unit": str(unit or ""),
        "unit_price_kes": unit_price_value,
        "total_kes": total_value,
        "source": source,
        # Backward-compatible display keys for the existing manager UI.
        "qty": quantity_value if quantity_value is not None else "",
        "rate": unit_price_value,
        "total": total_value,
    }


def _rate_from_total(total: Any, quantity: Any) -> Any:
    if quantity in (None, "", 0, "0"):
        return total
    try:
        return Decimal(str(total)) / Decimal(str(quantity))
    except Exception:
        return total


def line_items_from_preview(preview: dict[str, Any]) -> list[dict[str, Any]]:
    breakdown = _as_dict(preview.get("breakdown"))
    totals = _as_dict(preview.get("totals"))
    items: list[dict[str, Any]] = []
    saw_stock_printing = False

    paper = _as_dict(breakdown.get("paper"))
    paper_total = paper.get("total") or totals.get("paper_cost")
    if paper_total is not None:
        qty = paper.get("quantity") or preview.get("billable_sheets") or preview.get("good_sheets") or preview.get("parent_sheets_required")
        items.append(
            _component_line(
                component="paper",
                label=paper.get("label") or paper.get("name") or "Paper",
                spec=paper.get("sheet_size") or "",
                quantity=qty,
                unit="sheet",
                unit_price=paper.get("unit_price") or paper.get("paper_price_per_sheet") or paper.get("paper_price") or _rate_from_total(paper_total, qty),
                total=paper_total,
                source="shop_rate",
            )
        )
        saw_stock_printing = True

    printing = _as_dict(breakdown.get("printing") or breakdown.get("print"))
    print_total = printing.get("total") or totals.get("print_cost")
    if print_total is not None:
        qty = printing.get("quantity") or preview.get("billable_sheets") or preview.get("good_sheets") or preview.get("parent_sheets_required")
        print_spec = " ".join(
            part for part in [printing.get("color_mode"), printing.get("sides"), printing.get("machine_name")] if part
        )
        items.append(
            _component_line(
                component="printing",
                label=printing.get("label") or "Printing",
                spec=print_spec,
                quantity=qty,
                unit="sheet",
                unit_price=printing.get("unit_price") or printing.get("rate_per_sheet") or printing.get("print_total_per_sheet") or _rate_from_total(print_total, qty),
                total=print_total,
                source="shop_rate",
            )
        )
        saw_stock_printing = True

    # Law 11.3 / prompt item 3 — the manager quote shows ONE combined
    # stock + printing line (the rate card's "Total 1s" / "Total 2s"), never a
    # separate paper line beside a separate printing line. The engine already
    # resolved stock_printing_total, and it equals paper_cost + print_cost, so
    # the lines still sum to the printer's quoted subtotal.
    if saw_stock_printing:
        merged = stock_printing_line_from_preview(preview)
        if merged is not None:
            replaced = [index for index, item in enumerate(items) if item.get("component") in ("paper", "printing")]
            for index in reversed(replaced):
                items.pop(index)
            items.insert(replaced[0] if replaced else 0, merged)

    for finishing in breakdown.get("finishings") or []:
        if not isinstance(finishing, dict):
            continue
        total = finishing.get("total")
        if total is None:
            continue
        label = finishing.get("label") or finishing.get("name") or "Finishing"
        component = "cutting" if "cut" in str(label).lower() or "cut" in str(finishing.get("slug") or "").lower() else "finishing"
        items.append(
            _component_line(
                component=component,
                label=label,
                spec=finishing.get("selected_side") or finishing.get("calculation_basis") or "",
                quantity=finishing.get("quantity") or finishing.get("units") or finishing.get("units_count") or 1,
                unit=finishing.get("unit") or finishing.get("billing_basis") or "job",
                unit_price=finishing.get("unit_price") or finishing.get("rate") or total,
                total=total,
                source="shop_rate",
            )
        )

    optional_components = [
        ("setup", "Setup", "policy"),
        ("waste", "Waste", "policy"),
        ("quantity_tier", "Quantity tier", "policy"),
        ("misc", "Misc", "manual"),
    ]
    for component, default_label, source in optional_components:
        block = _as_dict(breakdown.get(component))
        total = block.get("total") or block.get("cost") or block.get(f"{component}_cost")
        if total is None:
            continue
        items.append(
            _component_line(
                component=component,
                label=block.get("label") or block.get("name") or default_label,
                spec=block.get("formula") or block.get("explanation") or "",
                quantity=block.get("quantity") or block.get("units") or 1,
                unit=block.get("unit") or "job",
                unit_price=block.get("unit_price") or block.get("rate") or total,
                total=total,
                source=source,
            )
        )
    return items


_SIDES_LABELS = {
    "DUPLEX": "both sides",
    "DOUBLE_SIDED": "both sides",
    "BOTH": "both sides",
    "SIMPLEX": "single side",
    "SINGLE_SIDED": "single side",
    "ONE_SIDED": "single side",
}


def stock_printing_line_from_preview(preview: dict[str, Any]) -> dict[str, Any] | None:
    """The ONE paper + printing line the manager quote shows (law 11.3).

    The rate card renders "Total 1s" / "Total 2s" per sheet, and the quote's
    price uses exactly that figure. Rather than re-adding a separate paper line
    and printing line on the client side, the projection publishes the combined
    stock + printing figure the engine already resolved, so both screens read
    from one number.
    """
    totals = _as_dict(preview.get("totals"))
    per_sheet = totals.get("stock_printing_total_per_sheet")
    total = totals.get("stock_printing_total")
    if per_sheet in (None, "") or total in (None, ""):
        return None
    breakdown = _as_dict(preview.get("breakdown"))
    paper = _as_dict(breakdown.get("paper"))
    printing = _as_dict(breakdown.get("printing") or breakdown.get("print"))
    sheets = preview.get("billable_sheets") or preview.get("good_sheets") or preview.get("parent_sheets_required")
    sides_label = _SIDES_LABELS.get(str(printing.get("sides") or "").upper(), "")
    spec = ", ".join(part for part in [sides_label, f"{_num(sheets)} sheets" if sheets else ""] if part)
    # "SRA3 300gsm Matte, both sides, 8 sheets" — sheet size rides with the
    # stock label so the merged line still says exactly which stock was used.
    label = paper.get("label") or paper.get("name") or "Paper + printing"
    sheet_size = str(paper.get("sheet_size") or "").strip()
    if sheet_size and sheet_size.lower() not in str(label).lower():
        label = f"{sheet_size} {label}"
    return _component_line(
        component="stock_printing",
        label=label,
        spec=spec,
        quantity=sheets,
        unit="sheet",
        unit_price=per_sheet,
        total=total,
        source="shop_rate",
    )


def production_breakdown_from_preview(preview: dict[str, Any]) -> dict[str, Any]:
    totals = _as_dict(preview.get("totals"))
    line_items = line_items_from_preview(preview)
    production_cost = _money(totals.get("subtotal") or totals.get("grand_total"))
    line_total = sum((Decimal(item["total_kes"]) for item in line_items), Decimal("0.00"))
    reconciles = line_total == Decimal(production_cost)
    stock_printing_line = stock_printing_line_from_preview(preview)
    return {
        "pieces_per_sheet": preview.get("copies_per_sheet"),
        "sheets_needed": preview.get("good_sheets"),
        "line_items": line_items,
        "breakdown": line_items,
        "production_cost": production_cost,
        # law 11.3 — the combined stock + printing figure (“Total 1s / Total 2s”)
        # the printer rate-card table shows, and the line the quote renders.
        "stock_printing_total_per_sheet": totals.get("stock_printing_total_per_sheet"),
        "stock_printing_total": totals.get("stock_printing_total"),
        "stock_printing_sheets": stock_printing_line.get("quantity") if stock_printing_line else None,
        "stock_printing_line": stock_printing_line,
        "breakdown_total_kes": _money(line_total),
        "breakdown_reconciles": reconciles,
        "non_reconciling_policy_values": [] if reconciles else ["vat_or_policy_adjustment"],
    }
