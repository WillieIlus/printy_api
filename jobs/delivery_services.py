"""Delivery and client-completion service actions for the managed workflow.

These are the canonical handlers for Blocker #2/#3: unified delivery tracking
(mark-delivered) and client accept-on-finish (confirm-completion). Ownership is
governed by TRANSITION_OWNER_RULES in jobs.workflow (DELIVERED -> ops/shop,
COMPLETED -> ops/client).
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from jobs.assignment_services import mark_assignment_completed
from jobs.audit_services import EVENT_JOB_COMPLETED, EVENT_JOB_DELIVERED, record_job_status_event
from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobExceptionStatus,
    ManagedJobStatus,
)
from jobs.handover import (
    MAX_FAILED_ATTEMPTS,
    HandoverCodeError,
    handover_codes_match,
    validate_new_handover_code,
)
from jobs.models import JobAssignment, ManagedJob
from jobs.notify_services import notify_managed_job_recipients
from jobs.payment_services import initialize_settlement_for_managed_job
from production.models import ProductionOrder


def _active_assignment(managed_job: ManagedJob) -> JobAssignment | None:
    return managed_job.assignments.filter(reassigned_from__isnull=True).order_by("-id").first()


def _sync_production_order_delivery(*, managed_job: ManagedJob, delivered: bool = False, completed: bool = False) -> None:
    assignment = _active_assignment(managed_job)
    production_order = assignment.production_order if assignment else None
    if production_order is None:
        production_order = getattr(managed_job, "source_production_order", None)
    if production_order is None:
        return
    update_fields = ["updated_at"]
    if delivered and production_order.delivery_status != ProductionOrder.DELIVERY_DELIVERED:
        production_order.delivery_status = ProductionOrder.DELIVERY_DELIVERED
        update_fields.append("delivery_status")
    if (delivered or completed) and production_order.delivered_at is None:
        production_order.delivered_at = timezone.now()
        update_fields.append("delivered_at")
    if completed and production_order.status in {ProductionOrder.READY, ProductionOrder.IN_PROGRESS}:
        production_order.status = ProductionOrder.COMPLETED
        production_order.completed_at = timezone.now()
        update_fields.extend(["status", "completed_at"])
    if len(update_fields) > 1:
        production_order.save(update_fields=update_fields)


def _delivery_message(managed_job: ManagedJob) -> str:
    return f"Order {managed_job.managed_reference or 'MJ'} has been delivered."


def _completion_message(managed_job: ManagedJob, *, confirmed_by_label: str) -> str:
    return f"Order {managed_job.managed_reference or 'MJ'} was accepted as complete by the {confirmed_by_label}."


@transaction.atomic
def mark_managed_job_delivered(*, managed_job: ManagedJob, actor=None, note: str = "", handover_code=None) -> ManagedJob:
    """Record dispatch/delivery of a finished managed job ("mark dispatched").

    Allowed owners: OPS_ACTOR, SHOP_ACTOR, PARTNER_ACTOR. Precondition statuses:
    in_production, finishing, ready (delivered/completed are idempotent no-ops,
    and never change the recorded handover code). A handover code is required
    (Prompt 7): the buyer must enter it to complete the job.
    """
    if managed_job.status in {ManagedJobStatus.DELIVERED, ManagedJobStatus.COMPLETED, ManagedJobStatus.CANCELLED, ManagedJobStatus.DISPUTED}:
        return managed_job
    if managed_job.status not in {
        ManagedJobStatus.IN_PRODUCTION,
        ManagedJobStatus.FINISHING,
        ManagedJobStatus.READY,
    }:
        raise ValueError(
            f"Cannot mark delivery from '{managed_job.status}'. Allowed states: in_production, finishing, ready."
        )
    code = validate_new_handover_code(handover_code)

    managed_job.status = ManagedJobStatus.DELIVERED
    managed_job.delivered_at = timezone.now()
    managed_job.handover_code = code
    managed_job.handover_code_set_at = managed_job.delivered_at
    managed_job.handover_code_set_by = actor if getattr(actor, "pk", None) else None
    managed_job.handover_failed_attempts = 0
    update_fields = [
        "status",
        "delivered_at",
        "handover_code",
        "handover_code_set_at",
        "handover_code_set_by",
        "handover_failed_attempts",
        "updated_at",
    ]
    if note:
        managed_job.operational_snapshot = {
            **managed_job.operational_snapshot,
            "delivery_note": note,
            "delivered_at": managed_job.delivered_at.isoformat(),
        }
        update_fields.append("operational_snapshot")
    managed_job.save(update_fields=update_fields)

    _sync_production_order_delivery(managed_job=managed_job, delivered=True)
    record_job_status_event(
        managed_job=managed_job,
        actor=actor,
        event_type=EVENT_JOB_DELIVERED,
        summary=_delivery_message(managed_job),
        metadata={"status": managed_job.status, "delivered_at": managed_job.delivered_at.isoformat(), "note": note},
    )
    notify_managed_job_recipients(managed_job=managed_job, actor=actor, message=_delivery_message(managed_job))
    return managed_job


def confirm_managed_job_completed(*, managed_job: ManagedJob, actor=None, note: str = "", handover_code=None) -> ManagedJob:
    """The buyer (or ops on the buyer's behalf) confirms receipt: "I received it".

    Allowed owners: OPS_ACTOR, CLIENT_ACTOR. Precondition status: delivered
    (the printer has marked it dispatched); completed is an idempotent no-op.
    The handover code recorded at dispatch must be entered (Prompt 7). A wrong
    code is counted and the job stays delivered; after MAX_FAILED_ATTEMPTS the
    code is locked. A job dispatched before handover codes existed (no code on
    record) can still be confirmed by the buyer, but is marked
    ``buyer_no_code`` so automatic payouts skip it.

    Not wrapped in one transaction on purpose: a wrong-code attempt is saved
    even though the call fails.
    """
    if managed_job.status == ManagedJobStatus.COMPLETED:
        return managed_job
    if managed_job.status != ManagedJobStatus.DELIVERED:
        raise ValueError(
            f"Cannot confirm completion from '{managed_job.status}'. Allowed states: delivered "
            "(the printer must mark it dispatched first)."
        )

    error = None
    with transaction.atomic():
        managed_job = ManagedJob.objects.select_for_update(of=("self",)).get(pk=managed_job.pk)
        if managed_job.status == ManagedJobStatus.COMPLETED:
            return managed_job
        if managed_job.status != ManagedJobStatus.DELIVERED:
            raise ValueError(f"Cannot confirm completion from '{managed_job.status}'. Allowed states: delivered.")
        if managed_job.handover_code:
            if managed_job.handover_failed_attempts >= MAX_FAILED_ATTEMPTS:
                raise HandoverCodeError(
                    "Too many wrong handover codes. Contact Printy support to complete this job.",
                    code="handover_locked",
                )
            if not handover_codes_match(managed_job.handover_code, handover_code):
                managed_job.handover_failed_attempts += 1
                managed_job.save(update_fields=["handover_failed_attempts", "updated_at"])
                remaining = MAX_FAILED_ATTEMPTS - managed_job.handover_failed_attempts
                error = HandoverCodeError(
                    "That handover code does not match. Check the code that came with your parcel."
                    + (f" {remaining} attempt(s) left." if remaining > 0 else " The code is now locked; contact Printy support."),
                    code="handover_code_mismatch" if str(handover_code or "").strip() else "handover_code_required",
                )
            else:
                managed_job.completion_method = ManagedJob.COMPLETION_BUYER_HANDOVER
        else:
            managed_job.completion_method = ManagedJob.COMPLETION_BUYER_NO_CODE
        if error is None:
            _complete_after_handover(managed_job=managed_job, actor=actor, note=note)
    if error is not None:
        raise error
    return managed_job


def _complete_after_handover(*, managed_job: ManagedJob, actor, note: str) -> None:
    now = timezone.now()
    managed_job.handover_confirmed_at = now
    managed_job.handover_confirmed_by = actor if getattr(actor, "pk", None) else None
    managed_job.save(update_fields=["handover_confirmed_at", "handover_confirmed_by", "completion_method", "updated_at"])
    managed_job.status = ManagedJobStatus.COMPLETED
    managed_job.completed_at = now
    if managed_job.exception_status == ManagedJobExceptionStatus.DELIVERY_ISSUE:
        managed_job.exception_status = ManagedJobExceptionStatus.CLEAR
        managed_job.delivery_issue_flag = False
        managed_job.save(
            update_fields=["status", "completed_at", "exception_status", "delivery_issue_flag", "updated_at"]
        )
    else:
        managed_job.save(update_fields=["status", "completed_at", "updated_at"])

    _sync_production_order_delivery(managed_job=managed_job, delivered=True, completed=True)

    assignment = _active_assignment(managed_job)
    if assignment is not None and assignment.status == JobAssignmentStatus.READY:
        mark_assignment_completed(assignment=assignment, actor=actor, note=note)

    if note:
        managed_job.operational_snapshot = {
            **managed_job.operational_snapshot,
            "completion_note": note,
            "completed_at": managed_job.completed_at.isoformat(),
        }
        managed_job.save(update_fields=["operational_snapshot", "updated_at"])

    initialize_settlement_for_managed_job(managed_job=managed_job)
    record_job_status_event(
        managed_job=managed_job,
        actor=actor,
        event_type=EVENT_JOB_COMPLETED,
        summary=f"Job accepted as complete by the client.",
        metadata={
            "status": managed_job.status,
            "completed_at": managed_job.completed_at.isoformat(),
            "note": note,
            "completion_method": managed_job.completion_method,
        },
    )
    notify_managed_job_recipients(
        managed_job=managed_job,
        actor=actor,
        message=_completion_message(managed_job, confirmed_by_label="client"),
    )