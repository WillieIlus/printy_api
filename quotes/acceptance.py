from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.services.roles import is_broker
from payments.services import create_payment_for_quote
from pricing.services.platform_fee_policy import ensure_quote_financial_split
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote


def _can_accept(quote: Quote, user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    quote_request = quote.quote_request
    if quote_request.created_by_id == user.id and not is_broker(user):
        return True
    if quote_request.on_behalf_of_id == user.id:
        return True
    return False


@transaction.atomic
def accept_quote_for_payment(*, quote: Quote, accepted_by, payer_phone: str = ""):
    quote = Quote.objects.select_for_update().select_related("quote_request").get(pk=quote.pk)
    if not _can_accept(quote, accepted_by):
        raise ValidationError("You cannot accept this quote.")
    if quote.is_expired:
        raise ValidationError("This quote has expired.")
    if quote.status not in {
        QuoteOfferStatus.SENT,
        QuoteOfferStatus.REVISED,
        QuoteOfferStatus.MODIFIED,
        QuoteOfferStatus.ACCEPTED,
    }:
        raise ValidationError("Only sent or revised quotes can be accepted.")
    ensure_quote_financial_split(quote=quote)

    quote_request = quote.quote_request
    if quote_request.status in {
        QuoteStatus.REJECTED,
        QuoteStatus.CANCELLED,
        QuoteStatus.EXPIRED,
        QuoteStatus.CLOSED,
    } and quote.status != QuoteOfferStatus.ACCEPTED:
        raise ValidationError("This request can no longer be accepted.")

    now = timezone.now()
    if quote.status != QuoteOfferStatus.ACCEPTED:
        quote.status = QuoteOfferStatus.ACCEPTED
        quote.accepted_at = now
        quote.rejected_at = None
        quote.rejection_reason = ""
        quote.rejection_message = ""
        quote.save(
            update_fields=[
                "status",
                "accepted_at",
                "rejected_at",
                "rejection_reason",
                "rejection_message",
                "updated_at",
            ]
        )

    if quote_request.status != QuoteStatus.CLOSED:
        quote_request.status = QuoteStatus.CLOSED
        quote_request.save(update_fields=["status", "updated_at"])

    Quote.objects.filter(quote_request=quote_request).exclude(pk=quote.pk).exclude(
        status=QuoteOfferStatus.PENDING,
    ).update(
        status=QuoteOfferStatus.REJECTED,
        rejected_at=now,
        rejection_reason="Superseded by accepted quote",
        rejection_message="Another quote was accepted.",
        updated_at=now,
    )

    payment = create_payment_for_quote(quote=quote, payer=accepted_by, payer_phone=payer_phone)
    return quote, payment


def accept_quote(*, quote: Quote, accepted_by, payer_phone: str = "") -> Quote:
    quote, _payment = accept_quote_for_payment(
        quote=quote,
        accepted_by=accepted_by,
        payer_phone=payer_phone,
    )
    return quote
