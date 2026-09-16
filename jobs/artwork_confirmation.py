from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from jobs.audit_services import record_job_status_event
from jobs.models import ManagedJob


ARTWORK_CONFIRMATION_KEY = "artwork_confirmation"
ARTWORK_CONFIRMATION_NOT_REQUIRED = "not_required"
ARTWORK_CONFIRMATION_REQUESTED = "requested"
ARTWORK_CONFIRMATION_APPROVED = "approved"
ARTWORK_CONFIRMATION_REJECTED = "rejected"

EVENT_ARTWORK_CONFIRMATION_REQUESTED = "artwork_confirmation_requested"
EVENT_ARTWORK_CONFIRMATION_APPROVED = "artwork_confirmation_approved"
EVENT_ARTWORK_CONFIRMATION_REJECTED = "artwork_confirmation_rejected"

BLOCKING_ARTWORK_CONFIRMATION_STATES = {
    ARTWORK_CONFIRMATION_REQUESTED,
    ARTWORK_CONFIRMATION_REJECTED,
}


def _metadata(managed_job: ManagedJob) -> dict:
    return managed_job.workflow_metadata if isinstance(managed_job.workflow_metadata, dict) else {}


def get_artwork_confirmation_payload(managed_job: ManagedJob) -> dict:
    payload = _metadata(managed_job).get(ARTWORK_CONFIRMATION_KEY)
    if not isinstance(payload, dict):
        payload = {}
    state = str(payload.get("state") or ARTWORK_CONFIRMATION_NOT_REQUIRED)
    if state not in {
        ARTWORK_CONFIRMATION_NOT_REQUIRED,
        ARTWORK_CONFIRMATION_REQUESTED,
        ARTWORK_CONFIRMATION_APPROVED,
        ARTWORK_CONFIRMATION_REJECTED,
    }:
        state = ARTWORK_CONFIRMATION_NOT_REQUIRED
    return {
        "state": state,
        "requested_at": payload.get("requested_at"),
        "requested_by": payload.get("requested_by"),
        "responded_at": payload.get("responded_at"),
        "responded_by": payload.get("responded_by"),
        "note": payload.get("note", ""),
    }


def artwork_confirmation_blocks_dispatch(managed_job: ManagedJob) -> bool:
    return get_artwork_confirmation_payload(managed_job)["state"] in BLOCKING_ARTWORK_CONFIRMATION_STATES


def require_artwork_confirmation_dispatch_ready(managed_job: ManagedJob) -> None:
    payload = get_artwork_confirmation_payload(managed_job)
    if payload["state"] == ARTWORK_CONFIRMATION_REQUESTED:
        raise ValidationError("Client artwork confirmation is required before dispatch.")
    if payload["state"] == ARTWORK_CONFIRMATION_REJECTED:
        raise ValidationError("Client requested artwork changes before dispatch.")


def _save_payload(*, managed_job: ManagedJob, payload: dict) -> ManagedJob:
    metadata = _metadata(managed_job).copy()
    metadata[ARTWORK_CONFIRMATION_KEY] = payload
    managed_job.workflow_metadata = metadata
    managed_job.save(update_fields=["workflow_metadata", "updated_at"])
    return managed_job


@transaction.atomic
def request_client_artwork_confirmation(*, managed_job: ManagedJob, actor=None, note: str = "") -> ManagedJob:
    payload = get_artwork_confirmation_payload(managed_job)
    if payload["state"] == ARTWORK_CONFIRMATION_APPROVED:
        raise ValidationError("Artwork confirmation is already approved.")
    payload.update(
        {
            "state": ARTWORK_CONFIRMATION_REQUESTED,
            "requested_at": timezone.now().isoformat(),
            "requested_by": getattr(actor, "id", None),
            "responded_at": None,
            "responded_by": None,
            "note": note or "",
        }
    )
    managed_job = _save_payload(managed_job=managed_job, payload=payload)
    record_job_status_event(
        managed_job=managed_job,
        actor=actor,
        event_type=EVENT_ARTWORK_CONFIRMATION_REQUESTED,
        summary="Client artwork confirmation requested.",
        metadata={"state": payload["state"], "note": note or ""},
    )
    return managed_job


@transaction.atomic
def respond_to_client_artwork_confirmation(*, managed_job: ManagedJob, actor=None, approved: bool, note: str = "") -> ManagedJob:
    payload = get_artwork_confirmation_payload(managed_job)
    if payload["state"] != ARTWORK_CONFIRMATION_REQUESTED:
        raise ValidationError("No artwork confirmation is waiting for client response.")
    payload.update(
        {
            "state": ARTWORK_CONFIRMATION_APPROVED if approved else ARTWORK_CONFIRMATION_REJECTED,
            "responded_at": timezone.now().isoformat(),
            "responded_by": getattr(actor, "id", None),
            "note": note or payload.get("note", ""),
        }
    )
    managed_job = _save_payload(managed_job=managed_job, payload=payload)
    record_job_status_event(
        managed_job=managed_job,
        actor=actor,
        event_type=EVENT_ARTWORK_CONFIRMATION_APPROVED if approved else EVENT_ARTWORK_CONFIRMATION_REJECTED,
        summary="Client approved artwork confirmation." if approved else "Client requested artwork changes.",
        metadata={"state": payload["state"], "note": note or ""},
    )
    return managed_job
