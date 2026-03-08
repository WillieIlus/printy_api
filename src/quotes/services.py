"""
Quote engine: calculate_quote_request.
Uses cost_quote_item from costing for item-level pricing.
"""
from decimal import Decimal
from django.utils import timezone

from .costing import cost_quote_item


def calculate_quote_request(quote_request, lock=False, force_recalc=False):
    total = Decimal("0.00")

    for item in quote_request.items.select_related(
        "paper", "material", "machine", "product"
    ).prefetch_related("finishings__finishing_rate"):
        if item.pricing_locked_at and not force_recalc:
            if item.line_total is not None:
                total += item.line_total
            continue

        result = cost_quote_item(item)
        total += result.total_cost

        if lock:
            item.unit_price = result.unit_price
            item.line_total = result.total_cost
            item.pricing_locked_at = timezone.now()
            item.save(update_fields=["unit_price", "line_total", "pricing_locked_at", "updated_at"])

    if lock:
        quote_request.total = total
        quote_request.pricing_locked_at = timezone.now()
        quote_request.save(update_fields=["total", "pricing_locked_at", "updated_at"])

    return total
