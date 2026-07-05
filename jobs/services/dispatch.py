from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from jobs.audit_services import EVENT_ASSIGNMENT_CREATED, record_job_status_event
from jobs.artwork_confirmation import require_artwork_confirmation_dispatch_ready
from jobs.choices import ManagedJobAssignmentStatus, ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import JobAssignment, ManagedJob
from notifications.models import Notification
from notifications.services import notify_quote_event


logger = logging.getLogger(__name__)


def _notify_shop_dispatched(*, managed_job: ManagedJob, target_shop, dispatched_by) -> None:
    recipient = getattr(target_shop, "owner", None)
    if not recipient or getattr(recipient, "id", None) == getattr(dispatched_by, "id", None):
        return
    message = f"{managed_job.managed_reference or 'Managed job'} has been dispatched to your production queue."
    if Notification.objects.filter(
        user=recipient,
        notification_type=Notification.JOB_STATUS_UPDATED,
        object_type="managed_job",
        object_id=managed_job.id,
        message=message,
    ).exists():
        return
    notify_quote_event(
        recipient=recipient,
        notification_type=Notification.JOB_STATUS_UPDATED,
        message=message,
        object_type="managed_job",
        object_id=managed_job.id,
        actor=dispatched_by,
    )


@transaction.atomic
def dispatch_job_to_shop(*, managed_job: ManagedJob, dispatched_by, shop=None, notes: str = "") -> JobAssignment:
    managed_job = ManagedJob.objects.select_for_update(of=("self",)).select_related("source_quote", "assigned_shop").get(pk=managed_job.pk)
    if managed_job.payment_status != ManagedJobPaymentStatus.CONFIRMED and managed_job.status != ManagedJobStatus.PAYMENT_CONFIRMED:
        raise ValidationError("Client payment must be confirmed before dispatch.")
    require_artwork_confirmation_dispatch_ready(managed_job)
    if managed_job.dispatched_at is not None:
        existing = managed_job.assignments.filter(reassigned_from__isnull=True).first()
        if existing is not None:
            return existing
        raise ValidationError("This job has already been dispatched.")

    target_shop = shop or managed_job.assigned_shop or getattr(getattr(managed_job, "source_quote", None), "shop", None)
    if target_shop is None:
        raise ValidationError("No shop assigned for dispatch.")

    split = getattr(getattr(managed_job, "source_quote", None), "financial_split", None)
    if split is None:
        raise ValidationError("Managed job needs QuoteFinancialSplit before dispatch.")
    shop_payout = split.shop_payout

    assignment = JobAssignment.objects.filter(
        managed_job=managed_job,
        reassigned_from__isnull=True,
    ).first()
    if assignment is None:
        assignment = JobAssignment.objects.create(
            managed_job=managed_job,
            assigned_shop=target_shop,
            source_quote=managed_job.source_quote,
            shop_payout=shop_payout,
            urgency_type=managed_job.urgency_type,
            operational_priority_level=managed_job.operational_priority_level,
            assignment_notes=notes,
            requested_deadline=managed_job.requested_deadline,
            operational_snapshot={
                "managed_job_id": managed_job.id,
                "managed_reference": managed_job.managed_reference,
                "source_quote_id": managed_job.source_quote_id,
                "assigned_shop_id": target_shop.id,
            },
        )
    else:
        assignment.assigned_shop = target_shop
        assignment.shop_payout = shop_payout
        if notes:
            assignment.assignment_notes = notes
        assignment.save(update_fields=["assigned_shop", "shop_payout", "assignment_notes", "updated_at"])

    managed_job.assigned_shop = target_shop
    managed_job.dispatched_at = timezone.now()
    managed_job.dispatched_by = dispatched_by
    managed_job.status = ManagedJobStatus.ASSIGNED
    managed_job.assignment_status = ManagedJobAssignmentStatus.ASSIGNED
    managed_job.save(
        update_fields=[
            "assigned_shop",
            "dispatched_at",
            "dispatched_by",
            "status",
            "assignment_status",
            "updated_at",
        ]
    )
    record_job_status_event(
        managed_job=managed_job,
        assignment=assignment,
        actor=dispatched_by,
        event_type=EVENT_ASSIGNMENT_CREATED,
        summary=f"Managed job dispatched to {target_shop.name}.",
        metadata={"assigned_shop_id": target_shop.id, "source_quote_id": managed_job.source_quote_id},
    )
    _notify_shop_dispatched(managed_job=managed_job, target_shop=target_shop, dispatched_by=dispatched_by)
    return assignment


def ensure_job_assignment_for_paid_job(
    *,
    managed_job: ManagedJob,
    dispatched_by=None,
    notes: str = "Auto-dispatched after payment confirmation",
) -> JobAssignment | None:
    managed_job = (
        ManagedJob.objects.select_related("source_quote", "source_quote_request", "assigned_shop", "broker")
        .filter(pk=managed_job.pk)
        .first()
    )
    if managed_job is None:
        return None

    active_assignment = managed_job.assignments.filter(reassigned_from__isnull=True).first()
    if active_assignment is not None:
        return active_assignment
    if managed_job.payment_status != ManagedJobPaymentStatus.CONFIRMED and managed_job.status != ManagedJobStatus.PAYMENT_CONFIRMED:
        return None
    if not managed_job.assigned_shop_id:
        return None
    if managed_job.dispatched_at is not None:
        logger.warning(
            "Paid managed job has dispatched_at but no active assignment managed_job_id=%s",
            managed_job.id,
        )
        return None

    actor = (
        dispatched_by
        or managed_job.broker
        or getattr(getattr(managed_job, "source_quote_request", None), "assigned_manager", None)
        or getattr(getattr(managed_job, "source_quote", None), "created_by", None)
    )
    assignment = dispatch_job_to_shop(
        managed_job=managed_job,
        dispatched_by=actor,
        shop=managed_job.assigned_shop,
        notes=notes,
    )
    logger.info(
        "Ensured active assignment for paid managed_job_id=%s assignment_id=%s shop_id=%s",
        managed_job.id,
        assignment.id,
        assignment.assigned_shop_id,
    )
    return assignment
