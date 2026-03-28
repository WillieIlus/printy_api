"""Canonical draft/request/response workflow services."""

from django.utils import timezone

from quotes.choices import QuoteDraftStatus, QuoteStatus, ShopQuoteStatus
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


def update_quote_draft(
    *,
    draft: QuoteDraft,
    title: str | None = None,
    shop=None,
    selected_product=None,
    calculator_inputs_snapshot: dict | None = None,
    pricing_snapshot: dict | None = None,
    custom_product_snapshot: dict | None = None,
    request_details_snapshot: dict | None = None,
) -> QuoteDraft:
    if draft.status != QuoteDraftStatus.DRAFT:
        raise ValueError("Only draft quote drafts can be updated.")

    if title is not None:
        draft.title = title
    if shop is not None:
        draft.shop = shop
    if selected_product is not None:
        draft.selected_product = selected_product
    if calculator_inputs_snapshot is not None:
        draft.calculator_inputs_snapshot = calculator_inputs_snapshot
    if pricing_snapshot is not None:
        draft.pricing_snapshot = pricing_snapshot
    if custom_product_snapshot is not None:
        draft.custom_product_snapshot = custom_product_snapshot
    if request_details_snapshot is not None:
        draft.request_details_snapshot = request_details_snapshot
    draft.save()
    return draft


def send_quote_draft_to_shops(*, draft: QuoteDraft, shops: list[Shop], request_details_snapshot: dict | None = None) -> list[QuoteRequest]:
    if draft.status != QuoteDraftStatus.DRAFT:
        raise ValueError("Only draft quote drafts can be sent.")

    created_requests = []
    for shop in shops:
        quote_request = QuoteRequest.objects.create(
            shop=shop,
            created_by=draft.user,
            customer_name=(request_details_snapshot or {}).get("customer_name") or getattr(draft.user, "name", "") or draft.user.email,
            customer_email=(request_details_snapshot or {}).get("customer_email") or draft.user.email,
            customer_phone=(request_details_snapshot or {}).get("customer_phone", ""),
            notes=(request_details_snapshot or {}).get("notes", ""),
            status=QuoteStatus.SUBMITTED,
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
    draft.status = QuoteDraftStatus.SENT
    draft.save(update_fields=["status", "updated_at"])
    return created_requests


def _request_status_for_response_status(response_status: str) -> str:
    if response_status == ShopQuoteStatus.ACCEPTED:
        return QuoteStatus.ACCEPTED
    if response_status == ShopQuoteStatus.REJECTED:
        return QuoteStatus.CLOSED
    return QuoteStatus.QUOTED


def _assert_response_transition(current_status: str | None, next_status: str):
    allowed = {
        None: {ShopQuoteStatus.PENDING, ShopQuoteStatus.MODIFIED, ShopQuoteStatus.ACCEPTED, ShopQuoteStatus.REJECTED},
        ShopQuoteStatus.PENDING: {ShopQuoteStatus.PENDING, ShopQuoteStatus.MODIFIED, ShopQuoteStatus.ACCEPTED, ShopQuoteStatus.REJECTED},
        ShopQuoteStatus.MODIFIED: {ShopQuoteStatus.MODIFIED, ShopQuoteStatus.ACCEPTED, ShopQuoteStatus.REJECTED},
        ShopQuoteStatus.ACCEPTED: set(),
        ShopQuoteStatus.REJECTED: set(),
    }
    if next_status not in allowed.get(current_status, set()):
        raise ValueError(f"Cannot change quote response from {current_status or 'new'} to {next_status}.")


def create_quote_response(*, quote_request: QuoteRequest, shop, user, status: str, response_snapshot: dict, revised_pricing_snapshot: dict | None = None, total=None, note: str = "", turnaround_days=None) -> ShopQuote:
    _assert_response_transition(None, status)
    response = ShopQuote.objects.create(
        quote_request=quote_request,
        shop=shop,
        created_by=user,
        status=status,
        total=total,
        sent_at=timezone.now() if status != ShopQuoteStatus.PENDING else None,
        note=note,
        turnaround_days=turnaround_days,
        revision_number=quote_request.shop_quotes.count() + 1,
        response_snapshot=response_snapshot,
        revised_pricing_snapshot=revised_pricing_snapshot,
    )
    response.quote_reference = _build_reference("QS", response.id)
    response.save(update_fields=["quote_reference", "updated_at"])
    quote_request.status = _request_status_for_response_status(status)
    quote_request.save(update_fields=["status", "updated_at"])
    return response


def update_quote_response(
    *,
    response: ShopQuote,
    status: str,
    response_snapshot: dict | None = None,
    revised_pricing_snapshot: dict | None = None,
    total=None,
    note: str | None = None,
    turnaround_days=None,
) -> ShopQuote:
    _assert_response_transition(response.status, status)

    response.status = status
    if response_snapshot is not None:
        response.response_snapshot = response_snapshot
    if revised_pricing_snapshot is not None:
        response.revised_pricing_snapshot = revised_pricing_snapshot
    if total is not None:
        response.total = total
    if note is not None:
        response.note = note
    if turnaround_days is not None:
        response.turnaround_days = turnaround_days
    if status != ShopQuoteStatus.PENDING and response.sent_at is None:
        response.sent_at = timezone.now()
    response.save()

    quote_request = response.quote_request
    quote_request.status = _request_status_for_response_status(status)
    quote_request.save(update_fields=["status", "updated_at"])
    return response
