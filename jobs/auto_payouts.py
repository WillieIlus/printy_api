"""Automatic payouts via Daraja B2C -- Prompt 7 PART 2 (disabled by default).

Runs from cron (``python manage.py run_auto_payouts``). Nothing here runs
unless ``settings.AUTO_PAYOUTS_ENABLED`` is True AND the B2C configuration is
complete (fail closed). No client- or manager-facing endpoint reaches this
module; only the runner, the authenticated B2C callbacks and the super-admin
payout endpoints do.

Eligibility (ALL required, re-checked under the job row lock right before
every send):

1. job status ``completed`` -- reachable only via the buyer's handover code,
   a legacy no-code buyer confirmation, or super-admin dispute resolution;
   legacy no-code completions go to needs_review (user decision);
2. client payment verified: a ``payments.Payment`` in ``paid`` and none in
   ``needs_review`` for the job;
3. no open dispute;
4. lateness hold resolved (``ManagedJob.payout_hold`` False). The rush-premium
   hold (``rush_premium_status == "held"``) does NOT block (decision 2); it
   is shown in the admin money summary.

Amounts come from the stored ``QuoteFinancialSplit`` (shop_payout,
broker_payout). They must match the job/assignment copies, be whole KES, and
sum to no more than the split's client_total.

Payees must have the ``can_receive_payouts`` capability and a verified phone
(UserProfile.phone_verified_at + phone_normalized); otherwise the payout stays
``pending`` with an admin-visible ``waiting_reason``.

Money safety:
- Lock order is always job row, then payout row.
- A payout is moved to ``processing`` and committed BEFORE the B2C request,
  so a concurrent runner sees it in flight and skips it.
- Every B2C request has its own OriginatorConversationID
  (``idempotency_key``, unique) -- never reused across attempts.
- Definite rejections retry with exponential backoff up to
  AUTO_PAYOUT_MAX_ATTEMPTS, then needs_review. An ambiguous send (may have
  reached Daraja) is never retried automatically: needs_review.
- Per-payout max and daily cap -> needs_review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from config.payout_config import auto_payout_config_errors
from jobs.audit_services import record_job_status_event
from jobs.choices import ManagedJobExceptionStatus, ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob
from jobs.models import ManagedJobPayout as P
from jobs.payout_state import (
    SOURCE_ADMIN_REVIEW,
    SOURCE_AUTO,
    SOURCE_B2C_CALLBACK,
    SOURCE_SYSTEM,
    audit_entry,
    transition,
)
from payments.b2c import B2CAmbiguous, B2CNotSent, get_b2c_client
from payments.models import Payment

logger = logging.getLogger("payments")

EVENT_PAYOUT_RELEASED_AUTO = "payout_released_auto"

# --- reason codes -------------------------------------------------------------
# pending + waiting_reason (re-evaluated every run; clears by itself)
WAIT_PAYMENT_NOT_VERIFIED = "payment_not_verified"
WAIT_DISPUTE_OPEN = "dispute_open"
WAIT_LATENESS_HOLD = "lateness_hold"
WAIT_JOB_NOT_COMPLETED = "job_not_completed"
WAIT_NO_RECIPIENT = "no_recipient"
WAIT_CANNOT_RECEIVE_PAYOUTS = "recipient_cannot_receive_payouts"
WAIT_PHONE_UNVERIFIED = "recipient_phone_unverified"
# needs_review (an admin must approve before money moves)
REVIEW_LEGACY_NO_CODE = "legacy_no_handover_code"
REVIEW_COMPLETION_UNVERIFIED = "completion_not_verified"
REVIEW_NO_SPLIT = "no_financial_split"
REVIEW_SPLIT_EXCEEDS_TOTAL = "split_exceeds_client_total"
REVIEW_AMOUNT_MISMATCH = "amount_mismatch_with_split"
REVIEW_NOT_WHOLE_KES = "amount_not_whole_kes"
REVIEW_OVER_MAX = "over_per_payout_max"
REVIEW_DAILY_CAP = "daily_cap_reached"
REVIEW_RETRIES_EXHAUSTED = "retries_exhausted"
REVIEW_AMBIGUOUS_SEND = "ambiguous_send_result"
REVIEW_RESULT_TIMEOUT = "result_timeout"
REVIEW_QUEUE_TIMEOUT = "b2c_queue_timeout"

WAITING_REASON_TEXT = {
    WAIT_PAYMENT_NOT_VERIFIED: "The client payment is not verified as paid (or a payment needs review).",
    WAIT_DISPUTE_OPEN: "The job has an open dispute.",
    WAIT_LATENESS_HOLD: "A lateness payout hold is set on the job.",
    WAIT_JOB_NOT_COMPLETED: "The job is not completed.",
    WAIT_NO_RECIPIENT: "No recipient account for this party.",
    WAIT_CANNOT_RECEIVE_PAYOUTS: "The recipient account cannot receive payouts (capability off).",
    WAIT_PHONE_UNVERIFIED: "The recipient has no verified M-Pesa phone number.",
    REVIEW_LEGACY_NO_CODE: "Job completed without a handover code (dispatched before handover codes).",
    REVIEW_COMPLETION_UNVERIFIED: (
        "The job has no recorded buyer handover or dispute completion (completed before handover codes); "
        "confirm delivery before approving."
    ),
    REVIEW_NO_SPLIT: "No stored QuoteFinancialSplit for the job.",
    REVIEW_SPLIT_EXCEEDS_TOTAL: "Stored payouts add up to more than the client total.",
    REVIEW_AMOUNT_MISMATCH: "The job's payout amount differs from the stored split.",
    REVIEW_NOT_WHOLE_KES: "B2C pays whole shillings only; the amount has cents.",
    REVIEW_OVER_MAX: "Above AUTO_PAYOUT_MAX_AMOUNT.",
    REVIEW_DAILY_CAP: "Sending it would exceed AUTO_PAYOUT_DAILY_CAP for today.",
    REVIEW_RETRIES_EXHAUSTED: "Daraja rejected every attempt (AUTO_PAYOUT_MAX_ATTEMPTS reached).",
    REVIEW_AMBIGUOUS_SEND: "The B2C request may have reached Daraja; outcome unknown. Check the M-Pesa statement.",
    REVIEW_RESULT_TIMEOUT: "No B2C result arrived in time. Check the M-Pesa statement.",
    REVIEW_QUEUE_TIMEOUT: "Daraja reported a queue timeout. Check the M-Pesa statement.",
}

# Approving these needs confirm_not_paid: money may already have moved.
POSSIBLY_PAID_REASONS = {REVIEW_AMBIGUOUS_SEND, REVIEW_RESULT_TIMEOUT, REVIEW_QUEUE_TIMEOUT}
# Data problems an approval cannot wave through: fix the data instead.
NON_APPROVABLE_REASONS = {REVIEW_NO_SPLIT, REVIEW_SPLIT_EXCEEDS_TOTAL, REVIEW_AMOUNT_MISMATCH, REVIEW_NOT_WHOLE_KES}

AUTO_PAYABLE_COMPLETION_METHODS = {ManagedJob.COMPLETION_BUYER_HANDOVER, ManagedJob.COMPLETION_ADMIN_DISPUTE}

ROLE_MANAGER = P.RECIPIENT_ROLE_MANAGER
ROLE_SHOP = P.RECIPIENT_ROLE_SHOP


class PayoutAdminError(ValidationError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message, code=code)
        self.code_name = code


# --- configuration --------------------------------------------------------------

def _settings_snapshot() -> dict:
    names = (
        "MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_B2C_SHORTCODE", "MPESA_B2C_INITIATOR_NAME",
        "MPESA_B2C_SECURITY_CREDENTIAL", "MPESA_B2C_RESULT_URL", "MPESA_B2C_TIMEOUT_URL",
        "MPESA_CALLBACK_SECRET", "AUTO_PAYOUT_MAX_AMOUNT", "AUTO_PAYOUT_DAILY_CAP",
    )
    return {name: getattr(settings, name, "") for name in names}


def config_errors() -> list[str]:
    return auto_payout_config_errors(_settings_snapshot(), production=not settings.DEBUG)


def auto_payouts_active() -> bool:
    return bool(getattr(settings, "AUTO_PAYOUTS_ENABLED", False)) and not config_errors()


def automatic_disbursement_available() -> bool:
    return auto_payouts_active()


def _int_setting(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


# --- evaluation -------------------------------------------------------------------

def _money(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _job_payments(job: ManagedJob):
    qs = Payment.objects.filter(managed_job=job)
    if job.source_quote_id:
        qs = Payment.objects.filter(managed_job=job) | Payment.objects.filter(quote_id=job.source_quote_id)
    return qs


def payment_verified(job: ManagedJob) -> bool:
    payments = _job_payments(job)
    if payments.filter(status=Payment.STATUS_NEEDS_REVIEW).exists():
        return False
    return payments.filter(status=Payment.STATUS_PAID).exists()


def dispute_open(job: ManagedJob) -> bool:
    return bool(
        job.dispute_open
        or str(job.exception_status) == ManagedJobExceptionStatus.DISPUTE_OPEN
        or str(job.status) == ManagedJobStatus.DISPUTED
    )


def verified_msisdn(user) -> str:
    if user is None:
        return ""
    from accounts.models import UserProfile

    profile = UserProfile.objects.filter(user=user).only("phone_normalized", "phone_verified_at").first()
    if profile is None or not profile.phone_verified_at or not profile.phone_normalized:
        return ""
    return str(profile.phone_normalized).lstrip("+")


def can_receive_payouts(user) -> bool:
    if user is None or not getattr(user, "is_active", False):
        return False
    from accounts.services.capabilities import has_capability

    return has_capability(user, "can_receive_payouts")


@dataclass
class Payee:
    role: str
    recipient: object
    amount: Decimal
    assignment: JobAssignment | None = None
    waiting_reason: str = ""
    review_reason: str = ""


@dataclass
class JobEvaluation:
    waiting_reason: str = ""
    review_reason: str = ""
    payees: list[Payee] = field(default_factory=list)
    split: dict | None = None


def _active_assignment(job: ManagedJob) -> JobAssignment | None:
    return (
        job.assignments.select_related("assigned_shop", "assigned_shop__owner")
        .filter(reassigned_from__isnull=True)
        .order_by("-id")
        .first()
    )


def evaluate_job(job: ManagedJob) -> JobEvaluation:
    """Decide, without side effects, what this job's automatic payouts need."""
    from jobs.settlement_compat import get_financial_split_for_job

    ev = JobEvaluation()
    if str(job.status) != ManagedJobStatus.COMPLETED:
        ev.waiting_reason = WAIT_JOB_NOT_COMPLETED
    elif dispute_open(job):
        ev.waiting_reason = WAIT_DISPUTE_OPEN
    elif job.payout_hold:
        ev.waiting_reason = WAIT_LATENESS_HOLD
    elif not payment_verified(job):
        ev.waiting_reason = WAIT_PAYMENT_NOT_VERIFIED

    split = get_financial_split_for_job(job)
    ev.split = split
    assignment = _active_assignment(job)
    shop_owner = getattr(getattr(assignment, "assigned_shop", None), "owner", None)
    if split is None:
        ev.review_reason = REVIEW_NO_SPLIT
        shop_amount = _money(getattr(assignment, "shop_payout", None))
        manager_amount = _money(job.broker_payout)
    else:
        shop_amount = _money(split.get("shop_payout"))
        manager_amount = _money(split.get("broker_payout"))
        client_total = _money(split.get("client_total"))
        if shop_amount + manager_amount > client_total:
            ev.review_reason = REVIEW_SPLIT_EXCEEDS_TOTAL
        elif _money(job.client_total) and _money(job.client_total) != client_total:
            ev.review_reason = REVIEW_AMOUNT_MISMATCH
        elif _money(job.broker_payout) and _money(job.broker_payout) != manager_amount:
            ev.review_reason = REVIEW_AMOUNT_MISMATCH
        elif assignment is not None and _money(assignment.shop_payout) and _money(assignment.shop_payout) != shop_amount:
            ev.review_reason = REVIEW_AMOUNT_MISMATCH
    # Allow-list (fail closed): only a buyer handover confirmation or a
    # platform-admin dispute resolution makes a completed job auto-payable.
    # buyer_no_code (dispatched before handover codes) and a blank method
    # (completed before migration 0008, possibly by the shop without delivery)
    # both need an admin approval first.
    if not ev.review_reason and str(job.status) == ManagedJobStatus.COMPLETED:
        if job.completion_method == ManagedJob.COMPLETION_BUYER_NO_CODE:
            ev.review_reason = REVIEW_LEGACY_NO_CODE
        elif job.completion_method not in AUTO_PAYABLE_COMPLETION_METHODS:
            ev.review_reason = REVIEW_COMPLETION_UNVERIFIED

    candidates = []
    if manager_amount > 0:
        candidates.append(Payee(role=ROLE_MANAGER, recipient=job.broker, amount=manager_amount))
    if shop_amount > 0:
        candidates.append(Payee(role=ROLE_SHOP, recipient=shop_owner, amount=shop_amount, assignment=assignment))
    for payee in candidates:
        if payee.recipient is None:
            payee.waiting_reason = WAIT_NO_RECIPIENT
        elif not can_receive_payouts(payee.recipient):
            payee.waiting_reason = WAIT_CANNOT_RECEIVE_PAYOUTS
        elif not verified_msisdn(payee.recipient):
            payee.waiting_reason = WAIT_PHONE_UNVERIFIED
        if payee.amount != payee.amount.to_integral_value():
            payee.review_reason = REVIEW_NOT_WHOLE_KES
    ev.payees = candidates
    return ev


