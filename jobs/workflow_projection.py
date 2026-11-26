"""Seven-stage, role-aware workflow projection.

The production status model is deliberately richer than the five canonical
stages.  This module projects the existing status, payment, artwork and printer
workflow rows onto the seven user-facing stages the product now uses:

    Quote -> Artwork -> Payment -> Production -> Finishing -> QC -> Delivery

It is a read-only projection.  It does not introduce new status values, mutate
a job, or replace the five-stage projection in ``jobs.stage_mapping``.  The
five-stage projection remains available for existing consumers while the UI
migrates.
"""

from __future__ import annotations

from typing import Any

from jobs.action_resolver import resolve_next_step
from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
)

WORKFLOW_STAGE_KEYS: tuple[str, ...] = (
    "quote",
    "artwork",
    "payment",
    "production",
    "finishing",
    "qc",
    "delivery",
)

WORKFLOW_STAGE_LABELS: dict[str, str] = {
    "quote": "Quote",
    "artwork": "Artwork",
    "payment": "Payment",
    "production": "Production",
    "finishing": "Finishing",
    "qc": "Quality check",
    "delivery": "Delivery",
}

WORKFLOW_STAGE_INDEX: dict[str, int] = {
    key: index for index, key in enumerate(WORKFLOW_STAGE_KEYS, start=1)
}

PAID_PAYMENT_STATUSES = frozenset(
    {
        ManagedJobPaymentStatus.CONFIRMED,
        ManagedJobPaymentStatus.RELEASE_READY,
        ManagedJobPaymentStatus.PAYOUT_ON_HOLD,
        ManagedJobPaymentStatus.RELEASED,
    }
)

TERMINAL_STATUSES = frozenset(
    {
        ManagedJobStatus.COMPLETED,
        ManagedJobStatus.DELIVERED,
    }
)

POST_PRODUCTION_STATUSES = frozenset(
    {
        ManagedJobStatus.FINISHING,
        ManagedJobStatus.READY,
        ManagedJobStatus.DELIVERED,
        ManagedJobStatus.COMPLETED,
    }
)

