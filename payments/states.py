"""Canonical payment states (law 10.7).

Money is recorded by three models with three different vocabularies:

* ``payments.Payment``               -> pending / processing / paid / failed / cancelled / expired
* ``mpesa_payments.MpesaPayment``    -> initiated / pending / paid / failed / cancelled / needs_review
* ``jobs.ManagedJob.payment_status`` -> pending / confirmation_pending / confirmed / release_ready / ...

Buyer and manager screens must speak one vocabulary, and - per law 10.7 - must
never show "Paid" unless the payment provider's confirmation was received and
verified. A sent STK push is "Awaiting confirmation", never "Paid".

This module is the single translation point. Every screen should read a state
from here rather than inventing its own mapping; the only status that maps to
``PAID`` is a provider-confirmed ``paid``.
"""

from __future__ import annotations


class PaymentState:
    """The canonical states a buyer/manager may be shown."""

    NOT_STARTED = "not_started"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    CHOICES = [
        (NOT_STARTED, "Not started"),
        (AWAITING_CONFIRMATION, "Awaiting confirmation"),
        (PAID, "Paid"),
        (FAILED, "Failed"),
        (CANCELLED, "Cancelled"),
        (TIMED_OUT, "Timed out"),
    ]
    LABELS = dict(CHOICES)

    # The only state a screen may present as a confirmed payment. A STK push is
    # "awaiting confirmation"; only a verified callback/status-query reaches here.
    VERIFIED_STATES = frozenset({PAID})

    # Convenience grouping for callers that need "is this attempt still live?".
    PENDING_STATES = frozenset({NOT_STARTED, AWAITING_CONFIRMATION})


# ``payments.Payment.status`` mapping. ``pending`` is a payment row that exists
# (the quote was accepted) but for which no STK push has been accepted yet, so
# it is "not started"; only a verified callback moves it to ``paid``.
_PAYMENT_STATUS_STATES = {
    "pending": PaymentState.NOT_STARTED,
    "processing": PaymentState.AWAITING_CONFIRMATION,
    "paid": PaymentState.PAID,
    "failed": PaymentState.FAILED,
    "cancelled": PaymentState.CANCELLED,
    "expired": PaymentState.TIMED_OUT,
}

# ``mpesa_payments.MpesaPayment.status`` mapping. ``initiated`` means the row
# exists but Daraja has not accepted the push; ``pending`` means Daraja accepted
# the push (money has *not* moved). ``needs_review`` is money that may have
# moved but whose amount could not be verified, so it is never "paid".
_MPESA_STATUS_STATES = {
    "initiated": PaymentState.NOT_STARTED,
    "pending": PaymentState.AWAITING_CONFIRMATION,
    "paid": PaymentState.PAID,
    "failed": PaymentState.FAILED,
    "cancelled": PaymentState.CANCELLED,
    "needs_review": PaymentState.AWAITING_CONFIRMATION,
}

# ``jobs.ManagedJob.payment_status`` mapping. ``confirmed`` and beyond are only
# ever set from a verified canonical ``Payment.STATUS_PAID`` (see
# ``jobs.services.managed_job_creation`` and ``mpesa_payments.receivers``).
_JOB_STATUS_STATES = {
    "pending": PaymentState.NOT_STARTED,
    "confirmation_pending": PaymentState.AWAITING_CONFIRMATION,
    "confirmed": PaymentState.PAID,
    "release_ready": PaymentState.PAID,
    "payout_on_hold": PaymentState.PAID,
    "released": PaymentState.PAID,
    "refunded": PaymentState.CANCELLED,
}


def _norm(value) -> str:
    return str(value or "").strip().lower()


def canonical_job_payment_state(job) -> str:
    """Canonical state for a ``ManagedJob``, preferring its real ``Payment`` rows.

    A sent STK push leaves the canonical ``Payment`` in ``processing`` (mapped to
    ``AWAITING_CONFIRMATION``); only a verified callback reaches ``paid``. Falls
    back to the job's own ``payment_status`` when no payment row exists yet.
    """
    payment = None
    canonical = getattr(job, "canonical_payments", None)
    if canonical is not None:
        payment = canonical.order_by("-created_at", "-id").first()
    if payment is None:
        source_quote = getattr(job, "source_quote", None)
        payments_qs = getattr(source_quote, "payments", None) if source_quote is not None else None
        if payments_qs is not None:
            payment = payments_qs.order_by("-created_at", "-id").first()
    if payment is not None:
        return payment_state_for(payment)
    return job_payment_state(getattr(job, "payment_status", ""))


def payment_state_for_status(status) -> str:
    """Map a ``payments.Payment.status`` value to the canonical state."""
    return _PAYMENT_STATUS_STATES.get(_norm(status), PaymentState.NOT_STARTED)


def mpesa_state_for_status(status) -> str:
    """Map an ``mpesa_payments.MpesaPayment.status`` value to the canonical state."""
    return _MPESA_STATUS_STATES.get(_norm(status), PaymentState.NOT_STARTED)


def job_payment_state(payment_status) -> str:
    """Map a ``jobs.ManagedJob.payment_status`` value to the canonical state."""
    return _JOB_STATUS_STATES.get(_norm(payment_status), PaymentState.NOT_STARTED)


def payment_state_for(payment) -> str:
    """Canonical state of a ``payments.Payment`` instance."""
    return payment_state_for_status(getattr(payment, "status", ""))


def mpesa_payment_state(mpesa_payment) -> str:
    """Canonical state of an ``mpesa_payments.MpesaPayment`` instance."""
    return mpesa_state_for_status(getattr(mpesa_payment, "status", ""))


def payment_state_label(state) -> str:
    """Human label for a canonical state, safe for any unknown value."""
    key = _norm(state)
    if key in PaymentState.LABELS:
        return PaymentState.LABELS[key]
    return str(state or "").replace("_", " ").title()


def is_verified_paid_state(state) -> bool:
    """True only for the provider-confirmed state (law 10.7)."""
    return _norm(state) in PaymentState.VERIFIED_STATES


def is_paid(payment) -> bool:
    """True only when the canonical payment was verified as paid."""
    return payment_state_for(payment) == PaymentState.PAID


__all__ = [
    "PaymentState",
    "payment_state_for",
    "payment_state_for_status",
    "mpesa_state_for_status",
    "job_payment_state",
    "canonical_job_payment_state",
    "payment_state_label",
    "is_verified_paid_state",
    "is_paid",
]