def _overrides(payout: P) -> set[str]:
    return set((payout.metadata or {}).get("approved_overrides") or [])


def _attempts_this_round(payout: P) -> int:
    return payout.attempt_count - int((payout.metadata or {}).get("attempt_base") or 0)


def _set_waiting(payout: P, reason: str, *, source: str = SOURCE_AUTO) -> bool:
    if payout.waiting_reason == reason:
        return False
    previous = payout.waiting_reason
    payout.waiting_reason = reason
    payout.waiting_detail = WAITING_REASON_TEXT.get(reason, "")
    audit_entry(payout, event="waiting_reason", source=source, reason=reason, previous_reason=previous)
    return True


def _to_review(payout: P, reason: str, *, source: str, detail: str = "", **extra) -> None:
    transition(payout, P.STATUS_NEEDS_REVIEW, source=source, reason=reason, detail=detail or None, **extra)
    payout.waiting_reason = reason
    payout.waiting_detail = detail or WAITING_REASON_TEXT.get(reason, "")


# --- preparing rows -------------------------------------------------------------------

def _lock_job(job_id: int) -> ManagedJob:
    return ManagedJob.objects.select_for_update(of=("self",)).get(pk=job_id)


def _lock_payout(**filters) -> P | None:
    return P.objects.select_for_update(of=("self",)).filter(**filters).first()