POST_FINISHING_STATUSES = frozenset(
    {
        ManagedJobStatus.READY,
        ManagedJobStatus.DELIVERED,
        ManagedJobStatus.COMPLETED,
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def _active_assignment(job: Any):
    try:
        return (
            job.assignments.filter(reassigned_from__isnull=True)
            .exclude(
                status__in=[
                    JobAssignmentStatus.REJECTED,
                    JobAssignmentStatus.CANCELLED,
                ]
            )
            .order_by("-id")
            .first()
        )
    except Exception:  # pragma: no cover - projection must never crash a read
        return None


def _artwork_satisfied(job: Any) -> bool:
    try:
        from jobs.artwork_workflow import artwork_satisfied

        return bool(artwork_satisfied(job))
    except Exception:  # pragma: no cover
        return bool(getattr(job, "artwork_uploaded", False))


def _artwork_claim_state(job: Any) -> str:
    metadata = getattr(job, "workflow_metadata", None)
    if not isinstance(metadata, dict):
        return "none"
    payload = metadata.get("artwork_workflow")
    if not isinstance(payload, dict):
        return "none"
    return _text(payload.get("claim_state")) or "none"


def _artwork_confirmation_state(job: Any) -> str:
    metadata = getattr(job, "workflow_metadata", None)
    if not isinstance(metadata, dict):
        return "not_required"
    payload = metadata.get("artwork_confirmation")
    if not isinstance(payload, dict):
        return "not_required"
    return _text(payload.get("state")) or "not_required"


def _printer_stage(job: Any) -> str:
    assignment = _active_assignment(job)
    if assignment is None:
        return ""
    try:
        from jobs.printer_workflow import build_printer_progress

        progress = build_printer_progress(assignment)
        return _text(progress.get("current_stage"))
    except Exception:  # pragma: no cover - projection must never crash a read
        return ""


def _payment_status(job: Any) -> str:
    return _text(getattr(job, "payment_status", ""))


def _has_assignment(job: Any) -> bool:
    return _active_assignment(job) is not None


def _payment_done(job: Any) -> bool:
    status = _text(getattr(job, "status", ""))
    if status in {ManagedJobStatus.DRAFT, ManagedJobStatus.QUOTED}:
        return False
    return _payment_status(job) in PAID_PAYMENT_STATUSES or status in {
        ManagedJobStatus.PAYMENT_CONFIRMED,
        ManagedJobStatus.ASSIGNED,
        ManagedJobStatus.IN_PRODUCTION,
        ManagedJobStatus.FINISHING,
        ManagedJobStatus.READY,
        ManagedJobStatus.DELIVERED,
        ManagedJobStatus.COMPLETED,
    }


def _current_stage_key(
    *,
    job: Any,
    status: str,
    artwork_satisfied: bool,
    printer_stage: str,
) -> str:
    """Pick the stage that best explains the server's current state.

    Each stage's done/current state is also computed independently below, so a
    legacy or unusual record can still render a coherent rail.
    """
    if status == ManagedJobStatus.DRAFT or status == ManagedJobStatus.QUOTED:
        return "quote"
    if status == ManagedJobStatus.AWAITING_PAYMENT:
        return "payment" if artwork_satisfied else "artwork"
    if status in {ManagedJobStatus.PAYMENT_CONFIRMED, ManagedJobStatus.ASSIGNED}:
        return "production"
    if status == ManagedJobStatus.IN_PRODUCTION:
        return "production"
    if status == ManagedJobStatus.FINISHING:
        if printer_stage == "quality_check":
            return "qc"
        if printer_stage == "ready_for_dispatch":
            return "delivery"
        return "finishing"
    if status == ManagedJobStatus.READY:
        return "delivery"
    if status in {ManagedJobStatus.DELIVERED, ManagedJobStatus.COMPLETED}:
        return "delivery"
    if status in {ManagedJobStatus.DISPUTED, ManagedJobStatus.CANCELLED}:
        # Terminal exception states still show the last real stage.  The
        # substate carries "Disputed" / "Cancelled".
        if getattr(job, "delivered_at", None) or getattr(job, "ready_at", None):
            return "delivery"
        if getattr(job, "production_started_at", None):
            return "production"
        if getattr(job, "payment_confirmed_at", None):
            return "production"
        if getattr(job, "accepted_at", None):
            return "artwork"
        return "quote"
    return "quote"


def _stage_done(
    stage_key: str,
    *,
    job: Any,
    status: str,
    artwork_satisfied: bool,
    payment_done: bool,
    printer_stage: str,
) -> bool:
    if status == ManagedJobStatus.COMPLETED:
        return True

    accepted = bool(getattr(job, "accepted_at", None)) or status not in {
        ManagedJobStatus.DRAFT,
        ManagedJobStatus.QUOTED,
        ManagedJobStatus.CANCELLED,
    }
    if stage_key == "quote":
        return accepted and status != ManagedJobStatus.DRAFT
    if stage_key == "artwork":
        # Payment cannot be confirmed without artwork; treating payment as
        # artwork-satisfied keeps the rail monotonic for legacy/imported rows.
        return artwork_satisfied or payment_done
    if stage_key == "payment":
        return payment_done
    if stage_key == "production":
        return status in POST_PRODUCTION_STATUSES or printer_stage in {
            "quality_check",
            "ready_for_dispatch",
        }
    if stage_key == "finishing":
        return status in POST_FINISHING_STATUSES or printer_stage in {
            "quality_check",
            "ready_for_dispatch",
        }
    if stage_key == "qc":
        return status in {
            ManagedJobStatus.READY,
            ManagedJobStatus.DELIVERED,
            ManagedJobStatus.COMPLETED,
        } or printer_stage == "ready_for_dispatch"
    if stage_key == "delivery":
        return status in TERMINAL_STATUSES
    return False


def _quote_substate(job: Any, status: str) -> str:
    if status == ManagedJobStatus.DRAFT:
        return "Draft"
    if status == ManagedJobStatus.QUOTED:
        return "Quote sent"
    if getattr(job, "accepted_at", None):
        return "Quote accepted"
    return "Awaiting quote"


def _artwork_substate(job: Any, *, artwork_satisfied: bool) -> str:
    if _artwork_confirmation_state(job) == "rejected":
        return "Artwork changes requested"
    if artwork_satisfied:
        return "Artwork received"
    if _artwork_claim_state(job) == "claimed":
        return "Waiting for manager to confirm artwork"
    return "Awaiting artwork"


def _payment_substate(job: Any) -> str:
    status = _payment_status(job)
    if status == ManagedJobPaymentStatus.PENDING:
        return "Awaiting payment"
    if status == ManagedJobPaymentStatus.CONFIRMATION_PENDING:
        return "Confirming payment"
    if status in {
        ManagedJobPaymentStatus.CONFIRMED,
        ManagedJobPaymentStatus.RELEASE_READY,
        ManagedJobPaymentStatus.PAYOUT_ON_HOLD,
    }:
        return "Payment held"
    if status == ManagedJobPaymentStatus.RELEASED:
        return "Payment released"
    if status == ManagedJobPaymentStatus.REFUNDED:
        return "Payment refunded"
    return "Awaiting payment"


def _production_substate(job: Any, *, printer_stage: str) -> str:
    confirmation = _artwork_confirmation_state(job)
    if confirmation == "requested":
        return "Artwork approval"
    if confirmation == "rejected":
        return "Artwork changes requested"
    if _text(getattr(job, "status", "")) in {
        ManagedJobStatus.PAYMENT_CONFIRMED,
        ManagedJobStatus.ASSIGNED,
    } and not _has_assignment(job):
        return "Ready to dispatch"
    if printer_stage == "accept":
        return "Printer accepted"
    if printer_stage == "artwork_check":
        return "Artwork check"
    if printer_stage == "printing":
        return "Printing"
    if printer_stage.startswith("finishing:"):
        return _finishing_label(printer_stage)
    return "In production"


def _finishing_label(printer_stage: str) -> str:
    raw = printer_stage.removeprefix("finishing:")
    cleaned = raw.replace("-", " ").replace("_", " ").strip()
    return cleaned.title() if cleaned else "Finishing"


def _delivery_substate(job: Any, status: str) -> str:
    if status == ManagedJobStatus.COMPLETED:
        return "Completed"
    if status == ManagedJobStatus.DELIVERED:
        return "Delivered"
    if getattr(job, "delivered_at", None):
        return "Delivered"
    if getattr(job, "dispatched_at", None):
        return "Dispatched"
    return "Ready for dispatch"


def _substate_label(
    *,
    job: Any,
    stage_key: str,
    status: str,
    artwork_satisfied: bool,
    printer_stage: str,
) -> str:
    if status == ManagedJobStatus.CANCELLED:
        return "Cancelled"
    if status == ManagedJobStatus.DISPUTED or getattr(job, "dispute_open", False):
        return "Disputed"
    if stage_key == "quote":
        return _quote_substate(job, status)
    if stage_key == "artwork":
        return _artwork_substate(job, artwork_satisfied=artwork_satisfied)
    if stage_key == "payment":
        return _payment_substate(job)
    if stage_key == "production":
        return _production_substate(job, printer_stage=printer_stage)
    if stage_key == "finishing":
        return _finishing_label(printer_stage) if printer_stage.startswith("finishing:") else "Finishing"
    if stage_key == "qc":
        return "Quality check"
    if stage_key == "delivery":
        return _delivery_substate(job, status)
    return ""


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def build_workflow(job: Any, *, viewer_role: str) -> dict[str, Any]:
    """Return the seven-stage projection for one job and one viewer role."""
    status = _text(getattr(job, "status", ""))
    artwork_satisfied = _artwork_satisfied(job)
    payment_done = _payment_done(job)
    printer_stage = _printer_stage(job)
    current_key = _current_stage_key(
        job=job,
        status=status,
        artwork_satisfied=artwork_satisfied,
        printer_stage=printer_stage,
    )

    resolver_role = "manager" if viewer_role == "admin" else viewer_role
    step = resolve_next_step(job, resolver_role)
    action = step.get("next_action") or {}
    owner_role = _text(step.get("owner_role")) or "none"
    is_action_needed = bool(step.get("is_mine")) and bool(action.get("key") and action.get("key") != "none")

    completed = status == ManagedJobStatus.COMPLETED
    cancelled = status == ManagedJobStatus.CANCELLED
    disputed = status == ManagedJobStatus.DISPUTED or bool(getattr(job, "dispute_open", False))

    stages: list[dict[str, Any]] = []
    current_seen = False
    for key in WORKFLOW_STAGE_KEYS:
        done = _stage_done(
            key,
            job=job,
            status=status,
            artwork_satisfied=artwork_satisfied,
            payment_done=payment_done,
            printer_stage=printer_stage,
        )
        if completed:
            state = "done"
        elif done:
            state = "done"
        elif not current_seen:
            state = "current"
            current_seen = True
        else:
            state = "upcoming"
        stages.append(
            {
                "key": key,
                "label": WORKFLOW_STAGE_LABELS[key],
                "index": WORKFLOW_STAGE_INDEX[key],
                "state": state,
                "is_current": state == "current",
                "is_done": state == "done",
                "is_action_needed": state == "current" and is_action_needed,
            }
        )

    # If every stage is done but the job is only delivered (not completed),
    # keep Delivery as the current visual anchor rather than showing no current
    # stage.  Completed jobs legitimately have no current stage.
    if not any(stage["is_current"] for stage in stages) and not completed:
        for stage in stages:
            if stage["key"] == "delivery":
                stage["state"] = "current"
                stage["is_current"] = True
                stage["is_done"] = False
                current_key = "delivery"
                break

    current_stage = next((stage for stage in stages if stage["is_current"]), None)
    if current_stage is not None:
        current_key = str(current_stage["key"])
        current_stage["substate"] = _substate_label(
            job=job,
            stage_key=current_key,
            status=status,
            artwork_satisfied=artwork_satisfied,
            printer_stage=printer_stage,
        )
    elif completed:
        for stage in stages:
            if stage["key"] == "delivery":
                stage["substate"] = "Completed"
                break

    stage_key = current_key if current_key in WORKFLOW_STAGE_INDEX else "quote"
    substate_label = _substate_label(
        job=job,
        stage_key=stage_key,
        status=status,
        artwork_satisfied=artwork_satisfied,
        printer_stage=printer_stage,
    )

    return {
        "stages": stages,
        "current": {
            "key": stage_key,
            "index": WORKFLOW_STAGE_INDEX[stage_key],
            "label": WORKFLOW_STAGE_LABELS[stage_key],
            "substate": substate_label,
            "state": "done" if completed else "current",
        },
        "completed": completed,
        "cancelled": cancelled,
        "disputed": disputed,
        "owner": {
            "role": owner_role,
            "label": str(step.get("waiting_label") or owner_role),
        },
        "next_action": {
            "code": str(action.get("key") or "none"),
            "label": str(action.get("label") or "No action needed"),
            "target": action.get("target"),
            "actor": str(action.get("actor") or owner_role),
        },
        "is_action_needed": is_action_needed,
        "due_at": _iso(getattr(job, "due_date", None)),
        "is_late": bool(getattr(job, "is_late", False)),
    }


__all__ = [
    "WORKFLOW_STAGE_KEYS",
    "WORKFLOW_STAGE_LABELS",
    "WORKFLOW_STAGE_INDEX",
    "build_workflow",
]
