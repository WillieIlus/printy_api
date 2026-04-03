"""Quote builder helpers for shop-scoped client draft actions."""

from django.db import transaction
from django.utils import timezone

from notifications.models import Notification
from notifications.services import notify
from quotes.choices import QuoteStatus
from quotes.models import QuoteItem, QuoteItemFinishing, QuoteRequest, QuoteRequestMessage


def _build_reference(prefix: str, instance_id: int) -> str:
    return f"{prefix}-{timezone.now():%Y%m%d}-{instance_id}"


def _clone_quote_item_to_request(*, source_item: QuoteItem, quote_request: QuoteRequest) -> QuoteItem:
    cloned_item = QuoteItem.objects.create(
        quote_request=quote_request,
        shop_quote=None,
        item_type=source_item.item_type,
        title=source_item.title,
        spec_text=source_item.spec_text,
        has_artwork=source_item.has_artwork,
        product=source_item.product,
        quantity=source_item.quantity,
        pricing_mode=source_item.pricing_mode,
        paper=source_item.paper,
        material=source_item.material,
        chosen_width_mm=source_item.chosen_width_mm,
        chosen_height_mm=source_item.chosen_height_mm,
        sides=source_item.sides,
        color_mode=source_item.color_mode,
        machine=source_item.machine,
        special_instructions=source_item.special_instructions,
        unit_price=source_item.unit_price,
        line_total=source_item.line_total,
        pricing_snapshot=source_item.pricing_snapshot,
        pricing_locked_at=source_item.pricing_locked_at,
        item_spec_snapshot=source_item.item_spec_snapshot,
        needs_review=source_item.needs_review,
    )
    QuoteItemFinishing.objects.bulk_create(
        [
            QuoteItemFinishing(
                quote_item=cloned_item,
                finishing_rate=finishing.finishing_rate,
                coverage_qty=finishing.coverage_qty,
                price_override=finishing.price_override,
                apply_to_sides=finishing.apply_to_sides,
                selected_side=finishing.selected_side,
            )
            for finishing in source_item.finishings.select_related("finishing_rate").all()
        ]
    )
    return cloned_item


def send_quote_request_item_to_shop(*, draft: QuoteRequest, item: QuoteItem, user) -> QuoteRequest:
    if draft.created_by_id != user.id:
        raise ValueError("You do not own this quote draft.")
    if draft.status != QuoteStatus.DRAFT:
        raise ValueError("Only draft quote requests can send individual items.")
    if item.quote_request_id != draft.id:
        raise ValueError("This item does not belong to the selected draft.")

    with transaction.atomic():
        single_request = QuoteRequest.objects.create(
            shop=draft.shop,
            created_by=user,
            customer_name=draft.customer_name,
            customer_email=draft.customer_email,
            customer_phone=draft.customer_phone,
            status=QuoteStatus.SUBMITTED,
            notes=draft.notes,
            customer_inquiry=draft.customer_inquiry,
            customer=draft.customer,
            delivery_address=draft.delivery_address,
            delivery_location=draft.delivery_location,
            delivery_preference=draft.delivery_preference,
            quote_draft_file=draft.quote_draft_file,
            source_draft=draft.source_draft,
            request_snapshot={
                "source": "single_item_submit",
                "source_draft_id": draft.id,
                "source_item_id": item.id,
            },
        )
        single_request.request_reference = _build_reference("QR", single_request.id)
        single_request.save(update_fields=["request_reference", "updated_at"])
        _clone_quote_item_to_request(source_item=item, quote_request=single_request)
        QuoteRequestMessage.objects.create(
            quote_request=single_request,
            sender=user,
            sender_role="client",
            message_kind="status",
            body="Request submitted to the shop.",
            metadata={
                "status": QuoteStatus.SUBMITTED,
                "source": "single_item_submit",
                "source_draft_id": draft.id,
                "source_item_id": item.id,
            },
        )
        if draft.shop.owner_id and draft.shop.owner_id != user.id:
            notify(
                recipient=draft.shop.owner,
                notification_type=Notification.QUOTE_REQUEST_SUBMITTED,
                message=f"New quote request #{single_request.id} from {single_request.customer_name or 'customer'}",
                object_type="quote_request",
                object_id=single_request.id,
                actor=user,
            )
        item.delete()
        draft.save(update_fields=["updated_at"])
        return single_request
