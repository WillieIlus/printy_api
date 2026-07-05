from __future__ import annotations

from typing import Any
from decimal import Decimal


def _money(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except (ValueError, TypeError, ArithmeticError):
        return None


def project_public_pricing(raw_payload: dict[str, Any], market_range: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Public projection: market range min/max, confidence, production summary only.
    No shop names, no slugs, no per-sheet rates, no formula, no raw preview blobs.
    """
    mr = market_range or {}
    return {
        "market_range": {
            "min": _money(mr.get("min")),
            "max": _money(mr.get("max")),
            "confidence": mr.get("confidence") or "estimated",
        },
        "production_summary": _extract_production_summary(raw_payload),
    }


def project_client_projection(raw_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Client projection: total, turnaround, safe line items (Print & finishing, Printy fee, Delivery).
    No pricing_snapshot, no response_snapshot, no shop identity.
    """
    totals = raw_payload.get("totals") or {}
    return {
        "total": _money(totals.get("grand_total")),
        "turnaround": raw_payload.get("turnaround_label") or raw_payload.get("human_ready_text"),
        "line_items": _extract_client_line_items(raw_payload),
    }


def project_broker_projection(raw_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Broker projection: production_estimate, sheets_needed phrased as "(N sheets at verified rates)",
    gross_margin slot, client_price, broker payout. No raw rate card values.
    """
    totals = raw_payload.get("totals") or {}
    breakdown = raw_payload.get("breakdown") or {}
    imposition = breakdown.get("imposition") or {}
    sheets_count = imposition.get("good_sheets") or imposition.get("parent_sheets_required") or 0
    
    return {
        "production_estimate": _money(totals.get("subtotal")),
        "sheets_needed_label": f"({sheets_count} sheets at verified rates)",
        "gross_margin": None,
        "client_price": None,
        "broker_payout": None,
    }


def project_shop_projection(raw_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Shop projection: exact payout, sheet count, paper spec, finishing spec, machine.
    No client price, no broker commission.
    """
    totals = raw_payload.get("totals") or {}
    breakdown = raw_payload.get("breakdown") or {}
    imposition = breakdown.get("imposition") or {}
    paper = breakdown.get("paper") or {}
    printing = breakdown.get("printing") or {}

    return {
        "payout": _money(totals.get("subtotal")), # Shop gets subtotal (before platform fees/markups)
        "sheet_count": imposition.get("good_sheets") or imposition.get("parent_sheets_required"),
        "paper_spec": paper.get("label") or paper.get("sheet_size"),
        "finishing_spec": [f.get("name") for f in breakdown.get("finishings", []) if f.get("name")],
        "machine": printing.get("machine_name"),
    }


def project_ops_projection(raw_payload: dict[str, Any]) -> dict[str, Any]:
    """
    Ops projection: everything, full audit trail.
    """
    return raw_payload


def _extract_production_summary(raw_payload: dict[str, Any]) -> str:
    breakdown = raw_payload.get("breakdown") or {}
    imposition = breakdown.get("imposition") or {}
    if raw_payload.get("pricing_mode") == "SHEET":
        sheets = imposition.get("good_sheets") or imposition.get("parent_sheets_required") or 0
        return f"{sheets} sheets production"
    dimensions = breakdown.get("dimensions") or {}
    area = dimensions.get("area_sqm") or 0
    return f"{area} sqm production"


def _extract_client_line_items(raw_payload: dict[str, Any]) -> list[dict[str, Any]]:
    totals = raw_payload.get("totals") or {}
    # Safe line items: Print & finishing, Printy fee, Delivery
    # Note: Printy fee and Delivery are usually added at the builder or serializer level,
    # but we can provide placeholders or extract if present in raw_payload.
    items = []
    
    # Combined Print & Finishing
    subtotal = totals.get("subtotal")
    if subtotal:
        items.append({"label": "Print and finishing", "amount": _money(subtotal)})
    
    # We don't have printy fee in raw engine payload usually (it's internal to engine as subtotal components)
    # But if we are projecting a calculated result that has them:
    if "printy_fee" in totals:
         items.append({"label": "Printy fee", "amount": _money(totals.get("printy_fee"))})
    if "delivery_fee" in totals:
         items.append({"label": "Delivery", "amount": _money(totals.get("delivery_fee"))})
         
    return items
