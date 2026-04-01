"""Canonical draft/request/response workflow services."""

from django.db import transaction
from django.utils import timezone

from inventory.models import Machine, Paper
from locations.models import Location
from notifications.models import Notification
from notifications.services import notify
from pricing.models import FinishingRate, Material
from quotes.choices import QuoteDraftStatus, QuoteStatus, ShopQuoteStatus
from quotes.models import (
    QuoteDraft,
    QuoteItem,
    QuoteItemFinishing,
    QuoteRequest,
    QuoteRequestMessage,
    ShopQuote,
)
from shops.models import Shop


def _build_reference(prefix: str, instance_id: int) -> str:
    return f"{prefix}-{timezone.now():%Y%m%d}-{instance_id}"


def _coerce_positive_int(value):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _resolve_location(candidate):
    if not candidate:
        return None
    if isinstance(candidate, dict):
        candidate = candidate.get("id") or candidate.get("value") or candidate.get("pk") or candidate.get("slug")
    if isinstance(candidate, str) and not candidate.isdigit():
        return Location.objects.filter(slug=candidate).first()
    location_id = _coerce_positive_int(candidate)
    if not location_id:
        return None
    return Location.objects.filter(pk=location_id).first()


def _resolve_shop_resource(model, shop: Shop, candidate, *, active_only: bool = False):
    resource_id = _coerce_positive_int(candidate)
    if not resource_id:
        return None
    queryset = model.objects.filter(pk=resource_id, shop=shop)
    if active_only and hasattr(model, "is_active"):
        queryset = queryset.filter(is_active=True)
    return queryset.first()


def _resolve_product_for_shop(draft: QuoteDraft, shop: Shop):
    product = draft.selected_product
    if product and product.shop_id == shop.id:
        return product
    return None


def _extract_shop_preview(pricing_snapshot, shop: Shop):
    if not isinstance(pricing_snapshot, dict):
        return None
    selected_shops = pricing_snapshot.get("selected_shops")
    if isinstance(selected_shops, list):
        for entry in selected_shops:
            if not isinstance(entry, dict):
                continue
            if entry.get("id") == shop.id or entry.get("slug") == shop.slug:
                return entry
    return pricing_snapshot


def _build_item_spec_snapshot(*, draft: QuoteDraft, merged_request_details: dict, shop: Shop):
    return {
        "source": "calculator_draft_send",
        "draft_reference": draft.draft_reference,
        "calculator_inputs": draft.calculator_inputs_snapshot or {},
        "custom_product_snapshot": draft.custom_product_snapshot or {},
        "request_details": merged_request_details,
        "selected_shop": {
            "id": shop.id,
            "slug": shop.slug,
            "name": shop.name,
        },
    }


def _build_quote_item(*, quote_request: QuoteRequest, draft: QuoteDraft, shop: Shop, merged_request_details: dict) -> QuoteItem:
    calculator_inputs = draft.calculator_inputs_snapshot or {}
    custom_snapshot = draft.custom_product_snapshot or {}
    product = _resolve_product_for_shop(draft, shop)

    pricing_mode = (
        calculator_inputs.get("product_pricing_mode")
        or calculator_inputs.get("pricing_mode")
        or getattr(product, "pricing_mode", "")
        or ("LARGE_FORMAT" if calculator_inputs.get("material_id") else "SHEET")
    )
    paper = _resolve_shop_resource(Paper, shop, calculator_inputs.get("paper_id"), active_only=True)
    material = _resolve_shop_resource(Material, shop, calculator_inputs.get("material_id"), active_only=True)
    machine = _resolve_shop_resource(
        Machine,
        shop,
        calculator_inputs.get("machine_id") or getattr(product, "default_machine_id", None),
        active_only=True,
    )
    width_mm = _coerce_positive_int(
        calculator_inputs.get("width_mm")
        or custom_snapshot.get("width_mm")
        or getattr(product, "default_finished_width_mm", None)
    )
    height_mm = _coerce_positive_int(
        calculator_inputs.get("height_mm")
        or custom_snapshot.get("height_mm")
        or getattr(product, "default_finished_height_mm", None)
    )

    item = QuoteItem.objects.create(
        quote_request=quote_request,
        item_type="PRODUCT" if product else "CUSTOM",
        product=product,
        title=(product.name if product else custom_snapshot.get("custom_title") or calculator_inputs.get("custom_title") or draft.title or "Custom print job")[:120],
        spec_text=(custom_snapshot.get("custom_brief") or calculator_inputs.get("custom_brief") or merged_request_details.get("notes") or "")[:5000],
        has_artwork=True,
        quantity=_coerce_positive_int(calculator_inputs.get("quantity")) or 1,
        pricing_mode=pricing_mode if pricing_mode in {"SHEET", "LARGE_FORMAT"} else "SHEET",
        paper=paper,
        material=material,
        chosen_width_mm=width_mm,
        chosen_height_mm=height_mm,
        sides=calculator_inputs.get("print_sides") or calculator_inputs.get("sides") or getattr(product, "default_sides", "") or "SIMPLEX",
        color_mode=calculator_inputs.get("colour_mode") or calculator_inputs.get("color_mode") or "COLOR",
        machine=machine,
        special_instructions=(merged_request_details.get("notes") or custom_snapshot.get("custom_brief") or "")[:5000],
        pricing_snapshot=_extract_shop_preview(draft.pricing_snapshot, shop),
        item_spec_snapshot=_build_item_spec_snapshot(
            draft=draft,
            merged_request_details=merged_request_details,
            shop=shop,
        ),
        needs_review=(
            not product
            and not (custom_snapshot.get("custom_title") or calculator_inputs.get("custom_title") or draft.title)
        ) or (
            pricing_mode == "SHEET" and not paper
        ) or (
            pricing_mode == "LARGE_FORMAT" and (not material or not width_mm or not height_mm)
        ),
    )

    finishing_selections = calculator_inputs.get("finishings")
    if not isinstance(finishing_selections, list):
        finishing_selections = []
    for selection in finishing_selections:
        if not isinstance(selection, dict):
            continue
        finishing = _resolve_shop_resource(
            FinishingRate,
            shop,
            selection.get("finishing_rate_id") or selection.get("finishing_rate"),
            active_only=True,
        )
        if not finishing:
            continue
        selected_side = selection.get("selected_side")
        QuoteItemFinishing.objects.get_or_create(
            quote_item=item,
            finishing_rate=finishing,
            defaults={
                "selected_side": selected_side if selected_side in {"front", "back", "both"} else "both",
                "apply_to_sides": "DOUBLE" if selected_side == "both" else "SINGLE",
            },
        )

    return item


