"""Assignment action helpers for managed production workflow."""

from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from jobs.audit_services import (
    EVENT_ASSIGNMENT_STATUS_CHANGED,
    EVENT_ISSUE_RAISED,
    record_job_status_event,
)
from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobAssignmentStatus,
    ManagedJobExceptionStatus,
    ManagedJobStatus,
)
from jobs.models import JobAssignment
from jobs.payment_services import initialize_settlement_for_managed_job
from notifications.models import Notification
from notifications.services import notify_quote_event
from production.models import ProductionOrder


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sync_production_order_status(*, assignment: JobAssignment, status: str) -> None:
    production_order = assignment.production_order
    if production_order is None:
        return

    update_fields = ["updated_at"]
    if status == JobAssignmentStatus.ACCEPTED and production_order.status != ProductionOrder.PENDING:
        production_order.status = ProductionOrder.PENDING
        update_fields.append("status")
    elif status == JobAssignmentStatus.IN_PRODUCTION and production_order.status != ProductionOrder.IN_PROGRESS:
        production_order.status = ProductionOrder.IN_PROGRESS
        update_fields.append("status")
    elif status == JobAssignmentStatus.FINISHING and production_order.status != ProductionOrder.IN_PROGRESS:
        production_order.status = ProductionOrder.IN_PROGRESS
        update_fields.append("status")
    elif status == JobAssignmentStatus.READY and production_order.status != ProductionOrder.READY:
        production_order.status = ProductionOrder.READY
        update_fields.append("status")
    elif status == JobAssignmentStatus.COMPLETED:
        if production_order.status != ProductionOrder.COMPLETED:
            production_order.status = ProductionOrder.COMPLETED
            update_fields.append("status")
        if production_order.completed_at is None:
            production_order.completed_at = timezone.now()
            update_fields.append("completed_at")
    elif status == JobAssignmentStatus.REJECTED and production_order.status != ProductionOrder.PENDING:
        production_order.status = ProductionOrder.PENDING
        update_fields.append("status")
    elif status == JobAssignmentStatus.CANCELLED and production_order.status != ProductionOrder.CANCELLED:
        production_order.status = ProductionOrder.CANCELLED
        update_fields.append("status")

    if len(update_fields) > 1:
        production_order.save(update_fields=update_fields)


def _ensure_current_status(*, assignment: JobAssignment, allowed: set[str], action: str) -> None:
    if assignment.status not in allowed:
        allowed_labels = ", ".join(sorted(allowed))
        raise ValueError(f"Cannot {action} from '{assignment.status}'. Allowed states: {allowed_labels}.")


def _notify_progress(*, assignment: JobAssignment, actor, message: str) -> None:
    managed_job = assignment.managed_job
    recipients = [managed_job.broker, managed_job.client]
    for recipient in recipients:
        if recipient and getattr(recipient, "id", None) != getattr(actor, "id", None):
            notify_quote_event(
                recipient=recipient,
                notification_type=Notification.JOB_STATUS_UPDATED,
                message=message,
                object_type="managed_job",
                object_id=managed_job.id,
                actor=actor,
            )


