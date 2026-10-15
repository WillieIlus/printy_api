"""Server-side list grouping and ordering for law 7.5.

Quote and job lists are grouped and ordered by the server, never by the UI.
Each list row carries a ``section``, a ``needs_attention`` flag and a `muted`
flag computed here, plus a stable order so the frontend renders exactly what the
server decided (law 7.5). Financial values are never touched here.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Any, Iterable

from jobs.timing import current_stage_for_job, stage_owner

# Quote sections, in render order (law 7.5).
QUOTE_SECTIONS: tuple[str, ...] = ("unresponded", "replied", "done")
QUOTE_SECTION_LABELS = {
    "unresponded": "Unresponded / sent",
    "replied": "Replied",
    "done": "Done",
}

# Job sections, in render order (law 7.5).
JOB_SECTIONS = ("new", "paid", "in_progress", "finished", "completed")
JOB_SECTION_LABELS = {
    "new": "New",
    "paid": "Paid",
    "in_progress": "In progress",
    "finished": "Finished",
    "completed": "Completed and paid",
}

_VIEWER_STAGE_OWNER = {
    "client": "buyer",
    "partner": "manager",
    "production": "printer",
}

_TERMINAL_QUOTE_STATUSES = {
    "accepted",
    "rejected",
    "expired",
    "cancelled",
    "completed",
    "closed",
    "converted",
}
_REPLIED_QUOTE_STATUSES = {"responded", "needs_confirmation", "viewed"}


def _norm(value) -> str:
    return str(value or "").strip().lower()


def _due_key(value):
    from datetime import datetime, timezone as dt_timezone

    if value is None:
        return datetime.max.replace(tzinfo=dt_timezone.utc)
    return value


# --- jobs ------------------------------------------------------------------
def job_section(*, status, payment_status, assignment_status="") -> str:
    """The law 7.5 job section for a canonical ManagedJob."""
    status = _norm(status)
    payment_status = _norm(payment_status)
    assignment_status = _norm(assignment_status)
    if status == "completed":
        return "completed"
    if status in {"ready", "delivered"}:
        return "finished"
    if status in {"assigned", "in_production", "finishing"} or assignment_status in {
        "accepted",
        "in_production",
        "finishing",
        "ready",
    }:
        return "in_progress"
    if status == "payment_confirmed" or payment_status in {"confirmed", "release_ready", "released"}:
        return "paid"
    return "new"


def job_flags(*, job, role: str) -> dict[str, object]:
    """Section, needs_attention and muted for a ManagedJob list row."""
    section = job_section(
        status=getattr(job, "status", ""),
        payment_status=getattr(job, "payment_status", ""),
        assignment_status=getattr(job, "assignment_status", ""),
    )
    owner = ""
    if section not in {"completed", "cancelled"}:
        owner = _norm(getattr(job, "stage_owner", "")) or _norm(stage_owner(getattr(job, "current_stage", "")))
    viewer_owner = _VIEWER_STAGE_OWNER.get(role, "")
    needs_attention = bool(owner and viewer_owner and owner == viewer_owner)
    return {
        "section": section,
        "section_label": JOB_SECTION_LABELS.get(section, section),
        "needs_attention": needs_attention,
        "muted": section in {"finished", "completed"},
    }


def assignment_flags(*, assignment, role: str = "production") -> dict[str, object]:
    """Section flags for a printer JobAssignment (the printer dashboard list)."""
    managed_job = getattr(assignment, "managed_job", None)
    assignment_status = _norm(getattr(assignment, "status", ""))
    payment_confirmed = _norm(getattr(managed_job, "payment_status", "")) in {
        "confirmed",
        "release_ready",
        "released",
    }
    if assignment_status == "completed":
        section = "completed"
    elif assignment_status == "ready":
        section = "finished"
    elif assignment_status in {"accepted", "in_production", "finishing"}:
        section = "in_progress"
    elif assignment_status == "pending":
        section = "paid" if payment_confirmed else "new"
    else:
        section = job_section(
            status=getattr(managed_job, "status", ""),
            payment_status=getattr(managed_job, "payment_status", ""),
        )
    needs_attention = False
    if assignment_status == "pending":
        needs_attention = True
    elif section == "in_progress":
        owner = _norm(getattr(managed_job, "stage_owner", ""))
        needs_attention = owner == "printer"
    return {
        "section": section,
        "section_label": JOB_SECTION_LABELS.get(section, section),
        "needs_attention": needs_attention and section not in {"completed"},
        "muted": section in {"finished", "completed"},
    }


def job_order_key(row: dict) -> tuple:
    section = str(row.get("section") or "new")
    index = JOB_SECTIONS.index(section) if section in JOB_SECTIONS else len(JOB_SECTIONS)
    timing = row.get("timing") if isinstance(row.get("timing"), dict) else {}
    return (
        index,
        0 if row.get("needs_attention") else 1,
        0 if timing.get("is_late") else 1,
        _due_key(timing.get("due_date")),
    )


def order_jobs(rows) -> list:
    return sorted(rows, key=job_order_key)


def order_assignments(rows) -> list:
    return sorted(rows, key=job_order_key)


# --- quotes ----------------------------------------------------------------
def quote_flags(*, request_status, latest_response_status, viewer_role: str) -> dict[str, object]:
    """Section flags for a quote row.

    ``viewer_role`` is "client" or "partner" — the manager owns the first reply
    while the buyer owns accepting or declining a reply (law 7.5).
    """
    status = _norm(request_status)
    response = _norm(latest_response_status)
    if status in _TERMINAL_QUOTE_STATUSES or response in {"accepted", "rejected", "expired"}:
        section = "done"
    elif response in {"sent", "modified", "revised"}:
        section = "replied"
    else:
        section = "unresponded"
    if section == "unresponded":
        needs_attention = viewer_role == "partner"
    elif section == "replied":
        needs_attention = viewer_role == "client"
    else:
        needs_attention = False
    return {
        "section": section,
        "section_label": QUOTE_SECTION_LABELS.get(section, section),
        "needs_attention": needs_attention,
        "muted": section == "done",
    }


def _recency_key(value) -> float:
    # Newest first within a section.
    if value is None:
        return 0.0
    try:
        return -value.timestamp()
    except Exception:
        return 0.0


def quote_order_key(row: dict) -> tuple:
    section = str(row.get("section") or "unresponded")
    index = QUOTE_SECTIONS.index(section) if section in QUOTE_SECTIONS else len(QUOTE_SECTIONS)
    updated = row.get("updated_at") or row.get("created_at")
    return (index, 0 if row.get("needs_attention") else 1, _recency_key(updated))


def order_quotes(rows) -> list:
    return sorted(rows, key=quote_order_key)