def prepare_job_payouts(job_id: int) -> list[P]:
    """Create/refresh this job's payout rows and their waiting/review reasons."""
    rows = []
    with transaction.atomic():
        job = _lock_job(job_id)
        ev = evaluate_job(job)
        for payee in ev.payees:
            payout = _lock_payout(managed_job=job, recipient_role=payee.role)
            if payout is None:
                payout = P(
                    managed_job=job,
                    recipient_role=payee.role,
                    recipient=payee.recipient,
                    assignment=payee.assignment,
                    amount=payee.amount,
                    status=P.STATUS_PENDING,
                    disbursement_mode=P.DISBURSEMENT_AUTOMATIC,
                    metadata={"release_mode": "automatic"},
                )
                audit_entry(payout, event="created", source=SOURCE_AUTO, amount=str(payee.amount))
            elif payout.status not in {P.STATUS_PENDING, P.STATUS_FAILED}:
                rows.append(payout)
                continue  # in flight, final, on hold or awaiting review
            payout.recipient = payee.recipient
            payout.assignment = payee.assignment or payout.assignment
            payout.amount = payee.amount
            payout.disbursement_mode = P.DISBURSEMENT_AUTOMATIC
            review = ev.review_reason or payee.review_reason
            if review and review not in _overrides(payout):
                _to_review(payout, review, source=SOURCE_AUTO)
            else:
                _set_waiting(payout, ev.waiting_reason or payee.waiting_reason)
            payout.save()
            rows.append(payout)
    return rows


