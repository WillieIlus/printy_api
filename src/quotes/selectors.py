"""Quote query selectors — read-only data access patterns."""
from django.db.models import Prefetch, Q

from quotes.models import QuoteItem, QuoteRequest


def get_quote_request_with_items(quote_request_id: int):
    """Prefetch quote request with items, finishings, and related FKs."""
    items_qs = QuoteItem.objects.select_related(
        "paper", "material", "machine", "product"
    ).prefetch_related("finishings__finishing_rate", "services__service_rate")
    return QuoteRequest.objects.filter(pk=quote_request_id).prefetch_related(
        Prefetch("items", queryset=items_qs)
    ).first()


def get_quote_requests_for_user(user):
    """Quote requests visible to user (buyer or seller)."""
    return QuoteRequest.objects.filter(
        Q(created_by=user) | Q(shop__owner=user)
    ).select_related("shop", "created_by").prefetch_related("items")


def get_quote_items_for_quote(quote_request):
    """Items for a quote with all pricing-related FKs."""
    return quote_request.items.select_related(
        "paper", "material", "machine", "product"
    ).prefetch_related("finishings__finishing_rate", "services__service_rate")
