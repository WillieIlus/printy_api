"""Verified payment transactions (law 10.2(b)).

A :class:`quotes.models.PriceTransaction` is written only here, and only from
a canonical ``Payment`` that already carries a provider-confirmed status and
confirmation time. A pending/processing payment (including one whose STK push
was merely sent) can never produce a transaction row.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from quotes.models import (
    PriceTransaction,
    QuoteVersion,
    QuoteVersionOutcome,
)
from quotes.spec_fingerprint import compute_spec_fingerprint


def _accepted_version_for(quote) -> QuoteVersion | None:
    if quote is None:
        return None
    version = (
        QuoteVersion.objects.filter(
            quote=quote, outcome=QuoteVersionOutcome.ACCEPTED
        )
        .order_by("-version_number", "-id")
        .first()
    )
    if version is not None:
        return version
    return (
        QuoteVersion.objects.filter(quote=quote)
        .exclude(outcome=QuoteVersionOutcome.SUPERSEDED)
        .order_by("-version_number", "-id")
        .first()
    )


def _quote_for_payment(payment):
    quote = getattr(payment, "quote", None)
    if quote is not None:
        return quote
    job = getattr(payment, "managed_job", None)
    return getattr(job, "source_quote", None) if job is not None else None


def record_verified_payment_transaction(payment) -> PriceTransaction | None:
    """Append the immutable transaction for a verified payment confirmation.

    Returns the existing row when the same payment is confirmed again, so
    retried callbacks stay idempotent. Returns ``None`` (and writes nothing)
    for any payment that is not both paid and provider-confirmed.
    """
    from payments.models import Payment

    if payment.status != Payment.STATUS_PAID or payment.confirmed_at is None:
        return None

    existing = PriceTransaction.objects.filter(payment=payment).first()
    if existing is not None:
        return existing

    quote = _quote_for_payment(payment)
    version = _accepted_version_for(quote)
    quote_request = getattr(version, "quote_request", None) or getattr(
        quote, "quote_request", None
    )
    managed_job = getattr(payment, "managed_job", None)
    if managed_job is None and quote is not None:
        from jobs.models import ManagedJob

        managed_job = ManagedJob.objects.filter(source_quote=quote).first()

    printer = getattr(version, "printer", None) or getattr(quote, "shop", None)
    if printer is None and managed_job is not None:
        printer = getattr(managed_job, "assigned_shop", None)

    spec_fingerprint = getattr(version, "spec_fingerprint", "") or ""
    if not spec_fingerprint and quote_request is not None:
        spec_fingerprint = compute_spec_fingerprint(quote_request)

    price_paid = (
        payment.received_amount
        if getattr(payment, "received_amount", None) is not None
        else payment.amount
    )
    if price_paid is None:
        raise ValidationError(
            "A verified payment must carry the price that was actually paid."
        )

    provider_reference = (
        getattr(payment, "mpesa_receipt_number", None)
        or getattr(payment, "checkout_request_id", None)
        or getattr(payment, "merchant_request_id", None)
        or ""
    )

    with transaction.atomic():
        # Re-check under the transaction so two concurrent confirmations of the
        # same payment cannot append two rows.
        existing = PriceTransaction.objects.filter(payment=payment).first()
        if existing is not None:
            return existing
        return PriceTransaction.objects.create(
            payment=payment,
            quote_version=version,
            quote_request=quote_request,
            managed_job=managed_job,
            printer=printer,
            spec_fingerprint=spec_fingerprint,
            price_paid=price_paid,
            currency=getattr(payment, "currency", "KES") or "KES",
            confirmed_payment_at=payment.confirmed_at,
            provider_reference=str(provider_reference)[:100],
            payer_phone=(getattr(payment, "payer_phone", None) or "")[:20],
        )