# --- sending ------------------------------------------------------------------------

def _nairobi_day_start(now):
    local = timezone.localtime(now)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _sent_today_total(now) -> Decimal:
    from django.db.models import Sum

    total = (
        P.objects.filter(
            disbursement_mode=P.DISBURSEMENT_AUTOMATIC,
            sent_at__gte=_nairobi_day_start(now),
            status__in=[P.STATUS_PROCESSING, P.STATUS_RELEASED, P.STATUS_NEEDS_REVIEW],
        ).aggregate(total=Sum("amount"))["total"]
    )
    return _money(total)


def _due_filter(now):
    from django.db.models import Q

    return (
        Q(status__in=[P.STATUS_PENDING, P.STATUS_FAILED], waiting_reason="", disbursement_mode=P.DISBURSEMENT_AUTOMATIC)
        & (Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    )


def send_payout(payout_id: int, *, now=None, client=None) -> str:
    """Send one payout through B2C if it is (still) due. Returns an outcome code."""
    now = now or timezone.now()
    job_id = P.objects.filter(pk=payout_id).values_list("managed_job_id", flat=True).first()
    if job_id is None:
        return "missing"
    with transaction.atomic():
        job = _lock_job(job_id)
        payout = _lock_payout(pk=payout_id)
        if payout is None or not P.objects.filter(_due_filter(now), pk=payout_id).exists():
            return "skipped"
        ev = evaluate_job(job)
        payee = next((p for p in ev.payees if p.role == payout.recipient_role), None)
        if payee is None:
            _set_waiting(payout, WAIT_NO_RECIPIENT)
            payout.save()
            return "waiting"
        overrides = _overrides(payout)
        review = ev.review_reason or payee.review_reason
        if review and review not in overrides:
            _to_review(payout, review, source=SOURCE_AUTO)
            payout.save()
            return "needs_review"
        waiting = ev.waiting_reason or payee.waiting_reason
        if waiting:
            _set_waiting(payout, waiting)
            payout.save()
            return "waiting"
        if payee.amount != payout.amount:
            _to_review(payout, REVIEW_AMOUNT_MISMATCH, source=SOURCE_AUTO)
            payout.save()
            return "needs_review"
        amount = payout.amount
        if amount > Decimal(_int_setting("AUTO_PAYOUT_MAX_AMOUNT", 50000)) and REVIEW_OVER_MAX not in overrides:
            _to_review(payout, REVIEW_OVER_MAX, source=SOURCE_AUTO, amount=str(amount))
            payout.save()
            return "needs_review"
        cap = Decimal(_int_setting("AUTO_PAYOUT_DAILY_CAP", 200000))
        if _sent_today_total(now) + amount > cap and REVIEW_DAILY_CAP not in overrides:
            _to_review(payout, REVIEW_DAILY_CAP, source=SOURCE_AUTO, amount=str(amount))
            payout.save()
            return "needs_review"
        msisdn = verified_msisdn(payee.recipient)
        payout.attempt_count += 1
        key = f"PRINTY-PO{payout.pk}-A{payout.attempt_count}"
        payout.idempotency_key = key
        payout.b2c_conversation_id = ""
        payout.sent_at = now
        payout.next_attempt_at = None
        transition(payout, P.STATUS_PROCESSING, source=SOURCE_AUTO, reason="b2c_request",
                   idempotency_key=key, amount=str(amount), attempt=payout.attempt_count)
        payout.save()
    # Committed: any other runner now sees ``processing`` and skips this payout.

    client = client or get_b2c_client()
    try:
        response = client.send(
            msisdn=msisdn,
            amount=int(amount),
            originator_conversation_id=key,
            remarks=f"Printy payout {job.managed_reference or job.id}",
            occasion=payout.recipient_role,
        )
    except B2CNotSent as exc:
        return _after_rejection(payout_id, key, str(exc), getattr(exc, "payload", None), now=now)
    except B2CAmbiguous as exc:
        _after_ambiguous(payout_id, key, str(exc))
        return "needs_review"
    except Exception as exc:  # unknown failure after the request may have left: treat as ambiguous
        logger.exception("Unexpected B2C error for payout %s", payout_id)
        _after_ambiguous(payout_id, key, f"unexpected error: {exc}")
        return "needs_review"

    with transaction.atomic():
        payout = _lock_payout(pk=payout_id)
        if payout is not None and payout.idempotency_key == key and payout.status == P.STATUS_PROCESSING:
            payout.b2c_conversation_id = str(response.get("ConversationID") or "")[:64]
            payout.provider_response = {"request": response}
            audit_entry(payout, event="b2c_accepted", source=SOURCE_AUTO,
                        conversation_id=payout.b2c_conversation_id, idempotency_key=key,
                        stub=bool(response.get("stub")) or None)
            payout.save()
    return "sent"


def _schedule_retry_or_review(payout: P, reason: str, *, now, source: str) -> str:
    if _attempts_this_round(payout) >= _int_setting("AUTO_PAYOUT_MAX_ATTEMPTS", 3):
        _to_review(payout, REVIEW_RETRIES_EXHAUSTED, source=source, detail=reason)
        return "needs_review"
    transition(payout, P.STATUS_FAILED, source=source, reason=reason)
    payout.failure_reason = reason
    payout.failed_at = now
    base = _int_setting("AUTO_PAYOUT_RETRY_BASE_SECONDS", 300)
    payout.next_attempt_at = now + timedelta(seconds=base * (2 ** max(_attempts_this_round(payout) - 1, 0)))
    return "failed"


def _after_rejection(payout_id: int, key: str, reason: str, payload, *, now) -> str:
    with transaction.atomic():
        payout = _lock_payout(pk=payout_id)
        if payout is None or payout.idempotency_key != key or payout.status != P.STATUS_PROCESSING:
            return "ignored"
        payout.provider_response = {"rejection": payload} if payload is not None else payout.provider_response
        outcome = _schedule_retry_or_review(payout, f"b2c_rejected: {reason}"[:500], now=now, source=SOURCE_AUTO)
        payout.save()
    return outcome


def _after_ambiguous(payout_id: int, key: str, reason: str) -> None:
    with transaction.atomic():
        payout = _lock_payout(pk=payout_id)
        if payout is None or payout.idempotency_key != key or payout.status != P.STATUS_PROCESSING:
            return
        _to_review(payout, REVIEW_AMBIGUOUS_SEND, source=SOURCE_AUTO, detail=reason[:500])
        payout.save()


# --- sweeps and the runner ------------------------------------------------------------

def sweep_result_timeouts(*, now=None) -> int:
    now = now or timezone.now()
    cutoff = now - timedelta(minutes=_int_setting("AUTO_PAYOUT_RESULT_TIMEOUT_MINUTES", 60))
    moved = 0
    for payout_id in P.objects.filter(status=P.STATUS_PROCESSING, sent_at__lt=cutoff).values_list("pk", flat=True):
        with transaction.atomic():
            payout = _lock_payout(pk=payout_id, status=P.STATUS_PROCESSING, sent_at__lt=cutoff)
            if payout is None:
                continue
            _to_review(payout, REVIEW_RESULT_TIMEOUT, source=SOURCE_SYSTEM)
            payout.save()
            moved += 1
    return moved


def cancel_dead_payouts() -> int:
    """Cancel unsent automatic payouts whose job was cancelled or refunded.

    A ``needs_review`` payout whose money may already have moved (ambiguous
    send, queue/result timeout) is left for an admin: cancelling it would hide
    a transfer that may have happened.
    """
    dead = (
        P.objects.filter(
            disbursement_mode=P.DISBURSEMENT_AUTOMATIC,
            status__in=[P.STATUS_PENDING, P.STATUS_FAILED, P.STATUS_NEEDS_REVIEW],
            managed_job__status=ManagedJobStatus.CANCELLED,
        )
        .exclude(status=P.STATUS_NEEDS_REVIEW, waiting_reason__in=POSSIBLY_PAID_REASONS)
        .values_list("pk", flat=True)
    )
    count = 0
    for payout_id in list(dead):
        with transaction.atomic():
            payout = _lock_payout(pk=payout_id)
            if payout is None or payout.status not in {P.STATUS_PENDING, P.STATUS_FAILED, P.STATUS_NEEDS_REVIEW}:
                continue
            if payout.status == P.STATUS_NEEDS_REVIEW and payout.waiting_reason in POSSIBLY_PAID_REASONS:
                continue
            transition(payout, P.STATUS_CANCELLED, source=SOURCE_SYSTEM, reason="job_cancelled")
            payout.save()
            count += 1
    return count


def candidate_job_ids(limit: int | None = None) -> list[int]:
    qs = (
        ManagedJob.objects.filter(status=ManagedJobStatus.COMPLETED)
        .exclude(payment_status=ManagedJobPaymentStatus.RELEASED)
        .order_by("completed_at", "id")
        .values_list("pk", flat=True)
    )
    return list(qs[:limit] if limit else qs)


def run_auto_payouts(*, now=None, client=None, limit: int | None = None) -> dict:
    """One cron pass. Sends nothing unless enabled and fully configured."""
    now = now or timezone.now()
    summary = {"enabled": bool(getattr(settings, "AUTO_PAYOUTS_ENABLED", False)), "sent": 0, "outcomes": {}}
    if not summary["enabled"]:
        return summary
    errors = config_errors()
    if errors:
        logger.error("Automatic payouts are enabled but misconfigured; nothing sent: %s", errors)
        summary["config_errors"] = errors
        return summary
    summary["timeouts"] = sweep_result_timeouts(now=now)
    summary["cancelled"] = cancel_dead_payouts()
    job_ids = candidate_job_ids(limit)
    for job_id in job_ids:
        prepare_job_payouts(job_id)
    summary["jobs_checked"] = len(job_ids)
    due_ids = list(P.objects.filter(_due_filter(now)).order_by("id").values_list("pk", flat=True))
    for payout_id in due_ids:
        outcome = send_payout(payout_id, now=now, client=client)
        summary["outcomes"][payout_id] = outcome
        if outcome == "sent":
            summary["sent"] += 1
    return summary


# --- B2C result / timeout callbacks -----------------------------------------------------

def _result_parameters(result: dict) -> dict:
    params = (result.get("ResultParameters") or {}).get("ResultParameter") or []
    if isinstance(params, dict):
        params = [params]
    out = {}
    for item in params:
        if isinstance(item, dict) and "Key" in item:
            out[str(item["Key"])] = item.get("Value")
    return out


def _maybe_mark_job_released(job_id: int, *, now) -> None:
    job = _lock_job(job_id)
    payouts = list(P.objects.filter(managed_job=job))
    if not payouts or any(p.status != P.STATUS_RELEASED for p in payouts):
        return
    if job.payment_status != ManagedJobPaymentStatus.RELEASED:
        job.payment_status = ManagedJobPaymentStatus.RELEASED
        job.save(update_fields=["payment_status", "updated_at"])
    record_job_status_event(
        managed_job=job,
        event_type=EVENT_PAYOUT_RELEASED_AUTO,
        actor=None,
        summary="Automatic payout released via M-Pesa B2C.",
        metadata={
            "payout_ids": [p.id for p in payouts],
            "receipts": [p.b2c_transaction_id or p.transfer_reference for p in payouts],
            "release_mode": "automatic",
            "money_summary": job_money_summary(job)["totals"],
        },
    )


def process_b2c_callback(payload: dict, *, kind: str = "result", now=None) -> str:
    """Apply an AUTHENTICATED B2C result/timeout callback. Returns an outcome code.

    Forged-but-authenticated or unknown bodies change nothing: the payout is
    matched by OriginatorConversationID (our per-request idempotency key) AND,
    when we stored one, ConversationID. A success must carry a receipt and
    exactly the payout amount; otherwise nothing changes (the timeout sweep
    later sends it to needs_review). Duplicates are no-ops.
    """
    now = now or timezone.now()
    if not isinstance(payload, dict):
        return "unknown"
    result = payload.get("Result") if isinstance(payload.get("Result"), dict) else payload
    occ = str(result.get("OriginatorConversationID") or "").strip()
    cid = str(result.get("ConversationID") or "").strip()
    if not occ:
        logger.warning("B2C %s callback without OriginatorConversationID ignored", kind)
        return "unknown"
    with transaction.atomic():
        payout = _lock_payout(idempotency_key=occ)
        if payout is None:
            logger.warning("B2C %s callback for unknown OriginatorConversationID=%s ignored", kind, occ)
            return "unknown"
        if payout.b2c_conversation_id and cid != payout.b2c_conversation_id:
            logger.warning("B2C %s callback ConversationID mismatch for payout %s ignored", kind, payout.pk)
            return "mismatch"
        if payout.status == P.STATUS_RELEASED:
            return "duplicate"
        if kind == "timeout":
            if payout.status != P.STATUS_PROCESSING:
                return "ignored"
            _to_review(payout, REVIEW_QUEUE_TIMEOUT, source=SOURCE_B2C_CALLBACK)
            payout.provider_response = {**(payout.provider_response or {}), "timeout": payload}
            payout.save()
            return "needs_review"
        code = str(result.get("ResultCode", "")).strip()
        if code == "0":
            if payout.status not in {P.STATUS_PROCESSING, P.STATUS_NEEDS_REVIEW}:
                return "ignored"
            params = _result_parameters(result)
            receipt = str(params.get("TransactionReceipt") or result.get("TransactionID") or "").strip()
            try:
                paid = Decimal(str(params.get("TransactionAmount")))
            except (InvalidOperation, ValueError, TypeError):
                paid = None
            if not receipt or paid is None or paid != payout.amount:
                logger.error(
                    "B2C success callback for payout %s rejected: receipt=%r amount=%r expected=%s",
                    payout.pk, receipt, params.get("TransactionAmount"), payout.amount,
                )
                return "rejected"
            transition(payout, P.STATUS_RELEASED, source=SOURCE_B2C_CALLBACK, reason="b2c_success",
                       receipt=receipt, amount=str(paid), conversation_id=cid or None)
            payout.b2c_conversation_id = payout.b2c_conversation_id or cid[:64]
            payout.b2c_transaction_id = receipt[:64]
            payout.transfer_reference = receipt[:100]
            payout.release_reference = payout.release_reference or f"B2C-{receipt}"[:100]
            payout.released_at = now
            payout.disbursement_mode = P.DISBURSEMENT_AUTOMATIC
            payout.waiting_reason = ""
            payout.waiting_detail = ""
            payout.provider_response = {**(payout.provider_response or {}), "result": payload}
            payout.save()
            _maybe_mark_job_released(payout.managed_job_id, now=now)
            return "released"
        if payout.status != P.STATUS_PROCESSING:
            return "ignored"
        payout.provider_response = {**(payout.provider_response or {}), "result": payload}
        outcome = _schedule_retry_or_review(
            payout, f"b2c_result_{code}: {result.get('ResultDesc') or ''}"[:500], now=now, source=SOURCE_B2C_CALLBACK
        )
        payout.save()
        return outcome


# --- super-admin actions ----------------------------------------------------------------

def approve_payout(payout: P, *, actor, confirm_amount, reason: str, confirm_not_paid: bool = False) -> P:
    """needs_review -> pending, waiving the reviewed check for this payout only."""
    reason = str(reason or "").strip()
    if not reason:
        raise PayoutAdminError("A reason is required.", code="reason_required")
    try:
        confirmed = Decimal(str(confirm_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise PayoutAdminError("confirm_amount must be the payout amount.", code="amount_confirmation_required") from None
    with transaction.atomic():
        _lock_job(payout.managed_job_id)
        payout = _lock_payout(pk=payout.pk)
        if payout.status != P.STATUS_NEEDS_REVIEW:
            raise PayoutAdminError("Only a payout in needs_review can be approved.", code="not_needs_review")
        if confirmed != payout.amount:
            raise PayoutAdminError(
                f"confirm_amount {confirmed} does not match the payout amount {payout.amount}.",
                code="amount_mismatch",
            )
        code = payout.waiting_reason
        if code in NON_APPROVABLE_REASONS:
            raise PayoutAdminError(
                f"'{code}' is a data problem; fix the job/split instead of approving.", code="not_approvable"
            )
        if code in POSSIBLY_PAID_REASONS and not confirm_not_paid:
            raise PayoutAdminError(
                "Money may already have moved for this payout. Check the M-Pesa statement, then send "
                "confirm_not_paid=true to send it again.",
                code="confirm_not_paid_required",
            )
        metadata = dict(payout.metadata or {})
        overrides = set(metadata.get("approved_overrides") or [])
        if code:
            overrides.add(code)
        metadata["approved_overrides"] = sorted(overrides)
        metadata["attempt_base"] = payout.attempt_count
        payout.metadata = metadata
        payout.reviewed_at = timezone.now()
        payout.reviewed_by = actor
        transition(payout, P.STATUS_PENDING, source=SOURCE_ADMIN_REVIEW, actor=actor, reason=reason,
                   approved_reason=code, confirmed_amount=str(confirmed),
                   confirm_not_paid=bool(confirm_not_paid) or None)
        payout.waiting_reason = ""
        payout.waiting_detail = ""
        payout.next_attempt_at = None
        payout.save()
    return payout


def retry_payout(payout: P, *, actor, reason: str) -> P:
    """failed -> pending for a fresh round of bounded attempts."""
    reason = str(reason or "").strip()
    if not reason:
        raise PayoutAdminError("A reason is required.", code="reason_required")
    with transaction.atomic():
        _lock_job(payout.managed_job_id)
        payout = _lock_payout(pk=payout.pk)
        if payout.status != P.STATUS_FAILED:
            raise PayoutAdminError("Only a failed payout can be retried.", code="not_failed")
        metadata = dict(payout.metadata or {})
        metadata["attempt_base"] = payout.attempt_count
        payout.metadata = metadata
        transition(payout, P.STATUS_PENDING, source=SOURCE_ADMIN_REVIEW, actor=actor, reason=reason)
        payout.next_attempt_at = None
        payout.save()
    return payout


# --- read-only summaries -------------------------------------------------------------------

def payout_row(payout: P, *, admin: bool = True) -> dict:
    row = {
        "id": payout.id,
        "job_id": payout.managed_job_id,
        "job_reference": getattr(payout.managed_job, "managed_reference", "") or "",
        "recipient_role": payout.recipient_role,
        "amount": str(payout.amount),
        "currency": payout.currency,
        "status": payout.status,
        "disbursement_mode": payout.disbursement_mode,
        "waiting_reason": payout.waiting_reason,
        "waiting_detail": payout.waiting_detail or WAITING_REASON_TEXT.get(payout.waiting_reason, ""),
        "released_at": payout.released_at,
        "transfer_reference": payout.transfer_reference,
    }
    if admin:
        row.update(
            {
                "recipient_id": payout.recipient_id,
                "attempt_count": payout.attempt_count,
                "next_attempt_at": payout.next_attempt_at,
                "sent_at": payout.sent_at,
                "idempotency_key": payout.idempotency_key,
                "b2c_conversation_id": payout.b2c_conversation_id,
                "failure_reason": payout.failure_reason,
                "reviewed_at": payout.reviewed_at,
                "reviewed_by": payout.reviewed_by_id,
                "audit_log": payout.audit_log,
            }
        )
    return row


def job_money_summary(job: ManagedJob) -> dict:
    """Admin, read-only: who paid what and who gets what for one job."""
    from jobs.settlement_compat import get_financial_split_for_job

    split = get_financial_split_for_job(job) or {}
    paid = sum((_money(p.amount) for p in _job_payments(job).filter(status=Payment.STATUS_PAID)), Decimal("0.00"))
    payouts = list(P.objects.filter(managed_job=job).order_by("recipient_role"))
    released = sum((_money(p.amount) for p in payouts if p.status == P.STATUS_RELEASED), Decimal("0.00"))
    shop = _money(split.get("shop_payout"))
    manager = _money(split.get("broker_payout"))
    fee = _money(split.get("printy_fee") if split else job.printy_fee)
    client_total = _money(split.get("client_total") if split else job.client_total)
    snapshot = job.operational_snapshot or {}
    return {
        "job_id": job.id,
        "job_reference": job.managed_reference,
        "status": job.status,
        "completion_method": job.completion_method,
        "payment_status": job.payment_status,
        "totals": {
            "client_total": str(client_total),
            "client_paid": str(paid),
            "shop_payout": str(shop),
            "manager_payout": str(manager),
            "printy_fee": str(fee),
            "released": str(released),
            "unallocated": str(client_total - shop - manager - fee),
            "payouts_within_client_total": shop + manager <= client_total,
        },
        "holds": {
            "payout_hold": bool(job.payout_hold),
            "lateness_hold": snapshot.get("lateness_hold"),
            "rush_premium": str(_money(job.rush_premium)),
            "rush_premium_status": job.rush_premium_status,
            "rush_premium_blocks_auto_payout": False,
            "dispute_open": dispute_open(job),
        },
        "split_source": split.get("source", "") if split else "",
        "payouts": [payout_row(p) for p in payouts],
        "auto_payouts_enabled": bool(getattr(settings, "AUTO_PAYOUTS_ENABLED", False)),
    }
