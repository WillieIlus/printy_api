"""Single source of truth for "who has the ball" on a managed job.

Law 6.4 / 10.1: every job exposes exactly one current owner and one next
action. This module is the only place that derives them; views and serializers
only project the result per role. It reuses the existing ManagedJob status
machine (no parallel status system) and the existing timing/ownership helpers.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.utils import timezone

from jobs.choices import JobAssignmentStatus, ManagedJobStatus
from jobs.models import ManagedJob
from jobs.artwork_workflow import artwork_satisfied, artwork_payment_block_reason

BUYER = "buyer"
MANAGER = "manager"
PRINTER = "printer"
NONE = "none"

STATUS_LABELS = {
    ManagedJobStatus.DRAFT: "Draft",
    ManagedJobStatus.QUOTED: "Quote sent",
    ManagedJobStatus.AWAITING_PAYMENT: "Awaiting payment",
    ManagedJobStatus.PAYMENT_CONFIRMED: "Payment confirmed",
    ManagedJobStatus.ASSIGNED: "Assigned",
    ManagedJobStatus.IN_PRODUCTION: "In production",
    ManagedJobStatus.FINISHING: "Finishing",
    ManagedJobStatus.READY: "Ready",
    ManagedJobStatus.DELIVERED: "Delivered",
    ManagedJobStatus.COMPLETED: "Completed",
    ManagedJobStatus.DISPUTED: "Disputed",
    ManagedJobStatus.CANCELLED: "Cancelled",
}

# Who is responsible for the job in each explicit state.
_STATUS_OWNER = {
    ManagedJobStatus.DRAFT: BUYER,
    ManagedJobStatus.QUOTED: BUYER,
    ManagedJobStatus.AWAITING_PAYMENT: BUYER,
    ManagedJobStatus.PAYMENT_CONFIRMED: MANAGER,
    ManagedJobStatus.ASSIGNED: MANAGER,
    ManagedJobStatus.IN_PRODUCTION: PRINTER,
    ManagedJobStatus.FINISHING: PRINTER,
    ManagedJobStatus.READY: MANAGER,
    ManagedJobStatus.DELIVERED: BUYER,
    ManagedJobStatus.COMPLETED: NONE,
    ManagedJobStatus.DISPUTED: NONE,
    ManagedJobStatus.CANCELLED: NONE,
}

# The artwork step transfers responsibility: once artwork is satisfied, the
# manager owns the approval/forward step; once a claim is pending, the manager
# owns the acceptance step too.
def _active_assignment(job: ManagedJob):
    return (
        job.assignments.filter(reassigned_from__isnull=True)
        .exclude(status__in=[JobAssignmentStatus.REJECTED, JobAssignmentStatus.CANCELLED])
        .order_by("-id")
        .first()
    )


def _status_owner(job: ManagedJob) -> str:
    status = getattr(job, "status", None)
    owner = _STATUS_OWNER.get(status, NONE)
    if status in (ManagedJobStatus.PAYMENT_CONFIRMED, ManagedJobStatus.ASSIGNED):
        if not _artwork_satisfied(job):
            return BUYER  # buyer must upload or claim
        confirmation_state = _artwork_confirmation_state(job)
        if confirmation_state in ("requested", "rejected"):
            return BUYER  # buyer must approve/re-upload
        if status == ManagedJobStatus.ASSIGNED:
            assignment = _active_assignment(job)
            if job.assigned_shop_id and assignment and assignment.status in {
                JobAssignmentStatus.PENDING,
                JobAssignmentStatus.ACCEPTED,
                JobAssignmentStatus.IN_PRODUCTION,
                JobAssignmentStatus.FINISHING,
            }:
                # Dispatch transferred the ball: the shop must accept/print.
                return PRINTER
        return MANAGER  # manager checks/forwards or dispatches
    return owner


def _artwork_satisfied(job: ManagedJob) -> bool:
    try:
        from jobs.artwork_workflow import artwork_satisfied

        return bool(artwork_satisfied(job))
    except Exception:  # pragma: no cover
        return bool(getattr(job, "artwork_uploaded", False))


def _artwork_confirmation_state(job: ManagedJob) -> str:
    meta = getattr(job, "workflow_metadata", None)
    if not isinstance(meta, dict):
        return "not_required"
    payload = meta.get("artwork_confirmation")
    if not isinstance(payload, dict):
        return "not_required"
    return str(payload.get("state") or "not_required")


def _artwork_satisfied_flag(job: ManagedJob) -> bool:
    return bool(getattr(job, "artwork_uploaded", False)) or _artwork_satisfied(job)


def _assignment_id(job: ManagedJob):
    assignment = _active_assignment(job)
    return getattr(assignment, "id", None)


def _role_for_owner(owner: str, viewer_role: str) -> str:
    owner = (owner or "").strip().lower()
    if viewer_role == BUYER:
        # The buyer's contract is with their manager, never the production shop.
        if owner in (MANAGER, PRINTER):
            return MANAGER
        if owner == BUYER:
            return BUYER
        return NONE
    if owner in (BUYER, MANAGER, PRINTER):
        return owner
    return NONE


def _next_action_key(stage_key: str, owner_role: str, job: ManagedJob, viewer_role: str) -> str:
    if owner_role == NONE or owner_role != viewer_role:
        return "none"
    if stage_key in (ManagedJobStatus.DRAFT, ManagedJobStatus.QUOTED, ManagedJobStatus.AWAITING_PAYMENT):
        return "accept_quote" if owner_role == BUYER else "prepare_quote"
    if stage_key in (ManagedJobStatus.PAYMENT_CONFIRMED, ManagedJobStatus.ASSIGNED):
        if owner_role == BUYER:
            if not _artwork_satisfied_flag(job):
                return "upload_artwork"
            if _artwork_confirmation_state(job) == "requested":
                return "approve_artwork"
            if _artwork_confirmation_state(job) == "rejected":
                return "upload_artwork"
            return "none"
        if owner_role == MANAGER:
            if not _artwork_satisfied_flag(job):
                return "none"
            if _artwork_confirmation_state(job) in ("requested", "rejected"):
                return "none"  # waiting on the buyer
            assignment = _active_assignment(job)
            if assignment is not None:
                return "none"  # dispatch already handed the job to the printer
            if getattr(job, "assignment_status", None) == "reassignment_required":
                return "reassign_job"
            return "dispatch_job"
        if owner_role == PRINTER:
            assignment = _active_assignment(job)
            if assignment is not None:
                if assignment.status == JobAssignmentStatus.PENDING:
                    return "accept_job"
                if assignment.status == JobAssignmentStatus.ACCEPTED:
                    return "start_printing"
                if assignment.status in (JobAssignmentStatus.IN_PRODUCTION, JobAssignmentStatus.FINISHING):
                    return "advance_production"
                if assignment.status == JobAssignmentStatus.READY:
                    return "mark_delivered"
            return "none"
        return "none"
    if stage_key in (ManagedJobStatus.IN_PRODUCTION, ManagedJobStatus.FINISHING):
        if owner_role == PRINTER:
            return "advance_production"
        return "none"
    if stage_key == ManagedJobStatus.READY:
        if owner_role == BUYER:
            return "confirm_delivery"
        if owner_role == PRINTER:
            return "mark_dispatched"
        if owner_role == MANAGER:
            return "coordinate_delivery"
        return "none"
    if stage_key == ManagedJobStatus.DELIVERED:
        if owner_role == BUYER:
            return "confirm_delivery"
        return "none"
    return "none"


def _next_action_label(key: str, status: str | None = None, viewer_role: str = "") -> str:
    if key == "none":
        if viewer_role == BUYER and status in (
            ManagedJobStatus.ASSIGNED,
            ManagedJobStatus.IN_PRODUCTION,
            ManagedJobStatus.FINISHING,
            ManagedJobStatus.READY,
        ):
            return "Your manager is running production"
        if viewer_role == MANAGER and status in (
            ManagedJobStatus.ASSIGNED,
            ManagedJobStatus.IN_PRODUCTION,
            ManagedJobStatus.FINISHING,
        ):
            return "Waiting on the printer"
        if viewer_role == PRINTER and status in (
            ManagedJobStatus.DRAFT,
            ManagedJobStatus.QUOTED,
            ManagedJobStatus.AWAITING_PAYMENT,
        ):
            return "Waiting on the buyer"
        return "No action needed"
    return {
        "accept_quote": "Accept the quote and pay",
        "prepare_quote": "Prepare and send the quote",
        "upload_artwork": "Upload your artwork",
        "approve_artwork": "Approve the artwork",
        "review_artwork": "Check the artwork and send it to the printer",
        "dispatch_job": "Assign a production printer",
        "reassign_job": "Reassign a production printer",
        "accept_job": "Accept the job",
        "start_printing": "Start printing",
        "advance_production": "Advance the production stage",
        "confirm_delivery": "Confirm the job is complete",
        "coordinate_delivery": "Coordinate delivery",
    }.get(key, "No action needed")


def _target_route(key: str, job: ManagedJob, viewer_role: str) -> str | None:
    job_id = job.id
    request_id = getattr(job, "source_quote_request_id", None)
    assignment_id = _assignment_id(job)
    if key == "accept_quote":
        return f"/app/buyer/requests/{request_id}" if request_id else "/app/buyer"
    if key in ("upload_artwork", "approve_artwork", "confirm_delivery"):
        return f"/app/buyer/jobs/{job_id}"
    if key in (
        "prepare_quote",
        "review_artwork",
        "dispatch_job",
        "reassign_job",
        "coordinate_delivery",
    ):
        return f"/app/manager/jobs?job={job_id}"
    if key in ("accept_job", "start_printing", "advance_production", "mark_dispatched"):
        return f"/app/printer?assignment={assignment_id}" if assignment_id else "/app/printer"
    return None


def _overdue_level(due_at, now) -> str:
    if due_at is None:
        return "none"
    remaining = (due_at - now).total_seconds() / 60.0
    if remaining <= 0:
        return "late"
    if remaining <= 24 * 60:
        return "warn"
    return "none"


_ACTION_EXPLANATIONS = {
    "accept_quote": "Review the manager-approved quote, then accept it to unlock payment.",
    "prepare_quote": "Your itemised quote is ready to send. It locks once sent.",
    "upload_artwork": "Attach your print-ready file, or tick that your manager already has it.",
    "approve_artwork": "Check bleed, resolution and colour, then approve or request changes.",
    "review_artwork": "Check bleed, resolution and colour, then approve or request changes.",
    "dispatch_job": "Choose the production shop and dispatch the job.",
    "reassign_job": "Choose a new production shop and dispatch the job again.",
    "accept_job": "A production request is waiting for your shop.",
    "start_printing": "Everything is checked — put the job on press.",
    "advance_production": "Mark the next production stage as complete.",
    "confirm_delivery": "You have your print — confirm delivery to release the payout.",
    "coordinate_delivery": "Hand to the rider or courier and mark it out for delivery.",
}


def _waiting_label(owner_role: str, viewer_role: str) -> str:
    if owner_role == BUYER:
        return "Waiting on you"
    if owner_role == MANAGER:
        return "Waiting on your manager" if viewer_role == BUYER else "Waiting on the manager"
    if owner_role == PRINTER:
        return "Waiting on the printer"
    return "Waiting on Printy"


def _phase_for_status(status: str | None) -> str:
    if status in (
        ManagedJobStatus.DRAFT,
        ManagedJobStatus.QUOTED,
        ManagedJobStatus.AWAITING_PAYMENT,
        ManagedJobStatus.PAYMENT_CONFIRMED,
    ):
        return "quote"
    if status in (ManagedJobStatus.COMPLETED, ManagedJobStatus.DELIVERED):
        return "outcome"
    return "production"


def resolve_next_step(job: ManagedJob, viewer_role: str) -> dict[str, Any]:
    """Return the single owner + next action for a job, role-aware."""
    now = timezone.now()
    status = getattr(job, "status", None)
    owner_role = _role_for_owner(_status_owner(job), viewer_role)
    key = _next_action_key(status, owner_role, job, viewer_role)
    # Terminal states: no further action for anyone except buyer confirming
    # delivery of a delivered job.
    if status in (ManagedJobStatus.COMPLETED, ManagedJobStatus.CANCELLED, ManagedJobStatus.DISPUTED):
        key = "none"
    if status == ManagedJobStatus.DELIVERED and viewer_role == BUYER:
        key = "confirm_delivery"
    is_mine = owner_role == viewer_role and owner_role != NONE and key != "none"
    completed = status == ManagedJobStatus.COMPLETED
    label = _next_action_label(key, status, viewer_role)
    headline = "Job completed" if completed else label
    explanation = (
        "Payout released per the escrow rules."
        if completed
        else _ACTION_EXPLANATIONS.get(key, "This step is waiting.")
    )
    reassurance = (
        "No payment here — your money stays in escrow until you confirm delivery"
        if viewer_role == BUYER
        else "Every step is timestamped and visible to the job's parties"
    )
    return {
        "current_stage": status,
        "stage_label": STATUS_LABELS.get(status, str(status or "")),
        "owner_role": owner_role,
        "is_mine": is_mine,
        "next_action": {
            "key": key,
            "label": label,
            "target": _target_route(key, job, viewer_role),
        },
        "due_at": getattr(job, "due_date", None),
        "waiting_since": getattr(job, "updated_at", None) or getattr(job, "created_at", None),
        "overdue_level": _overdue_level(getattr(job, "due_date", None), now),
        "payment_confirmed": bool(getattr(job, "payment_confirmed", False)),
        "artwork_satisfied": _artwork_satisfied_flag(job),
        "artwork_confirmation_state": _artwork_confirmation_state(job),
        "headline": headline,
        "explanation": explanation,
        "reassurance": reassurance,
        "waiting_label": None if completed else _waiting_label(owner_role, viewer_role),
        "phase": _phase_for_status(status),
        "completed": completed,
    }
