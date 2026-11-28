"""Super-admin resolution of ``needs_review`` payments (audit #1b).

Audit #1/#2 added ``needs_review``: Daraja reported a success that could not be
verified (wrong amount, missing receipt/amount, or a second debit on an
already-paid payment). Such a payment is never paid, and while it is under
review the quote cannot be re-pushed. This module is the only way out, and it
is deliberately narrow:

* ``confirm_paid``: the operator reconciled the money by hand and types the
  M-Pesa receipt number and amount. The amount must equal the server-stored
  expected amount unless an explicit ``override_reason`` is given. The receipt
  must match the receipt Daraja reported (when one was recorded) and must not
  already settle another payment. Confirmation then goes through the canonical
  paid path: ``payments.services.mark_payment_paid`` for a canonical
  ``Payment`` (creates/unlocks the ManagedJob, notifies), or the
  ``mpesa_payment_confirmed`` signal for a cashier ``MpesaPayment`` (settles
  its ManagedJob via ``mpesa_payments.receivers``).
* ``mark_failed``: closes the review as failed. The quote can then be paid
  again with a fresh STK push.

Only platform super-admins (``is_superuser``) may resolve. Every resolution
writes an admin audit entry (actor, timestamp, resolution, reason, amounts,
receipt) through ``api.admin_audit.record_admin_audit``.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from common.money import money
from mpesa_payments.models import MpesaPayment, MpesaPaymentStatus, MpesaReconciliationStatus
from payments.models import MpesaSTKRequest, Payment

logger = logging.getLogger("payments")

RESOLVE_CONFIRM_PAID = "confirm_paid"
RESOLVE_MARK_FAILED = "mark_failed"
RESOLUTIONS = {RESOLVE_CONFIRM_PAID, RESOLVE_MARK_FAILED}

KIND_PAYMENT = "payment"
KIND_MPESA_PAYMENT = "mpesa_payment"


def can_resolve_payment_reviews(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False))


def _parse_amount(value) -> Decimal:
    if value in (None, ""):
        raise ValidationError({"amount": "Type the amount received."})
    try:
        return money(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({"amount": "Amount must be a number."})


def _receipt_settles_another_payment(receipt: str, *, exclude_payment_id=None, exclude_mpesa_id=None) -> bool:
    canonical = Payment.objects.filter(mpesa_receipt_number__iexact=receipt, status=Payment.STATUS_PAID)
    if exclude_payment_id is not None:
        canonical = canonical.exclude(pk=exclude_payment_id)
    cashier = MpesaPayment.objects.filter(mpesa_receipt_number__iexact=receipt, status=MpesaPaymentStatus.PAID)
    if exclude_mpesa_id is not None:
        cashier = cashier.exclude(pk=exclude_mpesa_id)
    return canonical.exists() or cashier.exists()


def _validate_confirmation(*, expected: Decimal, recorded_receipt: str, receipt_number, amount, override_reason: str):
    receipt = str(receipt_number or "").strip()
    if not receipt:
        raise ValidationError({"receipt_number": "Type the M-Pesa receipt number."})
    typed = _parse_amount(amount)
    if recorded_receipt and recorded_receipt.strip().upper() != receipt.upper():
        raise ValidationError(
            {"receipt_number": f"Receipt does not match the receipt Daraja reported for this payment ({recorded_receipt})."}
        )
    if typed != expected and not override_reason:
        raise ValidationError(
            {"amount": f"Amount {typed} does not match the expected amount {expected}. Enter an override reason to accept it."}
        )
    return receipt, typed


def _audit(*, actor, obj, kind: str, resolution: str, reason: str, metadata: dict):
    from api.admin_audit import record_admin_audit

    record_admin_audit(
        user=actor,
        obj=obj,
        action=f"needs_review_{resolution}",
        reason=reason,
        metadata={"kind": kind, "resolved_at": timezone.now().isoformat(), **metadata},
    )
    logger.warning(
        "needs_review payment resolved kind=%s id=%s resolution=%s actor_id=%s reason=%s metadata=%s",
        kind, obj.pk, resolution, getattr(actor, "id", None), reason, metadata,
    )


def _common_inputs(actor, resolution, reason, override_reason):
    if not can_resolve_payment_reviews(actor):
        raise PermissionDenied("Only a platform super-admin can resolve a payment under review.")
    resolution = str(resolution or "").strip().lower()
    if resolution not in RESOLUTIONS:
        raise ValidationError({"resolution": "resolution must be confirm_paid or mark_failed."})
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "A resolution reason is required."})
    return resolution, reason, str(override_reason or "").strip()


@transaction.atomic
def resolve_canonical_payment(
    payment_id: int,
    *,
    actor,
    resolution: str,
    reason: str,
    receipt_number=None,
    amount=None,
    override_reason: str = "",
) -> Payment:
    resolution, reason, override_reason = _common_inputs(actor, resolution, reason, override_reason)
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    if payment.status != Payment.STATUS_NEEDS_REVIEW:
        raise ValidationError({"status": f"Payment is {payment.status}, not needs_review."})
    review_requests = list(
        MpesaSTKRequest.objects.select_for_update()
        .filter(payment=payment, status=MpesaSTKRequest.STATUS_NEEDS_REVIEW)
        .order_by("-requested_at", "-id")
    )
    expected = money(payment.expected_amount or payment.amount)
    previous = {
        "previous_status": payment.status,
        "expected_amount": str(expected),
        "reported_amount": str(payment.received_amount) if payment.received_amount is not None else None,
        "reported_receipt": payment.mpesa_receipt_number or "",
        "stk_request_ids": [stk.id for stk in review_requests],
    }

    if resolution == RESOLVE_CONFIRM_PAID:
        receipt, typed = _validate_confirmation(
            expected=expected,
            recorded_receipt=payment.mpesa_receipt_number or "",
            receipt_number=receipt_number,
            amount=amount,
            override_reason=override_reason,
        )
        if _receipt_settles_another_payment(receipt, exclude_payment_id=payment.id):
            raise ValidationError({"receipt_number": "This receipt already settles another payment."})
        now = timezone.now()
        for stk in review_requests[:1]:
            # The prompt whose money was reconciled becomes the successful one.
            stk.status = MpesaSTKRequest.STATUS_SUCCESS
            stk.response_description = f"Resolved paid by admin {getattr(actor, 'id', None)}: {reason}"[:1000]
            stk.completed_at = stk.completed_at or now
            stk.save(update_fields=["status", "response_description", "completed_at", "updated_at"])
        from payments.services import mark_payment_paid

        mark_payment_paid(
            payment,
            receipt_number=receipt,
            received_amount=typed,
            confirmed_at=now,
            amount_override_reason=override_reason if typed != expected else "",
        )
        _audit(
            actor=actor,
            obj=payment,
            kind=KIND_PAYMENT,
            resolution=resolution,
            reason=reason,
            metadata={
                **previous,
                "receipt_number": receipt,
                "confirmed_amount": str(typed),
                "amount_override_reason": override_reason if typed != expected else "",
            },
        )
    else:
        for stk in review_requests:
            stk.status = MpesaSTKRequest.STATUS_FAILED
            stk.response_description = f"Resolved failed by admin {getattr(actor, 'id', None)}: {reason}"[:1000]
            stk.save(update_fields=["status", "response_description", "updated_at"])
        payment.status = Payment.STATUS_FAILED
        payment.save(update_fields=["status", "updated_at"])
        _audit(actor=actor, obj=payment, kind=KIND_PAYMENT, resolution=resolution, reason=reason, metadata=previous)
    payment.refresh_from_db()
    return payment


@transaction.atomic
def resolve_mpesa_payment(
    payment_id: int,
    *,
    actor,
    resolution: str,
    reason: str,
    receipt_number=None,
    amount=None,
    override_reason: str = "",
) -> MpesaPayment:
    from mpesa_payments.signals import mpesa_payment_confirmed, mpesa_payment_failed

    resolution, reason, override_reason = _common_inputs(actor, resolution, reason, override_reason)
    payment = MpesaPayment.objects.select_for_update().get(pk=payment_id)
    if payment.status != MpesaPaymentStatus.NEEDS_REVIEW:
        raise ValidationError({"status": f"Payment is {payment.status}, not needs_review."})
    expected = money(payment.amount)
    previous = {
        "previous_status": payment.status,
        "previous_reconciliation_status": payment.reconciliation_status,
        "expected_amount": str(expected),
        "reported_amount": str(payment.paid_amount) if payment.paid_amount is not None else None,
        "reported_receipt": payment.mpesa_receipt_number or "",
    }

    if resolution == RESOLVE_CONFIRM_PAID:
        receipt, typed = _validate_confirmation(
            expected=expected,
            recorded_receipt=payment.mpesa_receipt_number or "",
            receipt_number=receipt_number,
            amount=amount,
            override_reason=override_reason,
        )
        if _receipt_settles_another_payment(receipt, exclude_mpesa_id=payment.id):
            raise ValidationError({"receipt_number": "This receipt already settles another payment."})
        payment.status = MpesaPaymentStatus.PAID
        payment.reconciliation_status = MpesaReconciliationStatus.CONFIRMED
        payment.mpesa_receipt_number = receipt
        payment.paid_amount = typed
        payment.confirmed_at = timezone.now()
        payment.result_desc = f"Resolved paid by admin {getattr(actor, 'id', None)}: {reason}"
        payment.save(
            update_fields=[
                "status", "reconciliation_status", "mpesa_receipt_number", "paid_amount",
                "confirmed_at", "result_desc", "updated_at",
            ]
        )
        _audit(
            actor=actor,
            obj=payment,
            kind=KIND_MPESA_PAYMENT,
            resolution=resolution,
            reason=reason,
            metadata={
                **previous,
                "receipt_number": receipt,
                "confirmed_amount": str(typed),
                "amount_override_reason": override_reason if typed != expected else "",
            },
        )
        # Same settlement path as a verified callback (mpesa_payments.receivers).
        transaction.on_commit(lambda p=payment: mpesa_payment_confirmed.send(sender=MpesaPayment, payment=p))
    else:
        payment.status = MpesaPaymentStatus.FAILED
        payment.reconciliation_status = MpesaReconciliationStatus.MANUAL_REVIEW
        payment.result_desc = f"Resolved failed by admin {getattr(actor, 'id', None)}: {reason}"
        payment.save(update_fields=["status", "reconciliation_status", "result_desc", "updated_at"])
        _audit(actor=actor, obj=payment, kind=KIND_MPESA_PAYMENT, resolution=resolution, reason=reason, metadata=previous)
        transaction.on_commit(lambda p=payment: mpesa_payment_failed.send(sender=MpesaPayment, payment=p))
    return payment
