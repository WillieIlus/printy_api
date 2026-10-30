"""Buyer-facing live tracking projection for paid, owned jobs."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import Any, Iterable

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.job_codes import job_code
from jobs.models import ManagedJob
from jobs.timing_config import STAGES_BY_KEY


BUYER_STAGES: tuple[tuple[str, str], ...] = (
    ("payment_received", "Payment received"),
    ("artwork_check", "Artwork check"),
    ("printing", "Printing"),
    ("finishing", "Finishing"),
    ("quality_check", "Quality check"),
    ("out_for_delivery", "Out for delivery"),
    ("delivered", "Delivered"),
)

PAID_PAYMENT_STATUSES = (
    ManagedJobPaymentStatus.CONFIRMED,
    ManagedJobPaymentStatus.RELEASE_READY,
    ManagedJobPaymentStatus.PAYOUT_ON_HOLD,
    ManagedJobPaymentStatus.RELEASED,
)

_DELIVERED_STATUSES = {ManagedJobStatus.DELIVERED, ManagedJobStatus.COMPLETED}
_PRODUCTION_STATUSES = {
    ManagedJobStatus.IN_PRODUCTION,
    ManagedJobStatus.FINISHING,
    ManagedJobStatus.READY,
    ManagedJobStatus.DELIVERED,
    ManagedJobStatus.COMPLETED,
}
_FINISHING_STATUSES = {
    ManagedJobStatus.FINISHING,
    ManagedJobStatus.READY,
    ManagedJobStatus.DELIVERED,
    ManagedJobStatus.COMPLETED,
}
_READY_STATUSES = {
    ManagedJobStatus.READY,
    ManagedJobStatus.DELIVERED,
    ManagedJobStatus.COMPLETED,
}


def tracked_jobs_queryset(*, user, now: datetime | None = None):
    now = now or timezone.now()
    cutoff = now - timedelta(hours=24)
    return (
        ManagedJob.objects.filter(
            Q(client=user) | Q(client__isnull=True, created_by=user),
            payment_status__in=PAID_PAYMENT_STATUSES,
        )
        .exclude(status=ManagedJobStatus.CANCELLED)
        .filter(Q(delivered_at__isnull=True) | Q(delivered_at__gte=cutoff))
        .select_related("source_quote_request")
        .prefetch_related(
            "assignments",
            "job_events",
            "source_quote_request__items__finishings__finishing_rate",
        )
    )


def buyer_tracked_jobs(*, user, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or timezone.now()
    rows = [
        build_tracked_job(job, now=now)
        for job in tracked_jobs_queryset(user=user, now=now)
    ]
    rows = [row for row in rows if not _delivery_expired(row, now=now)]
    rows.sort(
        key=lambda row: (
            0 if row["is_late"] else 1,
            row["_due_sort"],
            row["_job_id"],
        )
    )
    for row in rows:
        row.pop("_due_sort", None)
        row.pop("_delivered_sort", None)
        row.pop("_job_id", None)
    return rows


def build_tracked_job(job: ManagedJob, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or timezone.now()
    events = list(job.job_events.all())
    finishing_started_at = _finishing_started_at(job, events)
    milestones = _milestones(job=job, finishing_started_at=finishing_started_at)
    stages = _buyer_stages(milestones=milestones)
    current_key = _current_key(milestones=milestones)
    current_label = dict(BUYER_STAGES)[current_key]
    finishing_names = finishing_labels(job)
    finishing_status = next(stage["status"] for stage in stages if stage["key"] == "finishing")
    finishing_started = next(stage["started_at"] for stage in stages if stage["key"] == "finishing")
    finishing_completed = next(stage["completed_at"] for stage in stages if stage["key"] == "finishing")
    finishing_steps = [
        {
            "key": _slug(label),
            "name": label,
            "label": label,
            "status": finishing_status,
            "started_at": finishing_started,
            "completed_at": finishing_completed,
        }
        for label in finishing_names
    ]

    due_at = getattr(job, "due_date", None)
    overdue_minutes = 0
    is_late = bool(getattr(job, "is_late", False))
    if due_at is not None and now > due_at:
        is_late = True
        overdue_minutes = int((now - due_at).total_seconds() // 60)
    overdue_minutes = max(overdue_minutes, int(getattr(job, "lateness_minutes", 0) or 0))
    if not is_late:
        overdue_minutes = 0

    remaining = 0 if is_late else _remaining_sla_minutes(milestones)
    last_updated_at = _last_updated_at(job=job, stages=stages)

    return {
        "id": job.id,
        "job_code": job_code(job),
        "title": job.title or "Managed print job",
        "current_stage": current_label,
        "current_stage_key": current_key,
        "stage_note": _stage_note(
            current_key=current_key,
            finishing_labels=finishing_names,
            delivered=milestones["delivered"],
        ),
        "stage_number": _stage_number(current_key),
        "stage_count": len(BUYER_STAGES),
        "state": _buyer_state(is_late=is_late, delivered=milestones["delivered"]),
        "stages": stages,
        "finishing_steps": finishing_steps,
        "finishing_substeps": finishing_steps,
        "due_at": _iso(due_at),
        "estimated_remaining_minutes": int(remaining),
        "is_late": is_late,
        "overdue_minutes": overdue_minutes,
        "lateness_minutes": overdue_minutes,
        "last_updated_at": last_updated_at,
        "_due_sort": due_at or datetime.max.replace(tzinfo=dt_timezone.utc),
        "_delivered_sort": _iso(milestones["delivered_at"]),
        "_job_id": job.id,
    }


def _milestones(*, job: ManagedJob, finishing_started_at: datetime | None) -> dict[str, Any]:
    status = job.status
    payment_received_at = job.payment_confirmed_at or job.accepted_at or job.created_at
    artwork_done_at = job.production_started_at
    artwork_done = bool(artwork_done_at) or status in _PRODUCTION_STATUSES
    printing_started_at = job.production_started_at
    printing_started = bool(printing_started_at) or status in _PRODUCTION_STATUSES
    finishing_started = bool(finishing_started_at) or status in _FINISHING_STATUSES
    printing_done = finishing_started
    finishing_done_at = job.ready_at
    finishing_done = bool(finishing_done_at) or status in _READY_STATUSES
    qc_done_at = job.ready_at
    qc_done = bool(qc_done_at) or status in _READY_STATUSES
    delivered_at = job.delivered_at
    if delivered_at is None and status in _DELIVERED_STATUSES:
        delivered_at = job.completed_at or job.updated_at
    delivered = bool(delivered_at) or status in _DELIVERED_STATUSES
    return {
        "payment_received_at": payment_received_at,
        "artwork_done_at": artwork_done_at,
        "artwork_done": artwork_done,
        "printing_started_at": printing_started_at,
        "printing_started": printing_started,
        "printing_done": printing_done,
        "finishing_started_at": finishing_started_at,
        "finishing_started": finishing_started,
        "finishing_done_at": finishing_done_at,
        "finishing_done": finishing_done,
        "qc_done_at": qc_done_at,
        "qc_done": qc_done,
        "delivered_at": delivered_at,
        "completed_at": job.completed_at,
        "delivered": delivered,
        "completed": status == ManagedJobStatus.COMPLETED,
    }


def _remaining_sla_minutes(milestones: dict[str, Any]) -> int:
    production = STAGES_BY_KEY["production"].sla_working_hours
    production_step = production / 2
    remaining = Decimal("0")
    if not milestones["artwork_done"]:
        remaining += STAGES_BY_KEY["artwork_approval"].sla_working_hours
    if not milestones["printing_done"]:
        remaining += production_step
    if not milestones["finishing_done"]:
        remaining += production_step
    if not milestones["qc_done"]:
        remaining += STAGES_BY_KEY["qc"].sla_working_hours
    if not milestones["delivered"]:
        remaining += STAGES_BY_KEY["delivery"].sla_working_hours
    return int(remaining * 60)


def _last_updated_at(*, job: ManagedJob, stages: list[dict[str, Any]]) -> str:
    candidates = [job.updated_at]
    for stage in stages:
        for key in ("started_at", "completed_at"):
            value = _as_datetime(stage.get(key))
            if value is not None:
                candidates.append(value)
    latest = max((value for value in candidates if value is not None), default=timezone.now())
    return _iso(latest)


def _delivery_expired(row: dict[str, Any], *, now: datetime) -> bool:
    delivered_at = _as_datetime(row.get("_delivered_sort"))
    if delivered_at is None:
        return False
    return delivered_at < now - timedelta(hours=24)


def _stage_note(*, current_key: str, finishing_labels: list[str], delivered: bool) -> str:
    """Plain words for the buyer, e.g. "Being laminated" (never shop identity)."""
    if delivered:
        return "Delivered"
    if current_key == "finishing":
        label = finishing_labels[0] if finishing_labels else ""
        lowered = label.lower()
        if "lamination" in lowered or "laminate" in lowered:
            return "Being laminated"
        if "cut" in lowered:
            return "Being cut"
        if "bind" in lowered or "saddle" in lowered or "stitch" in lowered:
            return "Being bound"
        if "fold" in lowered:
            return "Being folded"
        return "Finishing"
    return {
        "payment_received": "Payment received",
        "artwork_check": "Checking your artwork",
        "printing": "Printing",
        "quality_check": "Quality check",
        "out_for_delivery": "Out for delivery",
        "delivered": "Delivered",
    }.get(current_key, "In progress")


def _stage_number(current_key: str) -> int:
    keys = [key for key, _label in BUYER_STAGES]
    return keys.index(current_key) + 1 if current_key in keys else 1


def _buyer_state(*, is_late: bool, delivered: bool) -> str:
    if delivered:
        return "delivered"
    if is_late:
        return "late"
    return "on_track"


def _current_key(*, milestones: dict[str, Any]) -> str:
    if milestones["completed"]:
        return "delivered"
    if milestones["delivered"]:
        return "delivered"
    if milestones["qc_done"]:
        return "out_for_delivery"
    if milestones["finishing_started"]:
        return "finishing"
    if milestones["printing_started"]:
        return "printing"
    return "artwork_check"


def _buyer_stages(*, milestones: dict[str, Any]) -> list[dict[str, Any]]:
    current = _current_key(milestones=milestones)
    printing_started_at = milestones["printing_started_at"]
    finishing_started_at = milestones["finishing_started_at"]
    finishing_done_at = milestones["finishing_done_at"]
    ready_at = milestones["qc_done_at"]
    delivered_at = milestones["delivered_at"]
    rows: list[dict[str, Any]] = []

    def add(key: str, status: str, started_at=None, completed_at=None) -> None:
        rows.append(
            {
                "key": key,
                "label": dict(BUYER_STAGES)[key],
                "status": status,
                "started_at": _iso(started_at),
                "completed_at": _iso(completed_at),
            }
        )

    add("payment_received", "done", milestones["payment_received_at"], milestones["payment_received_at"])

    if milestones["artwork_done"]:
        add("artwork_check", "done", milestones["payment_received_at"], milestones["artwork_done_at"])
    else:
        add("artwork_check", "current" if current == "artwork_check" else "upcoming", milestones["payment_received_at"])

    if milestones["finishing_started"] or milestones["finishing_done"]:
        add("printing", "done", printing_started_at, finishing_started_at or finishing_done_at)
    else:
        add("printing", "current" if current == "printing" else "upcoming", printing_started_at)

    if milestones["finishing_done"]:
        add("finishing", "done", finishing_started_at or finishing_done_at, finishing_done_at)
    else:
        add("finishing", "current" if current == "finishing" else "upcoming", finishing_started_at)

    if milestones["qc_done"]:
        add("quality_check", "done", finishing_done_at or ready_at, ready_at)
    else:
        add("quality_check", "current" if current == "quality_check" else "upcoming")

    if milestones["delivered"]:
        add("out_for_delivery", "done", ready_at, delivered_at)
    else:
        add("out_for_delivery", "current" if current == "out_for_delivery" else "upcoming", ready_at)

    if milestones["completed"]:
        add("delivered", "done", delivered_at, milestones.get("completed_at"))
    elif milestones["delivered"]:
        add("delivered", "current", delivered_at)
    else:
        add("delivered", "upcoming")
    return rows


def _finishing_started_at(job: ManagedJob, events: Iterable[Any]) -> datetime | None:
    values: list[datetime] = []
    for assignment in job.assignments.all():
        snapshot = assignment.operational_snapshot if isinstance(assignment.operational_snapshot, dict) else {}
        value = _as_datetime(snapshot.get("finishing_started_at"))
        if value is not None:
            values.append(value)
    for event in events:
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        value = _as_datetime(metadata.get("finishing_started_at"))
        if value is not None:
            values.append(value)
    return max(values) if values else None


def finishing_labels(job: ManagedJob) -> list[str]:
    quote_request = job.source_quote_request
    root = quote_request.request_snapshot if quote_request and isinstance(quote_request.request_snapshot, dict) else {}
    snapshot = root.get("request_snapshot") if isinstance(root.get("request_snapshot"), dict) else root
    values: list[str] = []

    raw_finishings = snapshot.get("finishings")
    if isinstance(raw_finishings, list):
        for entry in raw_finishings:
            if isinstance(entry, dict):
                label = entry.get("label") or entry.get("name") or entry.get("value") or entry.get("slug")
            else:
                label = entry
            label = str(label or "").strip()
            if label and label.lower() not in {"none", "no", "no-lamination", "no_lamination"}:
                values.append(label)

    for key in ("lamination", "cover_lamination", "cover_lamination_mode", "binding_type"):
        label = str(snapshot.get(key) or "").strip()
        if label and label.lower() not in {"none", "no", "no-lamination", "no_lamination"}:
            values.append(label)

    if quote_request is not None:
        quote_item = quote_request.items.all().first()
        if quote_item is not None:
            for finishing in quote_item.finishings.all():
                rate = getattr(finishing, "finishing_rate", None)
                label = str(getattr(rate, "name", "") or "").strip()
                if label:
                    values.append(label)

    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        result = parse_datetime(value.strip())
    else:
        return None
    if result is None:
        return None
    if timezone.is_naive(result):
        return timezone.make_aware(result, dt_timezone.utc)
    return result


def _iso(value) -> str | None:
    parsed = _as_datetime(value)
    return parsed.isoformat() if parsed is not None else None