def _create_request_message(*, quote_request: QuoteRequest, sender, metadata: dict | None = None):
    return QuoteRequestMessage.objects.create(
        quote_request=quote_request,
        sender=sender,
        sender_role="client",
        message_kind="status",
        body="Request submitted to the shop.",
        metadata=metadata or {"status": QuoteStatus.SUBMITTED, "source": "calculator_draft_send"},
    )


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

    merged_request_details = {
        **(draft.request_details_snapshot or {}),
        **(request_details_snapshot or {}),
    }
    created_requests = []

    with transaction.atomic():
        for shop in shops:
            quote_request = QuoteRequest.objects.create(
                shop=shop,
                created_by=draft.user,
                customer_name=merged_request_details.get("customer_name") or getattr(draft.user, "name", "") or draft.user.email,
                customer_email=merged_request_details.get("customer_email") or draft.user.email,
                customer_phone=merged_request_details.get("customer_phone", ""),
                notes=merged_request_details.get("notes", ""),
                status=QuoteStatus.SUBMITTED,
                delivery_preference=merged_request_details.get("delivery_preference", ""),
                delivery_address=merged_request_details.get("delivery_address", ""),
                delivery_location=_resolve_location(merged_request_details.get("delivery_location")),
                source_draft=draft,
                request_snapshot={
                    "draft_reference": draft.draft_reference,
                    "calculator_inputs": draft.calculator_inputs_snapshot,
                    "pricing_snapshot": draft.pricing_snapshot,
                    "request_details": merged_request_details,
                    "custom_product_snapshot": draft.custom_product_snapshot,
                    "selected_shop": {"id": shop.id, "slug": shop.slug, "name": shop.name},
                },
            )
            quote_request.request_reference = _build_reference("QR", quote_request.id)
            quote_request.save(update_fields=["request_reference", "updated_at"])
            _build_quote_item(
                quote_request=quote_request,
                draft=draft,
                shop=shop,
                merged_request_details=merged_request_details,
            )
            _create_request_message(quote_request=quote_request, sender=draft.user)
            if shop.owner_id and shop.owner_id != draft.user.id:
                notify(
                    recipient=shop.owner,
                    notification_type=Notification.QUOTE_REQUEST_SUBMITTED,
                    message=f"New quote request #{quote_request.id} from {quote_request.customer_name or 'customer'}",
                    object_type="quote_request",
                    object_id=quote_request.id,
                    actor=draft.user,
                )
            created_requests.append(quote_request)
        draft.status = QuoteDraftStatus.SENT
        draft.save(update_fields=["status", "updated_at"])
    return created_requests


def _request_status_for_response_status(response_status: str) -> str:
    if response_status == ShopQuoteStatus.ACCEPTED:
        return QuoteStatus.QUOTED
    if response_status == ShopQuoteStatus.REJECTED:
        return QuoteStatus.REJECTED
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
