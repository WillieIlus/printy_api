"""Output adapter for the public calculator.

All numbers come from services.pricing.calculator_preview. This module only
projects that engine response into a stable, buyer-friendly response shape.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _money(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _first_money(mapping: dict, *keys: str) -> str | None:
    for key in keys:
        amount = _money(mapping.get(key))
        if amount is not None:
            return amount
    return None


def _preview_from_match(match: dict) -> dict:
    return _dict(match.get("preview")) or _dict(match.get("pricing_preview")) or match


def _total_from_preview(preview: dict) -> str | None:
    totals = _dict(preview.get("totals"))
    result = _dict(preview.get("calculation_result"))
    customer = _dict(preview.get("customer_pricing"))
    return (
        _first_money(totals, "grand_total", "client_price", "total_job_price", "subtotal")
        or _first_money(result, "grand_total", "subtotal")
        or _first_money(customer, "final_client_price", "estimated_total")
    )


def _unit_price(total: str | None, quantity: int, preview: dict) -> str | None:
    totals = _dict(preview.get("totals"))
    result = _dict(preview.get("calculation_result"))
    explicit = _first_money(totals, "unit_price") or _first_money(result, "unit_price")
    if explicit is not None:
        return explicit
    if total is None or quantity <= 0:
        return None
    return str((Decimal(total) / Decimal(quantity)).quantize(Decimal("0.01")))


def _production(preview: dict) -> dict:
    result = _dict(preview.get("calculation_result"))
    metadata = _dict(result.get("metadata"))
    result_imposition = _dict(metadata.get("imposition"))
    breakdown = _dict(preview.get("breakdown"))
    engine_imposition = _dict(breakdown.get("imposition"))

    copies = (
        preview.get("copies_per_sheet")
        or result_imposition.get("copies_per_sheet")
        or engine_imposition.get("copies_per_sheet")
    )
    good = (
        preview.get("good_sheets")
        or result_imposition.get("sheets_required")
        or engine_imposition.get("good_sheets")
    )
    billable = (
        engine_imposition.get("billable_sheets")
        or engine_imposition.get("parent_sheets_required")
        or preview.get("parent_sheets_required")
        or good
    )
    waste = engine_imposition.get("waste_sheets_added")
    if waste is None and billable is not None and good is not None:
        try:
            waste = max(0, int(billable) - int(good))
        except (TypeError, ValueError):
            waste = None

    return {
        "copies_per_sheet": copies,
        "good_sheets": good,
        "waste_sheets": waste,
        "billable_sheets": billable,
        "orientation": engine_imposition.get("orientation") or preview.get("rotated"),
        "parent_sheet": (
            preview.get("parent_sheet_name")
            or _dict(breakdown.get("paper")).get("sheet_size")
        ),
    }


def _buyer_lines(preview: dict) -> list[dict]:
    result = _dict(preview.get("calculation_result"))
    lines = _list(result.get("line_items"))
    projected = []
    for line in lines:
        line = _dict(line)
        if line.get("code") in {"vat"}:
            continue
        projected.append(
            {
                "code": line.get("code"),
                "label": line.get("label") or "Price line",
                "amount": _money(line.get("amount")),
                "formula": line.get("formula") or "",
            }
        )
    return projected


def build_client_quote_response(*, preview: dict, payload: dict) -> dict:
    """Build the response consumed by the React PriceRail component."""

    quantity = int(payload.get("quantity") or 0)
    matches = _list(preview.get("matches")) or _list(preview.get("selected_shops"))
    priced_matches = []
    source_previews = []
    for index, match in enumerate(matches, start=1):
        match = _dict(match)
        option_preview = _preview_from_match(match)
        total = _total_from_preview(option_preview)
        if total is None:
            continue
        source_previews.append((Decimal(total), option_preview))
        priced_matches.append(
            {
                "id": f"option-{index}",
                "label": match.get("option_label") or f"Production option {index}",
                "currency": option_preview.get("currency") or preview.get("currency") or "KES",
                "grand_total": total,
                "unit_price": _unit_price(total, quantity, option_preview),
                "can_price_now": bool(match.get("can_price_now", True)),
                "summary": match.get("summary") or "Verified production capacity",
                "production": _production(option_preview),
            }
        )

    priced_matches.sort(key=lambda row: Decimal(row["grand_total"]))
    primary_preview = (
        min(source_previews, key=lambda row: row[0])[1]
        if source_previews
        else _dict(preview.get("pricing_preview")) or preview
    )
    total = _total_from_preview(primary_preview)
    if priced_matches:
        total = priced_matches[0]["grand_total"]

    result = _dict(primary_preview.get("calculation_result"))
    totals = _dict(primary_preview.get("totals"))
    exact = bool(
        primary_preview.get("exact_or_estimated")
        or preview.get("exact_or_estimated")
    )

    service_confirmation = []
    if payload.get("artwork_service") not in (None, "", "print_ready"):
        service_confirmation.append("artwork_service")
    if payload.get("delivery_method") not in (None, "", "pickup"):
        service_confirmation.append("delivery")

    return {
        "version": 1,
        "currency": primary_preview.get("currency") or preview.get("currency") or "KES",
        "price_status": "rate_card_exact" if exact and not service_confirmation else "estimate",
        "can_calculate": bool(total) and bool(primary_preview.get("can_calculate", True)),
        "reason": primary_preview.get("reason") or preview.get("reason") or "",
        "quantity": quantity,
        "total": {
            "subtotal": _first_money(totals, "subtotal"),
            "grand_total": total,
            "unit_price": _unit_price(total, quantity, primary_preview),
        },
        "production": _production(primary_preview),
        "line_items": _buyer_lines(primary_preview),
        "options": priced_matches,
        "warnings": _list(result.get("warnings")),
        "assumptions": _list(result.get("assumptions")),
        "needs_confirmation": service_confirmation,
        "input": payload,
        "handoff": {
            "save_draft": "/api/client-calculator/drafts/",
            "claim_guest_draft": "/api/calculator/drafts/claim/",
            "send_authenticated_draft": "/api/calculator/drafts/{draft_id}/send/",
        },
    }