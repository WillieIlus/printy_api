"""Lightweight audit/event helpers for managed-job workflow actions."""

from __future__ import annotations

from typing import Any

from jobs.models import JobAssignment, JobFile, ManagedJob, JobStatusEvent


EVENT_QUOTE_ACCEPTED = "quote_accepted"
EVENT_MANAGED_JOB_CREATED = "managed_job_created"
EVENT_ASSIGNMENT_CREATED = "assignment_created"
EVENT_ASSIGNMENT_STATUS_CHANGED = "assignment_status_changed"
EVENT_FILE_UPLOADED = "file_uploaded"
EVENT_FILE_REPLACED = "file_replaced"
EVENT_PROOF_APPROVED = "proof_approved"
EVENT_PROOF_REJECTED = "proof_rejected"
EVENT_REVISION_REQUESTED = "revision_requested"
EVENT_PAYMENT_CONFIRMED = "payment_confirmed"
EVENT_SETTLEMENT_RELEASE_READY = "settlement_release_ready"
EVENT_PAYOUT_RELEASED = "payout_released_manual"
EVENT_ISSUE_RAISED = "issue_raised"


def record_job_status_event(
    *,
    managed_job: ManagedJob,
    event_type: str,
    actor=None,
    assignment: JobAssignment | None = None,
    job_file: JobFile | None = None,
    summary: str = "",
    metadata: dict[str, Any] | None = None,
) -> JobStatusEvent:
    return JobStatusEvent.objects.create(
        managed_job=managed_job,
        assignment=assignment,
        job_file=job_file,
        actor=actor,
        event_type=event_type,
        summary=summary,
        metadata=metadata or {},
    )
