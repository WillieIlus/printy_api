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
def mark_managed_job_delivered(*, managed_job: ManagedJob, actor=None, note: str = "") -> ManagedJob:
    """Record delivery of a finished managed job.

    Allowed owners: OPS_ACTOR, SHOP_ACTOR, PARTNER_ACTOR. Precondition statuses:
    in_production, finishing, ready (delivered/completed are idempotent no-ops).
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

    managed_job.status = ManagedJobStatus.DELIVERED
    managed_job.delivered_at = timezone.now()
    if note:
        managed_job.operational_snapshot = {
            **managed_job.operational_snapshot,
            "delivery_note": note,
            "delivered_at": managed_job.delivered_at.isoformat(),
        }
        managed_job.save(update_fields=["status", "delivered_at", "operational_snapshot", "updated_at"])
    else:
        managed_job.save(update_fields=["status", "delivered_at", "updated_at"])

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


@transaction.atomic
def confirm_managed_job_completed(*, managed_job: ManagedJob, actor=None, note: str = "") -> ManagedJob:
    """Client (or ops) accepts the job as finished and closes it out.

    Allowed owners: OPS_ACTOR, CLIENT_ACTOR. Precondition statuses: ready,
    delivered (completed is an idempotent no-op).
    """
    if managed_job.status == ManagedJobStatus.COMPLETED:
        return managed_job
    if managed_job.status not in {ManagedJobStatus.READY, ManagedJobStatus.DELIVERED}:
        raise ValueError(
            f"Cannot confirm completion from '{managed_job.status}'. Allowed states: ready, delivered."
        )

    managed_job.status = ManagedJobStatus.COMPLETED
    managed_job.completed_at = timezone.now()
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
        metadata={"status": managed_job.status, "completed_at": managed_job.completed_at.isoformat(), "note": note},
    )
    notify_managed_job_recipients(
        managed_job=managed_job,
        actor=actor,
        message=_completion_message(managed_job, confirmed_by_label="client"),
    )
    return managed_job