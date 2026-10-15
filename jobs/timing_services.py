"""Write-side services for law Part 4 timeliness (6.2-6.6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from jobs.models import JobEvent, ManagedJob
from jobs.timing import (
    attribute_delay,
    compute_due_date,
    current_stage_for_job,
    stage_owner,
)
from jobs.timing_config import OWNER_BUYER
from quotes.models import QuoteDateRevision


# --- append-only event log (law 6.3) --------------------------------------
@transaction.atomic
def record_job_event(
    *,
    managed_job: ManagedJob,
    stage: str,
    event: str,
    actor=None,
    actor_role: str = "",
    source: str = JobEvent.SOURCE_SYSTEM,
    occurred_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
    references: JobEvent | None = None,
) -> JobEvent:
    """Append one immutable event to the job event log (law 6.3)."""
    return JobEvent.objects.create(
        managed_job=managed_job,
        stage=stage or "",
        event=event,
        actor=actor,
        actor_role=actor_role or "",
        occurred_at=occurred_at or timezone.now(),
        source=source,
        metadata=metadata or {},
        references=references,
    )


@transaction.atomic
def record_correction(
    *,
    original: JobEvent,
    actor=None,
    actor_role: str = "",
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> JobEvent:
    """Correct an event by appending a new referencing event (law 6.3)."""
    return record_job_event(
        managed_job=original.managed_job,
        stage=original.stage,
        event=JobEvent.CORRECTION,
        actor=actor,
        actor_role=actor_role,
        source=JobEvent.SOURCE_SYSTEM,
        metadata={"reason": reason, "corrects_event_id": original.id, **(metadata or {})},
        references=original,
    )


# --- due date placement (law 6.2) -----------------------------------------
def sync_job_timing(managed_job: ManagedJob, *, due: datetime | None = None) -> ManagedJob:
    """Keep the denormalised stage/owner/due-date fields in step with the job."""
    update_fields = []
    stage_key = current_stage_for_job(managed_job)
    owner = stage_owner(stage_key)
    if managed_job.current_stage != stage_key:
        managed_job.current_stage = stage_key
        update_fields.append("current_stage")
    if managed_job.stage_owner != owner:
        managed_job.stage_owner = owner
        update_fields.append("stage_owner")
    if managed_job.due_date is None and due is not None:
        managed_job.due_date = due
        update_fields.append("due_date")
    if update_fields:
        managed_job.save(update_fields=[*update_fields, "updated_at"])
    return managed_job


@transaction.atomic
def set_due_date_at_acceptance(*, quote, actor=None) -> datetime:
    """Compute and store the due date when the buyer accepts a quote (law 6.2).

    The due date is computed from the SLA config; it is never supplied by the
    caller. Existing managed jobs on the request inherit the new due date.
    """
    quote_request = quote.quote_request
    start = getattr(quote, "accepted_at", None) or timezone.now()
    due = quote_request.due_date or compute_due_date(start)
    if quote_request.due_date != due:
        quote_request.due_date = due
        quote_request.save(update_fields=["due_date", "updated_at"])
    for managed_job in quote_request.managed_jobs.all():
        sync_job_timing(managed_job, due=due)
        record_job_event(
            managed_job=managed_job,
            stage=current_stage_for_job(managed_job),
            event=JobEvent.STAGE_STARTED,
            actor=actor,
            actor_role=OWNER_BUYER,
            source=JobEvent.SOURCE_SYSTEM,
            metadata={"due_date": due.isoformat(), "reason": "quote_accepted"},
        )
    return due


def ensure_job_due_date(managed_job: ManagedJob, *, due: datetime | None = None) -> datetime:
    """Guarantee a managed job has a due date (law 6.2)."""
    if managed_job.due_date is None:
        fallback = due
        if fallback is None:
            quote_request = getattr(managed_job, "source_quote_request", None)
            fallback = getattr(quote_request, "due_date", None)
        if fallback is None:
            raise ValidationError("A due date must be set at quote acceptance before production (law 6.2).")
        managed_job.due_date = fallback
        managed_job.save(update_fields=["due_date", "updated_at"])
    sync_job_timing(managed_job, due=managed_job.due_date)
    return managed_job.due_date


def assert_due_date_for_production(managed_job: ManagedJob) -> datetime:
    """Refuse to move a job into production without a due date (law 6.2)."""
    if managed_job.due_date is None:
        raise ValidationError(
            "This job has no due date and cannot enter production. The due date is set "
            "when the buyer accepts the quote (law 6.2)."
        )
    sync_job_timing(managed_job)
    return managed_job.due_date


# --- due-date revisions (law 6.5) -----------------------------------------
@transaction.atomic
def record_date_revision(
    *,
    quote_request,
    new_date: datetime,
    old_date: datetime | None = None,
    cause: str = QuoteDateRevision.CAUSE_MANAGER,
    reason: str = "",
    user=None,
    override=None,
) -> QuoteDateRevision | None:
    """Record a non-silent due-date move and flag the buyer's "Date updated" state.

    Returns ``None`` when the date did not actually move.
    """
    current = old_date or quote_request.due_date
    if current is not None and new_date is not None and current == new_date:
        return None
    revision = QuoteDateRevision.objects.create(
        quote_request=quote_request,
        old_date=current,
        new_date=new_date,
        cause=cause,
        reason=reason or "",
        override=override,
        created_by=user,
    )
    quote_request.date_updated = True
    quote_request.date_updated_old = current
    quote_request.date_updated_new = new_date
    quote_request.date_acknowledged_at = None
    quote_request.date_acknowledged_by = None
    quote_request.save(
        update_fields=[
            "date_updated",
            "date_updated_old",
            "date_updated_new",
            "date_acknowledged_at",
            "date_acknowledged_by",
            "updated_at",
        ]
    )
    for managed_job in quote_request.managed_jobs.all():
        managed_job.due_date_revision_count = (managed_job.due_date_revision_count or 0) + 1
        managed_job.save(update_fields=["due_date_revision_count", "updated_at"])
        record_job_event(
            managed_job=managed_job,
            stage=current_stage_for_job(managed_job),
            event=JobEvent.DATE_REVISION,
            actor=user,
            actor_role=cause,
            source=cause,
            metadata={
                "old_date": current.isoformat() if current else None,
                "new_date": new_date.isoformat() if new_date else None,
                "reason": reason or "",
                "revision_id": revision.id,
                "override_id": getattr(override, "id", None),
            },
        )
    return revision


@transaction.atomic
def acknowledge_date_update(*, quote_request, user) -> QuoteDateRevision | None:
    """Buyer acknowledges the new due date so the change takes effect (law 6.5)."""
    new_date = quote_request.date_updated_new
    now = timezone.now()
    quote_request.date_updated = False
    quote_request.date_acknowledged_at = now
    quote_request.date_acknowledged_by = user
    if new_date is not None:
        quote_request.due_date = new_date
    quote_request.save(
        update_fields=[
            "date_updated",
            "date_acknowledged_at",
            "date_acknowledged_by",
            "due_date",
            "updated_at",
        ]
    )
    for managed_job in quote_request.managed_jobs.all():
        if new_date is not None:
            managed_job.due_date = new_date
        managed_job.due_date_acknowledged_at = now
        managed_job.save(update_fields=["due_date", "due_date_acknowledged_at", "updated_at"])
        record_job_event(
            managed_job=managed_job,
            stage=current_stage_for_job(managed_job),
            event=JobEvent.HANDOFF,
            actor=user,
            actor_role=OWNER_BUYER,
            source=JobEvent.SOURCE_BUYER,
            metadata={"acknowledged": True, "due_date": new_date.isoformat() if new_date else None},
        )
    return quote_request.date_revisions.order_by("-created_at", "-id").first()


# --- lateness (law 6.6) ----------------------------------------------------
@transaction.atomic
def record_lateness(managed_job: ManagedJob, *, now: datetime | None = None) -> ManagedJob:
    """Record and flag lateness against the acknowledged due date (law 6.6).

    Records the fact only. It never touches any payout, fee or held amount.
    """
    now = now or timezone.now()
    due = managed_job.due_date
    if due is None:
        sync_job_timing(managed_job)
        return managed_job
    late = now > due
    minutes = int((now - due).total_seconds() // 60) if late else 0
    newly_late = late and not managed_job.is_late
    managed_job.is_late = late
    managed_job.lateness_minutes = max(minutes, 0)
    managed_job.save(update_fields=["is_late", "lateness_minutes", "updated_at"])
    if newly_late:
        stage_key = current_stage_for_job(managed_job)
        attribution = attribute_delay(managed_job=managed_job, stage_key=stage_key)
        record_job_event(
            managed_job=managed_job,
            stage=stage_key,
            event=JobEvent.DELAY,
            actor=None,
            actor_role=attribution["owner"],
            source=JobEvent.SOURCE_SYSTEM,
            metadata={
                "due_date": due.isoformat(),
                "lateness_minutes": minutes,
                "attributed_owner": attribution["owner"],
                "buyer_caused": attribution["buyer_caused"],
                "counted_against_manager": attribution["counted_against_manager"],
                "counted_against_printer": attribution["counted_against_printer"],
            },
        )
    return managed_job


__all__ = [
    "record_job_event",
    "record_correction",
    "sync_job_timing",
    "set_due_date_at_acceptance",
    "ensure_job_due_date",
    "assert_due_date_for_production",
    "record_date_revision",
    "acknowledge_date_update",
    "record_lateness",
]
