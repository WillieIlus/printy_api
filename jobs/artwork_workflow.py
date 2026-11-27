"""Artwork-is-a-workflow-step services (law 7.2).

A job can only be paid once it has artwork, supplied one of two ways:

  (a) the buyer uploads an artwork file, at quote request or in the workflow
      step; or
  (b) the buyer states the artwork is already with the manager/printer (or the
      manager marks that they already hold it), and the manager accepts it.

Payment initiation is refused server-side until (a) or (b) is satisfied
(law 7.2). Production (dispatch) additionally requires the printer to
acknowledge they hold the artwork when it was supplied via (b), because a
production precondition is not a payment condition (law 7.2).

Every claim, acceptance, decline and acknowledgement is written to the
append-only job event log (law 6.3). The stage owner is the buyer while the
upload/claiim is outstanding and the manager while their acceptance is
pending, so delay is attributed correctly (law 6.4).

State lives in ``ManagedJob.workflow_metadata`` under ``artwork_workflow`` so no
migration history is touched (prompt line 4).
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from jobs.choices import JobFileStatus, JobFileType
from jobs.models import JobEvent, ManagedJob
from jobs.timing import current_stage_for_job
from jobs.timing_config import OWNER_BUYER, OWNER_MANAGER, OWNER_PRINTER
from jobs.timing_services import record_job_event


ARTWORK_WORKFLOW_KEY = "artwork_workflow"

SOURCE_BUYER_UPLOAD = "buyer_upload"
SOURCE_MANAGER_HOLDS = "manager_holds"

CLAIM_NONE = "none"
CLAIM_CLAIMED = "claimed"
CLAIM_ACCEPTED = "accepted"
CLAIM_DECLINED = "declined"

EVENT_ARTWORK_UPLOADED = "artwork_uploaded"
EVENT_ARTWORK_CLAIMED = "artwork_claimed_with_manager"
EVENT_ARTWORK_MANAGER_ACCEPTED = "artwork_manager_accepted"
EVENT_ARTWORK_MANAGER_HOLDS = "artwork_manager_holds"
EVENT_ARTWORK_MANAGER_DECLINED = "artwork_manager_declined"
EVENT_ARTWORK_PRINTER_ACKNOWLEDGED = "artwork_printer_acknowledged"

# Rendered verbatim by the UI on the disabled pay button (prompt 7.2).
PAYMENT_BLOCK_UPLOAD = "Upload your artwork to pay"
PAYMENT_BLOCK_AWAITING_MANAGER = "Waiting for your manager to confirm they have the artwork"
PAYMENT_BLOCK_AWAITING_APPROVAL = "Waiting for your manager to approve the artwork"
PAYMENT_BLOCK_CHANGES = "Your manager asked for changes to the artwork"

REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_CHANGES_REQUESTED = "changes_requested"

EVENT_ARTWORK_MANAGER_APPROVED = "artwork_manager_approved"
EVENT_ARTWORK_MANAGER_CHANGES_REQUESTED = "artwork_manager_changes_requested"


def _metadata(managed_job: ManagedJob) -> dict:
    raw = getattr(managed_job, "workflow_metadata", None)
    return raw if isinstance(raw, dict) else {}


def _workflow_raw(managed_job: ManagedJob) -> dict:
    raw = _metadata(managed_job).get(ARTWORK_WORKFLOW_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _latest_artwork_file(managed_job: ManagedJob):
    job_files = getattr(managed_job, "job_files", None)
    if job_files is None or not hasattr(job_files, "filter"):
        return None
    return (
        job_files.filter(
            file_type__in=[JobFileType.ARTWORK, JobFileType.CUSTOMER_UPLOAD],
        )
        .exclude(status=JobFileStatus.REPLACED)
        .order_by("-id", "-created_at")
        .first()
    )


def _artwork_file_present(managed_job: ManagedJob) -> bool:
    # Mirrors jobs.file_services.managed_job_has_artwork but avoids a circular
    # import (file_services imports this module).
    return _latest_artwork_file(managed_job) is not None


def _request_artwork_approved(managed_job: ManagedJob) -> bool:
    request = getattr(managed_job, "source_quote_request", None)
    if request is None:
        return False
    try:
        from quotes.artwork_review import is_approved

        return bool(is_approved(request))
    except Exception:
        return False


def _review_state(raw: dict) -> str:
    state = str(raw.get("review_state") or REVIEW_PENDING)
    if state not in {REVIEW_PENDING, REVIEW_APPROVED, REVIEW_CHANGES_REQUESTED}:
        state = REVIEW_PENDING
    return state


def _save(managed_job: ManagedJob, raw: dict) -> ManagedJob:
    metadata = _metadata(managed_job).copy()
    metadata[ARTWORK_WORKFLOW_KEY] = raw
    managed_job.workflow_metadata = metadata
    managed_job.save(update_fields=["workflow_metadata", "updated_at"])
    return managed_job


def _log(
    managed_job: ManagedJob,
    *,
    event: str,
    actor,
    actor_role: str,
    source: str,
    summary: str,
    metadata: dict | None = None,
) -> None:
    record_job_event(
        managed_job=managed_job,
        stage=current_stage_for_job(managed_job),
        event=event,
        actor=actor,
        actor_role=actor_role,
        source=source,
        metadata={"summary": summary, **(metadata or {})},
    )


def artwork_satisfied(managed_job: ManagedJob) -> bool:
    """True when artwork is present and the current version is approved.

    A manager-held claim counts once the manager accepts it. An uploaded file
    counts only when the manager approved that exact JobFile id. If the managed
    job predates review metadata, a manager approval recorded on the source
    quote request is accepted as the current approval.
    """
    raw = _workflow_raw(managed_job)
    if raw.get("source") == SOURCE_MANAGER_HOLDS and raw.get("claim_state") == CLAIM_ACCEPTED:
        return True
    latest = _latest_artwork_file(managed_job)
    if latest is None:
        return False
    if _review_state(raw) == REVIEW_APPROVED and int(raw.get("review_file_id") or 0) == int(latest.id):
        return True
    return _request_artwork_approved(managed_job)


def artwork_payment_block_reason(managed_job: ManagedJob) -> str | None:
    """The reason payment initiation is refused, or None when artwork is approved."""
    if artwork_satisfied(managed_job):
        return None
    raw = _workflow_raw(managed_job)
    if _review_state(raw) == REVIEW_CHANGES_REQUESTED:
        return PAYMENT_BLOCK_CHANGES
    if raw.get("source") == SOURCE_MANAGER_HOLDS and raw.get("claim_state") == CLAIM_CLAIMED:
        return PAYMENT_BLOCK_AWAITING_MANAGER
    if _latest_artwork_file(managed_job) is not None:
        return PAYMENT_BLOCK_AWAITING_APPROVAL
    return PAYMENT_BLOCK_UPLOAD


def artwork_blocks_dispatch(managed_job: ManagedJob) -> str | None:
    """A production (dispatch) precondition reason, or None (law 7.2).

    Only the manager-holds path needs a printer acknowledgement before
    production: when artwork was supplied as a file there is nothing to
    acknowledge (law 7.2).
    """
    raw = _workflow_raw(managed_job)
    if (
        raw.get("source") == SOURCE_MANAGER_HOLDS
        and raw.get("claim_state") == CLAIM_ACCEPTED
        and not raw.get("printer_acknowledged_at")
    ):
        return "The printer must acknowledge they hold the artwork before production can start."
    return None


def get_artwork_workflow_payload(managed_job: ManagedJob) -> dict[str, Any]:
    raw = _workflow_raw(managed_job)
    source = raw.get("source")
    if source not in {SOURCE_BUYER_UPLOAD, SOURCE_MANAGER_HOLDS}:
        source = SOURCE_BUYER_UPLOAD if _artwork_file_present(managed_job) else None
    claim_state = raw.get("claim_state")
    if claim_state not in {CLAIM_NONE, CLAIM_CLAIMED, CLAIM_ACCEPTED, CLAIM_DECLINED}:
        claim_state = CLAIM_NONE
    # Stage owner (law 6.4): the manager owns the artwork step while their
    # acceptance is pending; otherwise the buyer owns it.
    if source == SOURCE_MANAGER_HOLDS and claim_state == CLAIM_CLAIMED:
        stage_owner = OWNER_MANAGER
    else:
        stage_owner = OWNER_BUYER
    return {
        "source": source,
        "claim_state": claim_state,
        "review_state": _review_state(raw),
        "review_file_id": raw.get("review_file_id"),
        "review_note": raw.get("review_note", ""),
        "reviewed_at": raw.get("reviewed_at"),
        "reviewed_by": raw.get("reviewed_by"),
        "claimed_at": raw.get("claimed_at"),
        "claimed_by": raw.get("claimed_by"),
        "accepted_at": raw.get("accepted_at"),
        "accepted_by": raw.get("accepted_by"),
        "declined_at": raw.get("declined_at"),
        "declined_by": raw.get("declined_by"),
        "decline_reason": raw.get("decline_reason", ""),
        "printer_acknowledged_at": raw.get("printer_acknowledged_at"),
        "printer_acknowledged_by": raw.get("printer_acknowledged_by"),
        "satisfied": artwork_satisfied(managed_job),
        "payment_block_reason": artwork_payment_block_reason(managed_job),
        "stage_owner": stage_owner,
    }


@transaction.atomic
def record_artwork_uploaded(managed_job: ManagedJob, *, actor=None) -> ManagedJob:
    """Mark the artwork as supplied by upload and log it (law 6.3/7.2)."""
    raw = _workflow_raw(managed_job)
    latest = _latest_artwork_file(managed_job)
    raw.update(
        {
            "source": SOURCE_BUYER_UPLOAD,
            "claim_state": CLAIM_NONE,
            "review_state": REVIEW_PENDING,
            "review_file_id": getattr(latest, "id", None),
            "review_note": "",
            "reviewed_at": None,
            "reviewed_by": None,
        }
    )
    managed_job = _save(managed_job, raw)
    _log(
        managed_job,
        event=EVENT_ARTWORK_UPLOADED,
        actor=actor,
        actor_role=OWNER_BUYER,
        source=JobEvent.SOURCE_BUYER,
        summary="Buyer uploaded artwork.",
    )
    return managed_job


@transaction.atomic
def claim_artwork_with_manager(managed_job: ManagedJob, *, actor=None, note: str = "") -> ManagedJob:
    """Buyer states the artwork is already with the manager/printer (option b)."""
    if _artwork_file_present(managed_job):
        raise ValidationError("Artwork is already uploaded for this job.")
    raw = _workflow_raw(managed_job)
    raw.update(
        {
            "source": SOURCE_MANAGER_HOLDS,
            "claim_state": CLAIM_CLAIMED,
            "claimed_at": timezone.now().isoformat(),
            "claimed_by": getattr(actor, "id", None),
            "accepted_at": None,
            "accepted_by": None,
            "declined_at": None,
            "declined_by": None,
            "decline_reason": "",
            "note": note or "",
        }
    )
    managed_job = _save(managed_job, raw)
    _log(
        managed_job,
        event=EVENT_ARTWORK_CLAIMED,
        actor=actor,
        actor_role=OWNER_BUYER,
        source=JobEvent.SOURCE_BUYER,
        summary="Buyer states the artwork is already with the manager/printer.",
        metadata={"note": note or ""},
    )
    return managed_job


@transaction.atomic
def manager_accept_artwork_claim(managed_job: ManagedJob, *, actor=None, note: str = "") -> ManagedJob:
    """The manager confirms they hold artwork the buyer claimed was already with them."""
    raw = _workflow_raw(managed_job)
    if raw.get("claim_state") != CLAIM_CLAIMED:
        raise ValidationError("There is no buyer artwork claim waiting for the manager.")
    raw.update(
        {
            "source": SOURCE_MANAGER_HOLDS,
            "claim_state": CLAIM_ACCEPTED,
            "accepted_at": timezone.now().isoformat(),
            "accepted_by": getattr(actor, "id", None),
            "note": note or raw.get("note", ""),
        }
    )
    managed_job = _save(managed_job, raw)
    _log(
        managed_job,
        event=EVENT_ARTWORK_MANAGER_ACCEPTED,
        actor=actor,
        actor_role=OWNER_MANAGER,
        source=JobEvent.SOURCE_MANAGER,
        summary="Manager confirmed they hold the artwork.",
        metadata={"note": note or ""},
    )
    return managed_job


@transaction.atomic
def manager_mark_holds_artwork(managed_job: ManagedJob, *, actor=None, note: str = "") -> ManagedJob:
    """The manager marks that they already hold the artwork (no buyer tick needed)."""
    if _artwork_file_present(managed_job):
        raise ValidationError("Artwork is already uploaded for this job.")
    now = timezone.now().isoformat()
    raw = _workflow_raw(managed_job)
    raw.update(
        {
            "source": SOURCE_MANAGER_HOLDS,
            "claim_state": CLAIM_ACCEPTED,
            "claimed_at": raw.get("claimed_at") or now,
            "claimed_by": raw.get("claimed_by") or getattr(actor, "id", None),
            "accepted_at": now,
            "accepted_by": getattr(actor, "id", None),
            "note": note or raw.get("note", ""),
        }
    )
    managed_job = _save(managed_job, raw)
    _log(
        managed_job,
        event=EVENT_ARTWORK_MANAGER_HOLDS,
        actor=actor,
        actor_role=OWNER_MANAGER,
        source=JobEvent.SOURCE_MANAGER,
        summary="Manager marked that they already hold the artwork.",
        metadata={"note": note or ""},
    )
    return managed_job


@transaction.atomic
def manager_decline_artwork_claim(managed_job: ManagedJob, *, actor=None, reason: str = "") -> ManagedJob:
    """The manager cannot confirm the claim; the buyer must upload instead."""
    raw = _workflow_raw(managed_job)
    if raw.get("claim_state") != CLAIM_CLAIMED:
        raise ValidationError("There is no buyer artwork claim waiting for the manager.")
    raw.update(
        {
            "claim_state": CLAIM_DECLINED,
            "declined_at": timezone.now().isoformat(),
            "declined_by": getattr(actor, "id", None),
            "decline_reason": reason or "",
        }
    )
    managed_job = _save(managed_job, raw)
    _log(
        managed_job,
        event=EVENT_ARTWORK_MANAGER_DECLINED,
        actor=actor,
        actor_role=OWNER_MANAGER,
        source=JobEvent.SOURCE_MANAGER,
        summary="Manager could not confirm the artwork; the buyer must upload.",
        metadata={"reason": reason or ""},
    )
    return managed_job


@transaction.atomic
def printer_acknowledge_artwork(managed_job: ManagedJob, *, actor=None, note: str = "") -> ManagedJob:
    """The printer confirms they hold the artwork before production can start (law 7.2)."""
    raw = _workflow_raw(managed_job)
    if _artwork_file_present(managed_job):
        raise ValidationError("Artwork is uploaded; printer acknowledgement is not required.")
    if raw.get("source") != SOURCE_MANAGER_HOLDS or raw.get("claim_state") != CLAIM_ACCEPTED:
        raise ValidationError("The manager must confirm they hold the artwork before the printer acknowledges it.")
    raw.update(
        {
            "printer_acknowledged_at": timezone.now().isoformat(),
            "printer_acknowledged_by": getattr(actor, "id", None),
        }
    )
    managed_job = _save(managed_job, raw)
    _log(
        managed_job,
        event=EVENT_ARTWORK_PRINTER_ACKNOWLEDGED,
        actor=actor,
        actor_role=OWNER_PRINTER,
        source=JobEvent.SOURCE_PRINTER,
        summary="Printer acknowledged they hold the artwork.",
        metadata={"note": note or ""},
    )
    return managed_job


@transaction.atomic
def manager_approve_artwork(managed_job: ManagedJob, *, actor=None, file_id: int | None = None, note: str = "") -> ManagedJob:
    """Approve the current uploaded artwork version for payment."""
    latest = _latest_artwork_file(managed_job)
    if latest is None:
        raise ValidationError("There is no uploaded artwork to approve.")
    if file_id is not None and int(file_id) != int(latest.id):
        raise ValidationError("That artwork version is no longer current. Refresh and review the latest file.")
    raw = _workflow_raw(managed_job)
    raw.update(
        {
            "source": SOURCE_BUYER_UPLOAD,
            "review_state": REVIEW_APPROVED,
            "review_file_id": latest.id,
            "review_note": note or "",
            "reviewed_at": timezone.now().isoformat(),
            "reviewed_by": getattr(actor, "id", None),
        }
    )
    managed_job = _save(managed_job, raw)
    _log(
        managed_job,
        event=EVENT_ARTWORK_MANAGER_APPROVED,
        actor=actor,
        actor_role=OWNER_MANAGER,
        source=JobEvent.SOURCE_MANAGER,
        summary="Manager approved the current artwork.",
        metadata={"file_id": latest.id, "note": note or ""},
    )
    return managed_job


@transaction.atomic
def manager_request_artwork_changes(managed_job: ManagedJob, *, actor=None, reason: str = "") -> ManagedJob:
    """Reject the current artwork version; the buyer must upload a new one."""
    latest = _latest_artwork_file(managed_job)
    if latest is None:
        raise ValidationError("There is no uploaded artwork to review.")
    raw = _workflow_raw(managed_job)
    raw.update(
        {
            "source": SOURCE_BUYER_UPLOAD,
            "review_state": REVIEW_CHANGES_REQUESTED,
            "review_file_id": latest.id,
            "review_note": reason or "",
            "reviewed_at": timezone.now().isoformat(),
            "reviewed_by": getattr(actor, "id", None),
        }
    )
    managed_job = _save(managed_job, raw)
    _log(
        managed_job,
        event=EVENT_ARTWORK_MANAGER_CHANGES_REQUESTED,
        actor=actor,
        actor_role=OWNER_MANAGER,
        source=JobEvent.SOURCE_MANAGER,
        summary="Manager requested changes to the current artwork.",
        metadata={"file_id": latest.id, "reason": reason or ""},
    )
    return managed_job


@transaction.atomic
def seed_artwork_review_from_request(managed_job: ManagedJob) -> ManagedJob:
    """Carry a quote-request artwork approval into the managed job."""
    request = getattr(managed_job, "source_quote_request", None)
    if request is None:
        return managed_job
    try:
        from quotes.artwork_review import get_review

        review = get_review(request)
    except Exception:
        return managed_job
    latest = _latest_artwork_file(managed_job)
    if latest is None:
        return managed_job
    raw = _workflow_raw(managed_job)
    raw.update(
        {
            "source": SOURCE_BUYER_UPLOAD,
            "review_state": review.get("state") or REVIEW_PENDING,
            "review_file_id": latest.id,
            "review_note": review.get("note", ""),
            "reviewed_at": review.get("reviewed_at"),
            "reviewed_by": review.get("reviewed_by"),
        }
    )
    return _save(managed_job, raw)


__all__ = [
    "ARTWORK_WORKFLOW_KEY",
    "SOURCE_BUYER_UPLOAD",
    "SOURCE_MANAGER_HOLDS",
    "CLAIM_NONE",
    "CLAIM_CLAIMED",
    "CLAIM_ACCEPTED",
    "CLAIM_DECLINED",
    "PAYMENT_BLOCK_UPLOAD",
    "PAYMENT_BLOCK_AWAITING_MANAGER",
    "PAYMENT_BLOCK_AWAITING_APPROVAL",
    "PAYMENT_BLOCK_CHANGES",
    "REVIEW_PENDING",
    "REVIEW_APPROVED",
    "REVIEW_CHANGES_REQUESTED",
    "artwork_satisfied",
    "artwork_payment_block_reason",
    "artwork_blocks_dispatch",
    "get_artwork_workflow_payload",
    "record_artwork_uploaded",
    "claim_artwork_with_manager",
    "manager_accept_artwork_claim",
    "manager_mark_holds_artwork",
    "manager_decline_artwork_claim",
    "printer_acknowledge_artwork",
    "manager_approve_artwork",
    "manager_request_artwork_changes",
    "seed_artwork_review_from_request",
]
