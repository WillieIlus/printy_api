"""Recovery of lost M-Pesa STK confirmations (audit #1c).

Daraja confirms an STK payment by calling our callback URL. If that callback
is lost (network, outage, a deploy at the wrong moment) the canonical
``Payment`` used to sit in ``processing`` forever: nothing asked Daraja what
happened, and the super-admin needs_review resolution could not touch it.

This module asks Daraja (STK Query, ``/mpesa/stkpushquery/v1/query``) about
prompts whose callback never arrived and feeds the answer through
``payments.services.handle_stk_callback`` -- the SAME state machine, amount
and receipt checks a verified callback goes through. The rules:

* A final answer is replayed as a callback built from Daraja's own answer.
  ResultCode 0 is applied as ``paid`` ONLY if the answer carries
  ``MpesaReceiptNumber`` and an ``Amount`` equal to the stored expected
  amount, exactly like a callback. Daraja's documented STK Query answer has
  neither, so in practice a confirmed success becomes ``needs_review``, which
  a super-admin then reconciles against the M-Pesa statement and resolves
  with the receipt and amount (``/api/admin/payments/<id>/resolve-review/``).
  A bare "success" is never turned into ``paid``.
* A failure answer goes through the callback failure branch unchanged (a
  late failure for an expired prompt does not touch the payment).
* "Still being processed" for a prompt older than MPESA_STK_IN_FLIGHT_SECONDS
  expires the prompt and, when nothing else is in flight for it, sets the
  payment to ``expired``. Expired prompts keep being queried for
  MPESA_RECOVERY_MAX_AGE_HOURS and a late callback still applies, so a late
  success is never lost.
* A Daraja error (network, auth, 429, malformed answer) changes nothing.
* Final payments (paid, failed, cancelled, needs_review) are never queried.
* Every write is under ``select_for_update`` and is idempotent: running the
  recovery twice, or concurrently with a real callback, changes nothing the
  second time.
* Rate limits: a pause between queries, a per-prompt cooldown (shared cache),
  a per-run limit, and the batch stops on HTTP 429.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone

from payments.models import MpesaSTKRequest, Payment
from payments.services import (
    STK_SOURCE_QUERY,
    MpesaDarajaClient,
    MpesaDarajaError,
    _is_stub_mode,
    handle_stk_callback,
    stk_in_flight_window,
)

logger = logging.getLogger(__name__)

# Payments that can still have a prompt worth asking Daraja about.
RECOVERABLE_PAYMENT_STATUSES = (
    Payment.STATUS_PENDING,
    Payment.STATUS_PROCESSING,
    Payment.STATUS_EXPIRED,
)
FINAL_PAYMENT_STATUSES = (
    Payment.STATUS_PAID,
    Payment.STATUS_FAILED,
    Payment.STATUS_CANCELLED,
    Payment.STATUS_NEEDS_REVIEW,
)
# Prompts that never got a callback: still in flight, or expired by us.
QUERYABLE_STK_STATUSES = (*MpesaSTKRequest.IN_FLIGHT_STATUSES, MpesaSTKRequest.STATUS_EXPIRED)
STUB_CHECKOUT_PREFIX = "TEST-CR-"

OUTCOME_PAID = "paid"
OUTCOME_NEEDS_REVIEW = "needs_review"
OUTCOME_FAILED = "failed"
OUTCOME_EXPIRED = "expired"
OUTCOME_STILL_PROCESSING = "still_processing"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_ERROR = "error"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_SKIPPED_FINAL = "skipped_final"
OUTCOME_SKIPPED_COOLDOWN = "skipped_cooldown"
OUTCOME_SKIPPED_STUB = "skipped_stub"
OUTCOME_NO_PROMPT = "no_prompt"

# Outcomes for which a Daraja call was made (used by the batch rate limit).
_QUERIED_OUTCOMES = {
    OUTCOME_PAID,
    OUTCOME_NEEDS_REVIEW,
    OUTCOME_FAILED,
    OUTCOME_EXPIRED,
    OUTCOME_STILL_PROCESSING,
    OUTCOME_UNCHANGED,
    OUTCOME_ERROR,
    OUTCOME_RATE_LIMITED,
}


@dataclass
class RecoveryResult:
    payment_id: int
    outcome: str
    payment_status: str
    detail: str = ""
    checkout_request_id: str = ""

    def as_dict(self) -> dict:
        return {
            "payment_id": self.payment_id,
            "outcome": self.outcome,
            "payment_status": self.payment_status,
            "detail": self.detail,
            "checkout_request_id": self.checkout_request_id,
        }


@dataclass
class RecoveryRun:
    results: list[RecoveryResult] = field(default_factory=list)
    stopped_reason: str = ""

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            counts[result.outcome] = counts.get(result.outcome, 0) + 1
        return counts


def _setting(name: str, default):
    value = getattr(settings, name, default)
    return default if value in (None, "") else value


def _cooldown_seconds() -> int:
    return int(_setting("MPESA_RECOVERY_QUERY_COOLDOWN_SECONDS", 60))


def _max_age() -> timedelta:
    return timedelta(hours=int(_setting("MPESA_RECOVERY_MAX_AGE_HOURS", 24)))


def _query_interval() -> float:
    return float(_setting("MPESA_RECOVERY_QUERY_INTERVAL_SECONDS", 1.0))


def _queryable_prompts(*, now, older_than: timedelta, max_age: timedelta):
    """STK prompts with a Daraja id, no callback, sent before ``now - older_than``."""
    return (
        MpesaSTKRequest.objects.filter(
            status__in=QUERYABLE_STK_STATUSES,
            callback_received_at__isnull=True,
            checkout_request_id__isnull=False,
            requested_at__lt=now - older_than,
            requested_at__gte=now - max_age,
            payment__status__in=RECOVERABLE_PAYMENT_STATUSES,
        )
        .exclude(checkout_request_id="")
        .exclude(checkout_request_id__startswith=STUB_CHECKOUT_PREFIX)
    )


def stale_payment_ids(*, now=None, older_than: timedelta | None = None, max_age: timedelta | None = None) -> list[int]:
    """Payments with a prompt worth querying, oldest prompt first."""
    now = now or timezone.now()
    older_than = stk_in_flight_window() if older_than is None else older_than
    max_age = _max_age() if max_age is None else max_age
    ordered = (
        _queryable_prompts(now=now, older_than=older_than, max_age=max_age)
        .order_by("requested_at", "id")
        .values_list("payment_id", flat=True)
    )
    seen: list[int] = []
    for payment_id in ordered:
        if payment_id not in seen:
            seen.append(payment_id)
    return seen


def _callback_payload_from_query(stk_request: MpesaSTKRequest, data: dict) -> dict:
    """Rebuild a callback body from Daraja's own STK Query answer.

    Only fields Daraja returned are copied; nothing is invented. If Daraja did
    not return CallbackMetadata (its documented answer never does), the
    callback path sees a success with no receipt/amount and flags it.
    """
    stk_callback = {
        "MerchantRequestID": data.get("MerchantRequestID") or stk_request.merchant_request_id or "",
        "CheckoutRequestID": stk_request.checkout_request_id,
        "ResultCode": data.get("ResultCode"),
        "ResultDesc": data.get("ResultDesc") or "",
    }
    if isinstance(data.get("CallbackMetadata"), dict):
        stk_callback["CallbackMetadata"] = data["CallbackMetadata"]
    return {
        "Body": {"stkCallback": stk_callback},
        "RecoverySource": STK_SOURCE_QUERY,
        "StkQueryResponse": data,
    }


def _apply_final_answer(stk_request: MpesaSTKRequest, data: dict) -> str:
    payload = _callback_payload_from_query(stk_request, data)
    try:
        with transaction.atomic():
            handled = handle_stk_callback(callback_payload=payload, source=STK_SOURCE_QUERY)
    except ValidationError as exc:
        # Malformed answer (e.g. no usable ResultCode): treated like a Daraja
        # error -- the atomic block rolled back, nothing changed.
        logger.warning(
            "STK Query answer rejected checkout_request_id=%s error=%s", stk_request.checkout_request_id, exc
        )
        return OUTCOME_ERROR
    status = handled.get("status")
    if status == "success":
        return OUTCOME_UNCHANGED if handled.get("duplicate") else OUTCOME_PAID
    if status == "needs_review":
        return OUTCOME_NEEDS_REVIEW
    if status == "failed":
        return OUTCOME_FAILED
    return OUTCOME_UNCHANGED


def _apply_still_processing(stk_request_id: int, *, now) -> str:
    """Daraja is still working on it: expire the prompt if it is past the window."""
    cutoff = now - stk_in_flight_window()
    with transaction.atomic():
        stk_request = MpesaSTKRequest.objects.select_for_update().get(pk=stk_request_id)
        payment = Payment.objects.select_for_update().get(pk=stk_request.payment_id)
        if stk_request.status not in MpesaSTKRequest.IN_FLIGHT_STATUSES or stk_request.requested_at >= cutoff:
            return OUTCOME_STILL_PROCESSING
        stk_request.status = MpesaSTKRequest.STATUS_EXPIRED
        stk_request.completed_at = now
        stk_request.response_description = "Expired: no callback within the in-flight window (STK Query: still processing)."
        stk_request.save(update_fields=["status", "completed_at", "response_description", "updated_at"])
        other_in_flight = (
            MpesaSTKRequest.objects.filter(payment_id=payment.pk, status__in=MpesaSTKRequest.IN_FLIGHT_STATUSES)
            .exclude(pk=stk_request.pk)
            .exists()
        )
        if payment.status == Payment.STATUS_PROCESSING and not other_in_flight:
            payment.status = Payment.STATUS_EXPIRED
            payment.save(update_fields=["status", "updated_at"])
    return OUTCOME_EXPIRED


def recover_payment(
    payment: Payment | int,
    *,
    client: MpesaDarajaClient | None = None,
    now=None,
    older_than: timedelta | None = None,
    max_age: timedelta | None = None,
    respect_cooldown: bool = True,
) -> RecoveryResult:
    """Ask Daraja about ``payment``'s unanswered prompt and apply the answer.

    Never raises for a Daraja problem: the outcome is ``error`` (or
    ``rate_limited``) and no state is changed.
    """
    payment_id = payment if isinstance(payment, int) else payment.pk
    now = now or timezone.now()
    older_than = stk_in_flight_window() if older_than is None else older_than
    max_age = _max_age() if max_age is None else max_age

    current = Payment.objects.get(pk=payment_id)
    if current.status in FINAL_PAYMENT_STATUSES:
        return RecoveryResult(payment_id, OUTCOME_SKIPPED_FINAL, current.status, "Payment is already final.")
    if _is_stub_mode():
        return RecoveryResult(
            payment_id, OUTCOME_SKIPPED_STUB, current.status, "MPESA_FORCE_STUB/test mode: Daraja is not queried."
        )

    stk_request = (
        _queryable_prompts(now=now, older_than=older_than, max_age=max_age)
        .filter(payment_id=payment_id)
        .order_by("-requested_at", "-id")
        .first()
    )
    if stk_request is None:
        return RecoveryResult(payment_id, OUTCOME_NO_PROMPT, current.status, "No unanswered STK prompt to query.")
    checkout_request_id = stk_request.checkout_request_id

    if respect_cooldown:
        cooldown = _cooldown_seconds()
        if cooldown and not cache.add(f"mpesa-stk-query:{stk_request.pk}", "1", timeout=cooldown):
            return RecoveryResult(
                payment_id,
                OUTCOME_SKIPPED_COOLDOWN,
                current.status,
                f"Queried less than {cooldown}s ago.",
                checkout_request_id,
            )

    # The Daraja HTTP call is made with no lock held.
    try:
        client = client or MpesaDarajaClient()
        answer = client.query_stk_status(checkout_request_id=checkout_request_id)
    except (MpesaDarajaError, ImproperlyConfigured) as exc:
        http_status = getattr(exc, "http_status", None)
        outcome = OUTCOME_RATE_LIMITED if http_status == 429 else OUTCOME_ERROR
        logger.warning(
            "STK Query failed payment_id=%s checkout_request_id=%s http_status=%s error=%s",
            payment_id,
            checkout_request_id,
            http_status,
            exc,
        )
        return RecoveryResult(payment_id, outcome, current.status, str(exc), checkout_request_id)

    if answer.get("state") == "processing":
        outcome = _apply_still_processing(stk_request.pk, now=now)
    else:
        outcome = _apply_final_answer(stk_request, answer.get("data") or {})

    current.refresh_from_db(fields=["status"])
    logger.info(
        "STK recovery payment_id=%s checkout_request_id=%s outcome=%s payment_status=%s",
        payment_id,
        checkout_request_id,
        outcome,
        current.status,
    )
    return RecoveryResult(payment_id, outcome, current.status, "", checkout_request_id)


def recover_stale_payments(
    *,
    limit: int = 50,
    older_than: timedelta | None = None,
    max_age: timedelta | None = None,
    interval_seconds: float | None = None,
    client: MpesaDarajaClient | None = None,
    sleep=time.sleep,
    now=None,
) -> RecoveryRun:
    """Recover up to ``limit`` stale payments, pausing between Daraja calls.

    Stops early on HTTP 429 so a throttled run does not keep hammering Daraja.
    """
    now = now or timezone.now()
    interval = _query_interval() if interval_seconds is None else float(interval_seconds)
    run = RecoveryRun()
    if _is_stub_mode():
        run.stopped_reason = "stub_mode"
        return run
    payment_ids = stale_payment_ids(now=now, older_than=older_than, max_age=max_age)[: max(int(limit), 0)]
    if payment_ids and client is None:
        try:
            client = MpesaDarajaClient()  # one client: one cached OAuth token per run
        except (MpesaDarajaError, ImproperlyConfigured) as exc:
            logger.error("STK recovery cannot build the Daraja client: %s", exc)
            run.stopped_reason = f"daraja_config: {exc}"
            return run
    last_queried = False
    for payment_id in payment_ids:
        if last_queried and interval > 0:
            sleep(interval)
        result = recover_payment(
            payment_id, client=client, now=now, older_than=older_than, max_age=max_age, respect_cooldown=True
        )
        run.results.append(result)
        last_queried = result.outcome in _QUERIED_OUTCOMES
        if result.outcome == OUTCOME_RATE_LIMITED:
            run.stopped_reason = "rate_limited"
            break
    return run
