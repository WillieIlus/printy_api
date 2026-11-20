"""One place that maps internal job state to the five canonical stages.

Law 25 (SIMPLIFICATION LAW): users only ever see the five canonical stages
``quote_accepted``, ``artwork_approval``, ``production``, ``qc`` and
``delivery``. The 100 internal status values are never a stage.

This module is intentionally import-light. It must not import
``jobs.printer_workflow`` at module import time because that module imports
``jobs.timing_services`` -> ``jobs.timing`` -> ``jobs.timing_config``. The
printer-progress refinement is therefore imported lazily inside
``canonical_stage_for_job``.
"""

from __future__ import annotations

from typing import Any

from jobs.choices import JobAssignmentStatus, ManagedJobStatus

CANONICAL_STAGE_KEYS: tuple[str, ...] = (
    "quote_accepted",
    "artwork_approval",
    "production",
    "qc",
    "delivery",
)

STAGE_LABELS: dict[str, str] = {
    "quote_accepted": "Quote accepted",
    "artwork_approval": "Artwork approval",
    "production": "Production",
    "qc": "Quality check",
    "delivery": "Delivery",
}

# Every stored ManagedJobStatus maps to one canonical stage. Terminal states
# still need a stage for the card; ``canonical_stage_for_job`` refines them from
# the job's timestamps. The defaults below are only the pure-status fallback.
STATUS_TO_STAGE: dict[str, str] = {
    ManagedJobStatus.DRAFT: "quote_accepted",
    ManagedJobStatus.QUOTED: "quote_accepted",
    ManagedJobStatus.AWAITING_PAYMENT: "quote_accepted",
    ManagedJobStatus.PAYMENT_CONFIRMED: "artwork_approval",
    ManagedJobStatus.ASSIGNED: "artwork_approval",
    ManagedJobStatus.IN_PRODUCTION: "production",
    ManagedJobStatus.FINISHING: "production",
    ManagedJobStatus.READY: "delivery",
    ManagedJobStatus.DELIVERED: "delivery",
    ManagedJobStatus.COMPLETED: "delivery",
    ManagedJobStatus.DISPUTED: "qc",
    ManagedJobStatus.CANCELLED: "quote_accepted",
}


def canonical_stage_for_status(status: str | None) -> str:
    """Map a stored status to a canonical stage (default: quote accepted)."""
    if not status:
        return "quote_accepted"
    return STATUS_TO_STAGE.get(str(status), "quote_accepted")


def stage_label(stage_key: str | None) -> str:
    return STAGE_LABELS.get(str(stage_key or ""), "Quote accepted")


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
    except Exception:  # pragma: no cover - defensive for unsaved/odd jobs
        return None


def _stage_from_printer_progress(job: Any) -> str | None:
    """Return qc/delivery when the printer's own stage is ahead of the status.

    ``printer_workflow.py`` is the authority for the printer's real stage. A job
    sits at ``finishing`` while the printer runs the finishing steps and then
    the quality check, and only becomes ``ready`` after "Ready for dispatch".
    Status alone cannot tell QC apart from finishing, so the printer's current
    stage is the tie-breaker.
    """
    assignment = _active_assignment(job)
    if assignment is None:
        return None
    try:
        from jobs.printer_workflow import build_printer_progress

        progress = build_printer_progress(assignment)
    except Exception:  # pragma: no cover - never let a projection crash a read
        return None
    current = str(progress.get("current_stage") or "")
    if current == "quality_check":
        return "qc"
    if current == "ready_for_dispatch":
        return "delivery"
    if current == "printing" or current.startswith("finishing:"):
        return "production"
    if current in {"accept", "artwork_check"}:
        return "artwork_approval"
    return None


def _stage_from_timestamps(job: Any) -> str:
    """Best last-known stage for terminal states (disputed/cancelled)."""
    if getattr(job, "delivered_at", None) or getattr(job, "ready_at", None):
        return "delivery"
    if getattr(job, "production_started_at", None):
        return "qc"
    if getattr(job, "payment_confirmed_at", None):
        return "artwork_approval"
    return "quote_accepted"


def canonical_stage_for_job(job: Any) -> str:
    """Return the one canonical stage for a managed job."""
    status = str(getattr(job, "status", "") or "")
    stage = canonical_stage_for_status(status)
    if status in {ManagedJobStatus.DISPUTED, ManagedJobStatus.CANCELLED}:
        return _stage_from_timestamps(job)
    if status == ManagedJobStatus.READY:
        # READY is set only after the printer's "Ready for dispatch" tap.
        # Status is authoritative here; stale earlier printer events must not
        # pull the card back to QC.
        return "delivery"
    if status == ManagedJobStatus.IN_PRODUCTION:
        # IN_PRODUCTION is already the canonical production stage. A stale
        # artwork_check row must not pull it backwards.
        return "production"
    if status in {
        ManagedJobStatus.FINISHING,
    }:
        refined = _stage_from_printer_progress(job)
        if refined:
            return refined
        return "production"
    return stage


__all__ = [
    "CANONICAL_STAGE_KEYS",
    "STAGE_LABELS",
    "STATUS_TO_STAGE",
    "canonical_stage_for_status",
    "canonical_stage_for_job",
    "stage_label",
]