def _transition_assignment(
    *,
    assignment: JobAssignment,
    actor=None,
    status: str,
    managed_status: str,
    managed_assignment_status: str,
    summary: str,
    note: str = "",
    exception_status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> JobAssignment:
    update_fields = ["status", "updated_at"]
    assignment.status = status
    snapshot = {
        **_as_dict(assignment.operational_snapshot),
        "last_action_note": note,
        "last_action_status": status,
        "last_action_at": timezone.now().isoformat(),
    }
    assignment.operational_snapshot = snapshot
    update_fields.append("operational_snapshot")
    if status == JobAssignmentStatus.ACCEPTED and assignment.accepted_at is None:
        assignment.accepted_at = timezone.now()
        update_fields.append("accepted_at")
    if status == JobAssignmentStatus.REJECTED:
        assignment.rejected_at = timezone.now()
        update_fields.append("rejected_at")
    assignment.save(update_fields=update_fields)

    managed_job = assignment.managed_job
    managed_update_fields = ["status", "assignment_status", "updated_at"]
    managed_job.status = managed_status
    managed_job.assignment_status = managed_assignment_status
    if exception_status and managed_job.exception_status != exception_status:
        managed_job.exception_status = exception_status
        managed_update_fields.append("exception_status")
    if status == JobAssignmentStatus.ACCEPTED and managed_job.assigned_at is None:
        managed_job.assigned_at = timezone.now()
        managed_update_fields.append("assigned_at")
    if status == JobAssignmentStatus.IN_PRODUCTION and managed_job.production_started_at is None:
        managed_job.production_started_at = timezone.now()
        managed_update_fields.append("production_started_at")
    if status == JobAssignmentStatus.READY and managed_job.ready_at is None:
        managed_job.ready_at = timezone.now()
        managed_update_fields.append("ready_at")
    if status == JobAssignmentStatus.COMPLETED and managed_job.completed_at is None:
        managed_job.completed_at = timezone.now()
        managed_update_fields.append("completed_at")
    managed_job.save(update_fields=managed_update_fields)

    _sync_production_order_status(assignment=assignment, status=status)
    record_job_status_event(
        managed_job=managed_job,
        assignment=assignment,
        actor=actor,
        event_type=EVENT_ASSIGNMENT_STATUS_CHANGED,
        summary=summary,
        metadata={"status": status, "note": note, **(metadata or {})},
    )
    return assignment


@transaction.atomic
def accept_assignment(*, assignment: JobAssignment, actor=None, note: str = "") -> JobAssignment:
    _ensure_current_status(assignment=assignment, allowed={JobAssignmentStatus.PENDING}, action="accept assignment")
    assignment = _transition_assignment(
        assignment=assignment,
        actor=actor,
        status=JobAssignmentStatus.ACCEPTED,
        managed_status=ManagedJobStatus.ASSIGNED,
        managed_assignment_status=ManagedJobAssignmentStatus.ASSIGNED,
        summary="Assignment accepted.",
        note=note,
    )
    _notify_progress(assignment=assignment, actor=actor, message=f"{assignment.managed_job.managed_reference or 'Managed job'} was accepted by production.")
    return assignment


@transaction.atomic
def reject_assignment(*, assignment: JobAssignment, actor=None, note: str = "") -> JobAssignment:
    _ensure_current_status(
        assignment=assignment,
        allowed={JobAssignmentStatus.PENDING, JobAssignmentStatus.ACCEPTED},
        action="reject assignment",
    )
    return _transition_assignment(
        assignment=assignment,
        actor=actor,
        status=JobAssignmentStatus.REJECTED,
        managed_status=ManagedJobStatus.PAYMENT_CONFIRMED,
        managed_assignment_status=ManagedJobAssignmentStatus.REASSIGNMENT_REQUIRED,
        exception_status=ManagedJobExceptionStatus.OPS_REVIEW,
        summary="Assignment rejected.",
        note=note,
    )


@transaction.atomic
def mark_assignment_in_production(*, assignment: JobAssignment, actor=None, note: str = "") -> JobAssignment:
    _ensure_current_status(assignment=assignment, allowed={JobAssignmentStatus.ACCEPTED}, action="start printing")
    assignment = _transition_assignment(
        assignment=assignment,
        actor=actor,
        status=JobAssignmentStatus.IN_PRODUCTION,
        managed_status=ManagedJobStatus.IN_PRODUCTION,
        managed_assignment_status=ManagedJobAssignmentStatus.ASSIGNED,
        summary="Assignment moved into production.",
        note=note,
    )
    _notify_progress(assignment=assignment, actor=actor, message=f"{assignment.managed_job.managed_reference or 'Managed job'} moved into printing.")
    return assignment


@transaction.atomic
def mark_assignment_finishing(*, assignment: JobAssignment, actor=None, note: str = "") -> JobAssignment:
    _ensure_current_status(assignment=assignment, allowed={JobAssignmentStatus.IN_PRODUCTION}, action="start finishing")
    assignment = _transition_assignment(
        assignment=assignment,
        actor=actor,
        status=JobAssignmentStatus.FINISHING,
        managed_status=ManagedJobStatus.FINISHING,
        managed_assignment_status=ManagedJobAssignmentStatus.ASSIGNED,
        summary="Assignment moved into finishing.",
        note=note,
        metadata={"finishing_started_at": timezone.now().isoformat()},
    )
    assignment.operational_snapshot = {
        **_as_dict(assignment.operational_snapshot),
        "finishing_started_at": timezone.now().isoformat(),
    }
    assignment.save(update_fields=["operational_snapshot", "updated_at"])
    _notify_progress(assignment=assignment, actor=actor, message=f"{assignment.managed_job.managed_reference or 'Managed job'} moved into finishing.")
    return assignment


@transaction.atomic
def mark_assignment_ready(*, assignment: JobAssignment, actor=None, note: str = "") -> JobAssignment:
    _ensure_current_status(
        assignment=assignment,
        allowed={JobAssignmentStatus.IN_PRODUCTION, JobAssignmentStatus.FINISHING},
        action="mark assignment ready",
    )
    assignment = _transition_assignment(
        assignment=assignment,
        actor=actor,
        status=JobAssignmentStatus.READY,
        managed_status=ManagedJobStatus.READY,
        managed_assignment_status=ManagedJobAssignmentStatus.ASSIGNED,
        summary="Assignment marked ready.",
        note=note,
    )
    _notify_progress(assignment=assignment, actor=actor, message=f"{assignment.managed_job.managed_reference or 'Managed job'} is ready for collection.")
    return assignment


@transaction.atomic
def mark_assignment_completed(*, assignment: JobAssignment, actor=None, note: str = "") -> JobAssignment:
    _ensure_current_status(assignment=assignment, allowed={JobAssignmentStatus.READY}, action="complete assignment")
    assignment = _transition_assignment(
        assignment=assignment,
        actor=actor,
        status=JobAssignmentStatus.COMPLETED,
        managed_status=ManagedJobStatus.COMPLETED,
        managed_assignment_status=ManagedJobAssignmentStatus.ASSIGNED,
        summary="Assignment marked completed.",
        note=note,
    )
    managed_job = assignment.managed_job
    initialize_settlement_for_managed_job(managed_job=managed_job)
    _notify_progress(assignment=assignment, actor=actor, message=f"{managed_job.managed_reference or 'Managed job'} is marked completed.")
    return assignment


@transaction.atomic
def report_assignment_issue(*, assignment: JobAssignment, actor=None, note: str = "") -> JobAssignment:
    managed_job = assignment.managed_job
    assignment.operational_snapshot = {
        **_as_dict(assignment.operational_snapshot),
        "issue_note": note,
        "issue_reported_at": timezone.now().isoformat(),
    }
    assignment.save(update_fields=["operational_snapshot", "updated_at"])
    managed_job.exception_status = ManagedJobExceptionStatus.PRODUCTION_ISSUE
    managed_job.ops_review_required = True
    managed_job.production_issue_flag = True
    managed_job.save(update_fields=["exception_status", "ops_review_required", "production_issue_flag", "updated_at"])
    record_job_status_event(
        managed_job=managed_job,
        assignment=assignment,
        actor=actor,
        event_type=EVENT_ISSUE_RAISED,
        summary="Production issue reported.",
        metadata={"note": note, "status": assignment.status},
    )
    if managed_job.created_by and getattr(managed_job.created_by, "id", None) != getattr(actor, "id", None):
        notify_quote_event(
            recipient=managed_job.created_by,
            notification_type=Notification.JOB_STATUS_UPDATED,
            message=f"{managed_job.managed_reference or 'Managed job'} has a reported production issue.",
            object_type="managed_job",
            object_id=managed_job.id,
            actor=actor,
        )
    return assignment
