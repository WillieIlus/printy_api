"""Notification helpers for managed-job lifecycle events."""

from __future__ import annotations

from jobs.models import ManagedJob
from notifications.models import Notification
from notifications.services import notify_quote_event


def notify_managed_job_recipients(*, managed_job: ManagedJob, actor, message: str) -> None:
    """Notify client, broker and assigned-shop owner about a managed-job event.

    Recipients are deduplicated by user id and the triggering actor is skipped.
    """
    recipients = []
    for candidate in (
        getattr(managed_job, "client", None),
        getattr(managed_job, "broker", None),
        getattr(getattr(managed_job, "assigned_shop", None), "owner", None),
    ):
        if candidate is None or getattr(candidate, "id", None) is None:
            continue
        if any(recipient.id == candidate.id for recipient in recipients):
            continue
        recipients.append(candidate)

    for recipient in recipients:
        if getattr(recipient, "id", None) == getattr(actor, "id", None):
            continue
        notify_quote_event(
            recipient=recipient,
            notification_type=Notification.JOB_STATUS_UPDATED,
            message=message,
            object_type="managed_job",
            object_id=managed_job.id,
            actor=actor,
        )