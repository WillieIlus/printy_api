"""
Quote summary builders — preview, totals, line breakdowns.
"""
from decimal import Decimal
from typing import Any


def build_line_breakdown(item) -> list[dict[str, Any]]:
    """
    Build breakdown lines for quote items using the summary layer.
    Returns list of {label, amount} for frontend.
    """
    from quotes.services import _build_item_breakdown_lines

    return _build_item_breakdown_lines(item)


def build_quote_preview(
    quote_request,
    total: Decimal,
    lines: list[dict],
    can_calculate: bool,
    reason: str = "",
    missing_fields: list[str] | None = None,
    needs_review_items: list[int] | None = None,
    item_diagnostics: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Build standardized quote preview response."""
    currency = getattr(quote_request.shop, "currency", "KES") or "KES"
    return {
        "currency": currency,
        "total": float(total),
        "lines": lines,
        "can_calculate": can_calculate,
        "reason": reason,
        "missing_fields": sorted(missing_fields or []),
        "needs_review_items": needs_review_items or [],
        "item_diagnostics": item_diagnostics or {},
    }


def format_line_total(amount: Decimal) -> str:
    """Format line total for display."""
    return f"{amount:,.0f}" if amount else ""
