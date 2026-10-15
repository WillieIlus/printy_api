"""Working-time maths and role projections for law Part 4 (6.2-6.7).

Everything here is derived from :mod:`jobs.timing_config`; no due date or SLA is
invented at call time. Working time is Africa/Nairobi; stored/returned instants
are UTC (law 6.2).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Iterable

from django.utils import timezone

from jobs.timing_config import (
    DUE_DATE_BUFFER_WORKING_HOURS,
    HOLIDAYS,
    MIN_JOBS_FOR_PUBLIC_SCORE,
    SHOP_TIMEZONE,
    STAGE_ORDER,
    STAGES,
    STAGES_BY_KEY,
    WORKING_WEEKDAYS,
    WORK_DAY_END,
    WORK_DAY_START,
    stage_for_status,
)

if TYPE_CHECKING:  # pragma: no cover
    from jobs.models import ManagedJob


# --- calendar primitives ---------------------------------------------------
def is_working_day(day) -> bool:
    return day.weekday() in WORKING_WEEKDAYS and day not in HOLIDAYS


def _combine(day, clock) -> datetime:
    return datetime(day.year, day.month, day.day, clock.hour, clock.minute, tzinfo=SHOP_TIMEZONE)


def next_working_instant(moment: datetime) -> datetime:
    """Return the first working instant at or after ``moment`` (shop-local)."""
    local = moment.astimezone(SHOP_TIMEZONE)
    for _ in range(3660):
        if not is_working_day(local.date()):
            local = _combine(local.date() + timedelta(days=1), WORK_DAY_START)
            continue
        if local.timetz().replace(tzinfo=None) < WORK_DAY_START:
            return _combine(local.date(), WORK_DAY_START)
        if local.timetz().replace(tzinfo=None) >= WORK_DAY_END:
            local = _combine(local.date() + timedelta(days=1), WORK_DAY_START)
            continue
        return local
    raise RuntimeError("No working instant found within a decade; check the holiday calendar.")


def add_working_hours(start: datetime, hours: Decimal | int | float) -> datetime:
    """Return the UTC instant ``hours`` working hours after ``start``."""
    hours = Decimal(str(hours))
    if hours <= 0:
        return start.astimezone(dt_timezone.utc)
    cursor = next_working_instant(start)
    remaining = hours
    for _ in range(3660):
        if remaining <= 0:
            break
        day_end = _combine(cursor.date(), WORK_DAY_END)
        available = Decimal(str((day_end - cursor).total_seconds())) / Decimal("3600")
        if available >= remaining:
            cursor = cursor + timedelta(hours=float(remaining))
            remaining = Decimal("0")
        else:
            remaining -= available
            cursor = next_working_instant(day_end + timedelta(seconds=1))
    return cursor.astimezone(dt_timezone.utc)


# --- SLA helpers -----------------------------------------------------------
def stage_config(stage_key: str | None):
    if stage_key is None:
        return None
    return STAGES_BY_KEY.get(stage_key)


def stage_owner(stage_key: str | None) -> str:
    config = stage_config(stage_key)
    return config.owner if config else ""


def total_stage_sla_working_hours() -> Decimal:
    return sum((stage.sla_working_hours for stage in STAGES), Decimal("0"))


def compute_due_date(
    start: datetime,
    *,
    buffer_working_hours: Decimal = DUE_DATE_BUFFER_WORKING_HOURS,
) -> datetime:
    """Compute the due date from the SLA config (law 6.2), never freehand."""
    total = total_stage_sla_working_hours() + Decimal(str(buffer_working_hours))
    return add_working_hours(start, total)


def stage_windows(start: datetime) -> list[tuple[Any, datetime, datetime]]:
    """Return ``(stage, window_start, window_end)`` per stage, anchored at start."""
    cursor = start
    windows = []
    for stage in STAGES:
        window_start = cursor
        cursor = add_working_hours(cursor, stage.sla_working_hours)
        windows.append((stage, window_start, cursor))
    return windows


# --- per-job projection ----------------------------------------------------
def _anchor(managed_job: "ManagedJob") -> datetime:
    return (
        getattr(managed_job, "accepted_at", None)
        or getattr(managed_job, "payment_confirmed_at", None)
        or getattr(managed_job, "created_at", None)
        or timezone.now()
    )


def current_stage_for_job(managed_job: "ManagedJob") -> str:
    return stage_for_status(getattr(managed_job, "status", None))


def _stage_window(managed_job: "ManagedJob", stage_key: str):
    anchor = _anchor(managed_job)
    for stage, window_start, window_end in stage_windows(anchor):
        if stage.key == stage_key:
            return stage, window_start, window_end
    return None, anchor, anchor


def job_timing(*, managed_job: "ManagedJob", now: datetime | None = None) -> dict[str, Any]:
    """The canonical timing projection for a managed job (server-computed)."""
    now = now or timezone.now()
    stage_key = current_stage_for_job(managed_job)
    stage, window_start, window_end = _stage_window(managed_job, stage_key)
    due = getattr(managed_job, "due_date", None)
    stage_due = window_end
    remaining_minutes = None
    if stage_due is not None:
        remaining_minutes = int((stage_due - now).total_seconds() // 60)
    job_remaining_minutes = None
    if due is not None:
        job_remaining_minutes = int((due - now).total_seconds() // 60)
    late_minutes = 0
    if due is not None and now > due:
        late_minutes = int((now - due).total_seconds() // 60)
    stage_late = bool(stage_due is not None and now > stage_due)
    return {
        "due_date": due,
        "current_stage": stage_key,
        "stage_label": stage.label if stage else "",
        "stage_owner": stage.owner if stage else "",
        "stage_sla_hours": str(stage.sla_working_hours) if stage else None,
        "next_action": stage.next_action if stage else "",
        "stage_started_at": window_start,
        "stage_due_at": stage_due,
        "time_remaining_minutes": job_remaining_minutes,
        "stage_time_remaining_minutes": remaining_minutes,
        "is_late": bool(getattr(managed_job, "is_late", False) or late_minutes > 0),
        "lateness_minutes": max(late_minutes, int(getattr(managed_job, "lateness_minutes", 0) or 0)),
        "stage_overdue": stage_late,
        "revision_count": int(getattr(managed_job, "due_date_revision_count", 0) or 0),
        "due_date_acknowledged_at": getattr(managed_job, "due_date_acknowledged_at", None),
    }


def timing_fields(managed_job: "ManagedJob", *, now: datetime | None = None) -> dict[str, Any]:
    """Timeliness fields keyed exactly as the role allowlists expect.

    Callers merge this into the canonical mapping and pass it through
    ``project_canonical`` so each role only receives what its allowlist allows.
    """
    timing = job_timing(managed_job=managed_job, now=now)
    quote_request = getattr(managed_job, "source_quote_request", None)
    return {
        "due_date": timing["due_date"],
        "current_stage": timing["current_stage"],
        "stage_label": timing["stage_label"],
        "stage_owner": timing["stage_owner"],
        "next_action": timing["next_action"],
        "stage_sla_hours": timing["stage_sla_hours"],
        "time_remaining_minutes": timing["time_remaining_minutes"],
        "is_late": timing["is_late"],
        "lateness_minutes": timing["lateness_minutes"],
        "revision_count": timing["revision_count"],
        "stage_timestamps": stage_timestamps(managed_job),
        "due_date_updated": bool(getattr(quote_request, "date_updated", False)),
        "due_date_updated_old": getattr(quote_request, "date_updated_old", None),
        "due_date_updated_new": getattr(quote_request, "date_updated_new", None),
        "due_date_acknowledged_at": getattr(quote_request, "date_acknowledged_at", None),
    }


def stage_timestamps(managed_job: "ManagedJob") -> dict[str, Any]:
    """Per-stage timestamp snapshot (manager/printer view, law 6.3/6.4)."""
    anchor = _anchor(managed_job)
    current = current_stage_for_job(managed_job)
    rows = []
    for stage, window_start, window_end in stage_windows(anchor):
        rows.append(
            {
                "stage": stage.key,
                "label": stage.label,
                "owner": stage.owner,
                "sla_hours": str(stage.sla_working_hours),
                "started_at": window_start,
                "due_at": window_end,
                "is_current": stage.key == current,
            }
        )
    return {"anchor": anchor, "current_stage": current, "stages": rows}


def stage_timeline(
    managed_job: "ManagedJob",
    *,
    now: datetime | None = None,
    events: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Full per-stage timeline for the printer dashboard and track-job view (law 7.6).

    Each stage reports its owner, SLA, when it started, when it was completed (if
    the append-only event log says so), how long it took or has been running, how
    long remains, and whether it is overdue. It never contains financial data.
    """
    now = now or timezone.now()
    anchor = _anchor(managed_job)
    completions = latest_stage_completions(events or [])
    due = getattr(managed_job, "due_date", None)
    stages: list[dict[str, Any]] = []
    total_allowed_minutes = 0
    total_taken_minutes = 0
    for stage, window_start, window_end in stage_windows(anchor):
        allowed_minutes = int(Decimal(str(stage.sla_working_hours)) * 60)
        total_allowed_minutes += allowed_minutes
        completed_at = completions.get(stage.key)
        if completed_at is not None:
            taken = max(0, int((completed_at - window_start).total_seconds() // 60))
            state = "completed"
            remaining: int | None = None
            overdue = taken > allowed_minutes
        elif window_start <= now:
            taken = max(0, int((now - window_start).total_seconds() // 60))
            remaining = int((window_end - now).total_seconds() // 60)
            overdue = now > window_end
            state = "overdue" if overdue else "current"
        else:
            taken = 0
            remaining = int((window_end - now).total_seconds() // 60)
            overdue = False
            state = "pending"
        total_taken_minutes += taken
        stages.append(
            {
                "key": stage.key,
                "label": stage.label,
                "owner": stage.owner,
                "next_action": stage.next_action,
                "sla_hours": str(stage.sla_working_hours),
                "started_at": window_start,
                "due_at": window_end,
                "completed_at": completed_at,
                "time_taken_minutes": taken,
                "time_remaining_minutes": remaining,
                "is_overdue": overdue,
                "state": state,
            }
        )
    remaining_minutes = None
    if due is not None:
        remaining_minutes = int((due - now).total_seconds() // 60)
    return {
        "due_date": due,
        "current_stage": current_stage_for_job(managed_job),
        "is_late": bool(getattr(managed_job, "is_late", False) or (due is not None and now > due)),
        "lateness_minutes": int(getattr(managed_job, "lateness_minutes", 0) or 0),
        "time_remaining_minutes": remaining_minutes,
        "total_allowed_minutes": total_allowed_minutes,
        "total_taken_minutes": total_taken_minutes,
        "stages": stages,
    }


def latest_stage_completions(events: Iterable[Any]) -> dict[str, datetime]:
    """Map stage -> latest completion timestamp from the append-only event log."""
    completions: dict[str, datetime] = {}
    for event in events:
        if getattr(event, "event", "") in {"stage_completed", "handoff"}:
            stage = getattr(event, "stage", "")
            occurred = getattr(event, "occurred_at", None)
            if stage and occurred is not None:
                existing = completions.get(stage)
                if existing is None or occurred > existing:
                    completions[stage] = occurred
    return completions


# --- ownership + delay attribution (law 6.4) -------------------------------
def attribute_delay(*, managed_job: "ManagedJob", stage_key: str | None = None) -> dict[str, Any]:
    """Attribute a delay to the owner of the stage where the job is waiting.

    Buyer-caused delay (e.g. late artwork approval) is never charged to the
    manager or printer (law 6.4).
    """
    stage_key = stage_key or current_stage_for_job(managed_job)
    owner = stage_owner(stage_key)
    buyer_stage = owner == "buyer"
    return {
        "stage": stage_key,
        "owner": owner,
        "buyer_caused": buyer_stage,
        "counted_against_manager": (not buyer_stage) and owner == "manager",
        "counted_against_printer": (not buyer_stage) and owner == "printer",
    }


# --- reliability (law 6.7) -------------------------------------------------
def on_time_rate(*, completed_jobs: Iterable["ManagedJob"]) -> float | None:
    """On-time % for a completed-job set, or None below MIN_JOBS_FOR_PUBLIC_SCORE.

    A score is only ever a real computed number. Below the threshold the caller
    must show nothing (never a dash or a sample figure).
    """
    jobs = [job for job in completed_jobs if getattr(job, "due_date", None) is not None]
    if len(jobs) < MIN_JOBS_FOR_PUBLIC_SCORE:
        return None
    on_time = sum(1 for job in jobs if not getattr(job, "is_late", False))
    return round(on_time / len(jobs) * 100, 1)


__all__ = [
    "is_working_day",
    "next_working_instant",
    "add_working_hours",
    "stage_config",
    "stage_owner",
    "total_stage_sla_working_hours",
    "compute_due_date",
    "stage_windows",
    "current_stage_for_job",
    "job_timing",
    "timing_fields",
    "stage_timestamps",
    "stage_timeline",
    "latest_stage_completions",
    "attribute_delay",
    "on_time_rate",
    "STAGE_ORDER",
]
