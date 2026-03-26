"""Canonical draft/request/response workflow services."""

from django.utils import timezone

from quotes.models import QuoteDraft, QuoteRequest, ShopQuote
from shops.models import Shop


def _build_reference(prefix: str, instance_id: int) -> str:
    return f"{prefix}-{timezone.now():%Y%m%d}-{instance_id}"


def save_quote_draft(*, user, selected_product=None, shop=None, title: str = "", calculator_inputs_snapshot: dict, pricing_snapshot: dict | None = None, custom_product_snapshot: dict | None = None, request_details_snapshot: dict | None = None) -> QuoteDraft:
    draft = QuoteDraft.objects.create(
        user=user,
        shop=shop,
        selected_product=selected_product,
        title=title,
        calculator_inputs_snapshot=calculator_inputs_snapshot,
        pricing_snapshot=pricing_snapshot,
        custom_product_snapshot=custom_product_snapshot,
        request_details_snapshot=request_details_snapshot,
    )
    draft.draft_reference = _build_reference("QD", draft.id)
    draft.save(update_fields=["draft_reference", "updated_at"])
    return draft


def send_quote_draft_to_shops(*, draft: QuoteDraft, shops: list[Shop], request_details_snapshot: dict | None = None) -> list[QuoteRequest]:
    created_requests = []
    for shop in shops:
        quote_request = QuoteRequest.objects.create(
            shop=shop,
            created_by=draft.user,
            customer_name=(request_details_snapshot or {}).get("customer_name") or getattr(draft.user, "name", "") or draft.user.email,
            customer_email=(request_details_snapshot or {}).get("customer_email") or draft.user.email,
            customer_phone=(request_details_snapshot or {}).get("customer_phone", ""),
            notes=(request_details_snapshot or {}).get("notes", ""),
            status=QuoteRequest.SUBMITTED,
            source_draft=draft,
            request_snapshot={
                "draft_reference": draft.draft_reference,
                "calculator_inputs": draft.calculator_inputs_snapshot,
                "pricing_snapshot": draft.pricing_snapshot,
                "request_details": request_details_snapshot or draft.request_details_snapshot or {},
                "custom_product_snapshot": draft.custom_product_snapshot,
            },
        )
        quote_request.request_reference = _build_reference("QR", quote_request.id)
        quote_request.save(update_fields=["request_reference", "updated_at"])
        created_requests.append(quote_request)
    draft.status = QuoteDraft.Status.SENT
    draft.save(update_fields=["status", "updated_at"])
    return created_requests


def create_quote_response(*, quote_request: QuoteRequest, shop, user, status: str, response_snapshot: dict, revised_pricing_snapshot: dict | None = None, total=None, note: str = "", turnaround_days=None) -> ShopQuote:
    response = ShopQuote.objects.create(
        quote_request=quote_request,
        shop=shop,
        created_by=user,
        status=status,
        total=total,
        sent_at=timezone.now() if status != ShopQuote.PENDING else None,
        note=note,
        turnaround_days=turnaround_days,
        response_snapshot=response_snapshot,
        revised_pricing_snapshot=revised_pricing_snapshot,
    )
    response.quote_reference = _build_reference("QS", response.id)
    response.save(update_fields=["quote_reference", "updated_at"])
    return response
