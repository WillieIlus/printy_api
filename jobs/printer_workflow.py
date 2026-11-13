"""One-action printer stage workflow (law Part 5 + prompt 4).

The plant floor gets exactly one next action per job. The server owns the
stage order, the action label, the timestamp and the idempotency check; the
client only sends which stage it believes is current (or a request id).

Everything is derived from the append-only :class:`jobs.models.JobEvent` log.
No extra model is introduced: an advance is a ``stage_started`` or
``stage_completed`` event carrying a ``printer_stage`` in its metadata, and an
undo is an append-only ``correction`` event referencing the original. The
original timestamps are never edited, so both sides of a mis-tap stay in the
audit trail.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from jobs.buyer_tracking import finishing_labels
from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
)
from jobs.models import JobAssignment, JobEvent, ManagedJob
from jobs.timing_services import record_job_event, sync_job_timing

# --- action + policy constants --------------------------------------------
PAID_PAYMENT_STATUSES = frozenset(
    {
        ManagedJobPaymentStatus.CONFIRMED,
        ManagedJobPaymentStatus.RELEASE_READY,
        ManagedJobPaymentStatus.PAYOUT_ON_HOLD,
        ManagedJobPaymentStatus.RELEASED,
    }
)
UNDO_WINDOW = timedelta(minutes=2)
DOUBLE_TAP_WINDOW = timedelta(seconds=60)
FINISHED_STATUSES = frozenset(
    {
        ManagedJobStatus.DELIVERED,
        ManagedJobStatus.COMPLETED,
    }
)
LOCKED_ASSIGNMENT_STATUSES = frozenset(
    {
        JobAssignmentStatus.REJECTED,
        JobAssignmentStatus.CANCELLED,
        JobAssignmentStatus.COMPLETED,
    }
)

# Display and nudge budgets. The due date itself still comes from the law 6.2
# SLA config; these only decide when a stage is running over and what the
# printer is asked. Prompt general replaces these weights with measured medians.
BUDGET_MINUTES = {
    "accept": 60,
    "artwork_check": 60,
    "printing": 180,
    "finishing": 45,
    "quality_check": 30,
    "ready_for_dispatch": 30,
}

ACTION_ARTWORK_CHECKED = "Artwork checked"
ACTION_START_PRINTING = "Start printing"
ACTION_PRINTING_DONE = "Printing done"
ACTION_QC_PASSED = "QC passed"
ACTION_READY_FOR_DISPATCH = "Ready for dispatch"
ACTION_ACCEPT = "Accept job"


class PrinterWorkflowError(Exception):
    """A refusal the API can turn into a clear response."""

    def __init__(self, detail: str, *, status_code: int = 400, code: str = "printer_workflow"):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class PrinterStagePlan:
    key: str
    label: str
    kind: str  # accept | single | start_done
    done_action: str
    start_action: str = ""
    budget_minutes: int = 0

    @property
    def is_single(self) -> bool:
        return self.kind == "single"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _stage_label(value: str) -> str:
    cleaned = str(value or "").strip()
    return cleaned[:1].upper() + cleaned[1:] if cleaned else ""


def stage_plan(*, assignment: JobAssignment) -> list[PrinterStagePlan]:
    """The ordered printer stages for *this* job, finishing steps included only
    when the job actually carries them (prompt 4 item 3)."""
    plans: list[PrinterStagePlan] = []
    if assignment.status == JobAssignmentStatus.PENDING:
        plans.append(
            PrinterStagePlan(
                key="accept",
                label="Accept job",
                kind="accept",
                done_action=ACTION_ACCEPT,
                budget_minutes=BUDGET_MINUTES["accept"],
            )
        )
    plans.append(
        PrinterStagePlan(
            key="artwork_check",
            label="Artwork check",
            kind="single",
            done_action=ACTION_ARTWORK_CHECKED,
            budget_minutes=BUDGET_MINUTES["artwork_check"],
        )
    )
    plans.append(
        PrinterStagePlan(
            key="printing",
            label="Printing",
            kind="start_done",
            start_action=ACTION_START_PRINTING,
            done_action=ACTION_PRINTING_DONE,
            budget_minutes=BUDGET_MINUTES["printing"],
        )
    )
    seen: set[str] = set()
    for label in finishing_labels(assignment.managed_job):
        key = f"finishing:{_slug(label)}"
        if not key or key in seen:
            continue
        seen.add(key)
        plans.append(
            PrinterStagePlan(
                key=key,
                label=_stage_label(label),
                kind="start_done",
                start_action=f"Start {label.strip().lower()}",
                done_action=f"{_stage_label(label)} done",
                budget_minutes=BUDGET_MINUTES["finishing"],
            )
        )
    plans.append(
        PrinterStagePlan(
            key="quality_check",
            label="Quality check",
            kind="single",
            done_action=ACTION_QC_PASSED,
            budget_minutes=BUDGET_MINUTES["quality_check"],
        )
    )
    plans.append(
        PrinterStagePlan(
            key="ready_for_dispatch",
            label="Ready for dispatch",
            kind="single",
            done_action=ACTION_READY_FOR_DISPATCH,
            budget_minutes=BUDGET_MINUTES["ready_for_dispatch"],
        )
    )
    return plans


# --- event-log reading -----------------------------------------------------
def _as_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _events(job: ManagedJob) -> list[JobEvent]:
    manager = getattr(job, "job_events", None)
    if manager is None:
        return []
    return list(manager.all())


def _reverted_ids(events: Iterable[JobEvent]) -> set[int]:
    reverted: set[int] = set()
    for event in events:
        if event.event != JobEvent.CORRECTION or not event.references_id:
            continue
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        if metadata.get("revert"):
            reverted.add(event.references_id)
    return reverted


def _advance_events(job: ManagedJob) -> list[JobEvent]:
    events = _events(job)
    reverted = _reverted_ids(events)
    return [
        event
        for event in events
        if event.id not in reverted
        and event.event in {JobEvent.STAGE_STARTED, JobEvent.STAGE_COMPLETED}
        and isinstance(event.metadata, dict)
        and event.metadata.get("printer_stage")
    ]


def _latest_group_events(job: ManagedJob, *, actor=None) -> list[JobEvent]:
    advances = [event for event in _advance_events(job) if actor is None or event.actor_id == getattr(actor, "id", None)]
    if not advances:
        return []
    latest = max(advances, key=lambda event: (event.occurred_at, event.id))
    metadata = latest.metadata if isinstance(latest.metadata, dict) else {}
    group = metadata.get("advance_group_id")
    if not group:
        return [latest]
    return [event for event in advances if (event.metadata or {}).get("advance_group_id") == group]


def _accepted_at(assignment: JobAssignment, job: ManagedJob) -> datetime | None:
    """When *this printer* accepted the assignment.

    ``job.accepted_at`` is the buyer accepting the quote, which is not the same
    event: a pending assignment must still show "Accept job" even when the
    buyer accepted the quote days ago.
    """
    if assignment.accepted_at is not None:
        return assignment.accepted_at
    if str(assignment.status or "") not in {
        JobAssignmentStatus.PENDING,
        JobAssignmentStatus.REJECTED,
        JobAssignmentStatus.CANCELLED,
    }:
        return assignment.created_at
    return None


def _base_start(assignment: JobAssignment, job: ManagedJob) -> datetime:
    return (
        job.payment_confirmed_at
        or job.accepted_at
        or assignment.created_at
        or job.created_at
        or timezone.now()
    )


def _canonical_job_stage(printer_stage: str) -> str:
    if printer_stage.startswith("finishing:") or printer_stage in {"artwork_check", "printing"}:
        return "production"
    if printer_stage == "quality_check":
        return "qc"
    if printer_stage == "ready_for_dispatch":
        return "delivery"
    return "production"


def _canonical_action_label(stage: PrinterStagePlan, action: str) -> str:
    if stage.kind == "accept":
        return ACTION_ACCEPT
    if action == "started":
        return stage.start_action or stage.done_action
    return stage.done_action


# --- progress projection ---------------------------------------------------
def build_printer_progress(assignment: JobAssignment, *, now: datetime | None = None) -> dict[str, Any]:
    """Everything the printer card needs, computed from the event log."""
    now = now or timezone.now()
    job = assignment.managed_job
    plans = stage_plan(assignment=assignment)
    events = _advance_events(job)
    accepted = _accepted_at(assignment, job)
    paid = str(job.payment_status or "") in PAID_PAYMENT_STATUSES
    finished_job = str(job.status or "") in FINISHED_STATUSES
    locked = str(assignment.status or "") in LOCKED_ASSIGNMENT_STATUSES

    by_stage: dict[str, list[JobEvent]] = {}
    for event in events:
        key = str((event.metadata or {}).get("printer_stage") or "")
        if key:
            by_stage.setdefault(key, []).append(event)

    stages: list[dict[str, Any]] = []
    previous_done_at: datetime | None = accepted or _base_start(assignment, job)
    previous_done = True
    current_key: str | None = None
    next_action: dict[str, Any] | None = None

    for plan in plans:
        stage_events = by_stage.get(plan.key, [])
        starts = [event for event in stage_events if (event.metadata or {}).get("printer_action") == "started"]
        completions = [event for event in stage_events if (event.metadata or {}).get("printer_action") == "completed"]
        started_at = min((event.occurred_at for event in starts), default=None)
        completed_at = min((event.occurred_at for event in completions), default=None)

        if plan.kind == "accept":
            done = accepted is not None
            if done:
                started_at = assignment.created_at
                completed_at = accepted or assignment.created_at
        else:
            done = completed_at is not None

        if done:
            state = "done"
        elif plan.kind == "start_done" and started_at is not None:
            state = "in_progress"
            started_at = started_at
        elif previous_done and current_key is None:
            state = "current"
        else:
            state = "upcoming"

        if state != "done" and current_key is None:
            current_key = plan.key

        stage_start = started_at or (previous_done_at if previous_done else None)
        if state == "done" and stage_start and completed_at:
            taken = max(0, int((completed_at - stage_start).total_seconds() // 60))
        elif state == "in_progress" and started_at:
            taken = max(0, int((now - started_at).total_seconds() // 60))
        else:
            taken = None

        budget = plan.budget_minutes
        overdue = bool(taken is not None and budget and taken > budget)

        row: dict[str, Any] = {
            "key": plan.key,
            "label": plan.label,
            "kind": plan.kind,
            "state": state,
            "started_at": started_at.isoformat() if started_at else None,
            "completed_at": completed_at.isoformat() if completed_at else None,
            "time_taken_minutes": taken,
            "budget_minutes": budget,
            "is_overdue": overdue,
            "start_action": plan.start_action or "",
            "done_action": plan.done_action,
        }

        if state == "done":
            previous_done_at = completed_at or previous_done_at
            previous_done = True
        elif state in {"in_progress", "current"}:
            previous_done = False
            if next_action is None and paid and not finished_job and not locked:
                action = "completed" if state == "in_progress" or plan.is_single else "started"
                next_action = {
                    "stage_key": plan.key,
                    "stage_label": plan.label,
                    "action": action,
                    "label": _canonical_action_label(plan, action),
                    "kind": "accept" if plan.kind == "accept" else "advance",
                }
        stages.append(row)

    total_taken = sum(int(stage["time_taken_minutes"] or 0) for stage in stages)
    if next_action is None and not finished_job and not locked:
        next_action = {
            "stage_key": "",
            "stage_label": "All steps complete",
            "action": "none",
            "label": "All steps complete",
            "kind": "none",
        }

    latest_group = _latest_group_events(job, actor=None)
    latest_advance = max(
        (event for event in events if (event.metadata or {}).get("printer_action") in {"started", "completed"}),
        key=lambda event: (event.occurred_at, event.id),
        default=None,
    )
    can_undo = bool(
        latest_advance
        and now - latest_advance.occurred_at <= UNDO_WINDOW
        and not finished_job
        and str(assignment.status or "") not in LOCKED_ASSIGNMENT_STATUSES
    )
    undo_expires_at = (
        latest_advance.occurred_at + UNDO_WINDOW if latest_advance and can_undo else None
    )

    current_stage = next((stage for stage in stages if stage["state"] in {"in_progress", "current"}), None)
    return {
        "paid": paid,
        "finished": finished_job,
        "locked": locked,
        "current_stage": current_stage["key"] if current_stage else "",
        "current_stage_label": current_stage["label"] if current_stage else "",
        "current_note": _current_note(current_stage),
        "next_action": next_action,
        "stages": stages,
        "time_taken_minutes": total_taken,
        "can_undo": can_undo,
        "undo_expires_at": undo_expires_at.isoformat() if undo_expires_at else None,
        "latest_group_id": (latest_group[0].metadata or {}).get("advance_group_id") if latest_group else None,
    }


def _current_note(stage: dict[str, Any] | None) -> str:
    if not stage:
        return ""
    key = str(stage.get("key") or "")
    label = str(stage.get("label") or "")
    if key.startswith("finishing:") and stage.get("state") == "in_progress":
        lowered = label.lower()
        if "lamination" in lowered:
            return "Being laminated"
        if "cut" in lowered:
            return "Being cut"
        if "bind" in lowered or "saddle" in lowered:
            return "Being bound"
        return f"Finishing — {label}"
    return label


# --- mutations -------------------------------------------------------------
def _require_printer(assignment: JobAssignment, actor) -> None:
    owner_id = getattr(getattr(assignment, "assigned_shop", None), "owner_id", None)
    if owner_id is None or owner_id != getattr(actor, "id", None):
        raise PrinterWorkflowError("Only the printer assigned to this job can update it.", status_code=403)


def _require_paid(job: ManagedJob) -> None:
    if str(job.payment_status or "") not in PAID_PAYMENT_STATUSES:
        raise PrinterWorkflowError("This job is not paid yet, so it cannot enter production.", status_code=400)
    if str(job.status or "") in {ManagedJobStatus.DRAFT, ManagedJobStatus.QUOTED, ManagedJobStatus.AWAITING_PAYMENT}:
        raise PrinterWorkflowError("This job is not paid yet, so it cannot enter production.", status_code=400)


def _require_not_finished(job: ManagedJob, assignment: JobAssignment) -> None:
    if str(job.status or "") in FINISHED_STATUSES:
        raise PrinterWorkflowError("This job is already finished.", status_code=400)
    if str(job.status or "") == ManagedJobStatus.CANCELLED:
        raise PrinterWorkflowError("This job was cancelled.", status_code=400)
    if str(assignment.status or "") in {JobAssignmentStatus.REJECTED, JobAssignmentStatus.CANCELLED}:
        raise PrinterWorkflowError("This assignment is no longer active.", status_code=400)


def _latest_event(job: ManagedJob) -> JobEvent | None:
    advances = _advance_events(job)
    return max(advances, key=lambda event: (event.occurred_at, event.id), default=None)


def _already_processed(job: ManagedJob, *, request_id: str | None) -> bool:
    if not request_id:
        return False
    return any(
        (event.metadata or {}).get("request_id") == request_id
        for event in _advance_events(job)
    )


def _status_for_stages(stages: list[dict[str, Any]]) -> tuple[str, str, str | None]:
    done = {stage["key"] for stage in stages if stage["state"] == "done"}
    started = {stage["key"] for stage in stages if stage["started_at"]}
    if "quality_check" in done or "ready_for_dispatch" in done:
        return ManagedJobStatus.READY, JobAssignmentStatus.READY, "ready"
    if "printing" in done or any(key.startswith("finishing:") for key in started):
        return ManagedJobStatus.FINISHING, JobAssignmentStatus.FINISHING, "finishing"
    if "printing" in started:
        return ManagedJobStatus.IN_PRODUCTION, JobAssignmentStatus.IN_PRODUCTION, "printing"
    if "artwork_check" in done:
        return ManagedJobStatus.ASSIGNED, JobAssignmentStatus.ACCEPTED, "assigned"
    return ManagedJobStatus.ASSIGNED, JobAssignmentStatus.ACCEPTED, "assigned"


def _sync_job_and_assignment(*, assignment: JobAssignment, job: ManagedJob, stages: list[dict[str, Any]]) -> None:
    if str(job.status or "") in {*FINISHED_STATUSES, ManagedJobStatus.CANCELLED}:
        return
    status, assignment_status, marker = _status_for_stages(stages)
    update_fields = ["status", "assignment_status", "updated_at"]
    job.status = status
    job.assignment_status = assignment_status

    if marker in {"printing", "finishing", "ready"} and job.production_started_at is None:
        job.production_started_at = timezone.now()
        update_fields.append("production_started_at")
    if marker == "ready" and job.ready_at is None:
        job.ready_at = timezone.now()
        update_fields.append("ready_at")
    if marker != "ready" and job.ready_at is not None:
        # An undo moved the job back before QC; the printer-set ready flag must
        # not survive it. Delivery timestamps are never touched here.
        job.ready_at = None
        update_fields.append("ready_at")
    if marker == "assigned" and job.production_started_at is not None:
        # An undo of "Start printing" must remove the production anchor too,
        # otherwise the buyer tracker would still show Printing.
        job.production_started_at = None
        update_fields.append("production_started_at")
    job.save(update_fields=update_fields)

    assignment_fields = ["status", "updated_at"]
    assignment.status = assignment_status
    if assignment_status != JobAssignmentStatus.PENDING and assignment.accepted_at is None:
        assignment.accepted_at = timezone.now()
        assignment_fields.append("accepted_at")
    assignment.save(update_fields=assignment_fields)
    sync_job_timing(job)


def _overrun_minutes(stage: dict[str, Any]) -> int:
    taken = stage.get("time_taken_minutes")
    budget = stage.get("budget_minutes")
    if taken is None or not budget:
        return 0
    return max(0, int(taken) - int(budget))


@transaction.atomic
def advance_printer_job(
    *,
    assignment: JobAssignment,
    actor,
    expected_stage: str | None = None,
    request_id: str | None = None,
    client_timestamp: str | None = None,
) -> tuple[JobAssignment, dict[str, Any], bool]:
    """Advance one stage. Returns ``(assignment, progress, applied)``.

    ``applied`` is False for an idempotent replay (same request id, same
    expected stage already completed, or an obvious double tap).
    """
    _require_printer(assignment, actor)
    job = ManagedJob.objects.select_for_update().get(pk=assignment.managed_job_id)
    assignment = (
        JobAssignment.objects.select_for_update(of=("self",))
        .select_related("managed_job", "assigned_shop")
        .get(pk=assignment.pk)
    )
    assignment.managed_job = job
    _require_paid(job)
    _require_not_finished(job, assignment)
    if str(assignment.status or "") in LOCKED_ASSIGNMENT_STATUSES:
        raise PrinterWorkflowError("This assignment is no longer active.", status_code=400)

    now = timezone.now()
    progress = build_printer_progress(assignment, now=now)
    action = progress["next_action"] or {}
    if action.get("kind") == "none":
        return assignment, progress, False

    if _already_processed(job, request_id=request_id):
        return assignment, build_printer_progress(assignment, now=now), False

    stage_key = str(action.get("stage_key") or "")
    stage_action = str(action.get("action") or "")
    if expected_stage:
        if expected_stage != stage_key:
            prior = next((stage for stage in progress["stages"] if stage["key"] == expected_stage), None)
            if prior and prior["state"] == "done":
                # A retry of the tap that just succeeded: never advance again.
                return assignment, progress, False
            raise PrinterWorkflowError(
                "That stage moved already. Refresh the job to see the next action.",
                status_code=409,
                code="stage_moved",
            )

    latest = _latest_event(job)
    if latest is not None:
        metadata = latest.metadata if isinstance(latest.metadata, dict) else {}
        same_stage = metadata.get("printer_stage") == stage_key
        same_action = metadata.get("printer_action") == stage_action
        if same_stage and same_action and now - latest.occurred_at <= DOUBLE_TAP_WINDOW:
            return assignment, progress, False

    if stage_key == "accept":
        from jobs.assignment_services import accept_assignment

        accept_assignment(assignment=assignment, actor=actor, note="")
        assignment.refresh_from_db()
        sync_job_timing(job)
        return assignment, build_printer_progress(assignment, now=now), True

    if stage_key == "printing":
        try:
            from jobs.timing_services import assert_due_date_for_production

            assert_due_date_for_production(job)
        except ValidationError as exc:
            raise PrinterWorkflowError(str(exc.messages[0] if getattr(exc, "messages", None) else exc)) from exc

    group_id = str(uuid.uuid4())
    event_name = JobEvent.STAGE_STARTED if stage_action == "started" else JobEvent.STAGE_COMPLETED
    metadata: dict[str, Any] = {
        "printer_stage": stage_key,
        "printer_stage_label": action.get("stage_label"),
        "printer_action": stage_action,
        "printer_action_label": action.get("label"),
        "request_id": request_id or group_id,
        "advance_group_id": group_id,
        "expected_stage": expected_stage or "",
        "client_timestamp_ignored": bool(client_timestamp),
        "server_timestamp": now.isoformat(),
        "budget_minutes": next(
            (stage["budget_minutes"] for stage in progress["stages"] if stage["key"] == stage_key),
            None,
        ),
    }
    record_job_event(
        managed_job=job,
        stage=_canonical_job_stage(stage_key),
        event=event_name,
        actor=actor,
        actor_role="printer",
        source=JobEvent.SOURCE_PRINTER,
        occurred_at=now,
        metadata=metadata,
    )

    # Auto-chain the next two-step stage when the previous one finishes
    # (prompt general): the printer sees "Lamination done", not "Start".
    if event_name == JobEvent.STAGE_COMPLETED:
        plans = stage_plan(assignment=assignment)
        position = next((index for index, plan in enumerate(plans) if plan.key == stage_key), None)
        if position is not None:
            for plan in plans[position + 1:]:
                if plan.kind == "start_done":
                    if not plan.key.startswith("finishing:"):
                        # Starting the press stays a deliberate tap; only
                        # finishing steps chain automatically (prompt general).
                        break
                    record_job_event(
                        managed_job=job,
                        stage=_canonical_job_stage(plan.key),
                        event=JobEvent.STAGE_STARTED,
                        actor=actor,
                        actor_role="printer",
                        source=JobEvent.SOURCE_PRINTER,
                        occurred_at=now,
                        metadata={
                            "printer_stage": plan.key,
                            "printer_stage_label": plan.label,
                            "printer_action": "started",
                            "printer_action_label": plan.start_action,
                            "request_id": f"{metadata['request_id']}:auto",
                            "advance_group_id": group_id,
                            "auto_started": True,
                            "server_timestamp": now.isoformat(),
                            "budget_minutes": plan.budget_minutes,
                        },
                    )
                    break
                if plan.kind == "single":
                    break

    job.refresh_from_db()
    refreshed = build_printer_progress(assignment, now=now)
    _sync_job_and_assignment(assignment=assignment, job=job, stages=refreshed["stages"])
    refreshed = build_printer_progress(assignment, now=timezone.now())

    for stage in refreshed["stages"]:
        if stage["key"] != stage_key:
            continue
        over = _overrun_minutes(stage)
        if over:
            record_job_event(
                managed_job=job,
                stage=_canonical_job_stage(stage_key),
                event=JobEvent.DELAY,
                actor=actor,
                actor_role="printer",
                source=JobEvent.SOURCE_PRINTER,
                occurred_at=now,
                metadata={
                    "printer_stage": stage_key,
                    "printer_stage_label": stage["label"],
                    "over_budget_minutes": over,
                    "budget_minutes": stage["budget_minutes"],
                    "time_taken_minutes": stage["time_taken_minutes"],
                    "attributed_owner": "printer",
                    "counted_against_printer": True,
                    "counted_against_manager": False,
                    "buyer_caused": False,
                    "correction_group_id": group_id,
                },
            )

    # Emit a status event the notification system can consume. No notification
    # is sent from here (prompt 4 item 8).
    from jobs.audit_services import record_job_status_event

    record_job_status_event(
        managed_job=job,
        assignment=assignment,
        actor=actor,
        event_type="printer_stage_advanced",
        summary=action.get("label") or "",
        metadata={
            "printer_stage": stage_key,
            "printer_action": stage_action,
            "advance_group_id": group_id,
            "request_id": metadata["request_id"],
        },
    )
    return assignment, refreshed, True


@transaction.atomic
def undo_printer_advance(*, assignment: JobAssignment, actor) -> tuple[JobAssignment, dict[str, Any], bool]:
    """Revert the most recent printer advance within two minutes (prompt 4.5)."""
    _require_printer(assignment, actor)
    job = ManagedJob.objects.select_for_update().get(pk=assignment.managed_job_id)
    assignment = (
        JobAssignment.objects.select_for_update(of=("self",))
        .select_related("managed_job", "assigned_shop")
        .get(pk=assignment.pk)
    )
    assignment.managed_job = job
    _require_not_finished(job, assignment)

    now = timezone.now()
    group = _latest_group_events(job, actor=actor)
    if not group:
        raise PrinterWorkflowError("Nothing to undo for this job.", status_code=400, code="nothing_to_undo")
    latest = max(group, key=lambda event: (event.occurred_at, event.id))
    if now - latest.occurred_at > UNDO_WINDOW:
        raise PrinterWorkflowError(
            "The undo window (2 minutes) has passed. The stage stays as recorded.",
            status_code=400,
            code="undo_expired",
        )
    metadata = latest.metadata if isinstance(latest.metadata, dict) else {}
    if metadata.get("printer_action") not in {"started", "completed"}:
        raise PrinterWorkflowError("That action cannot be undone.", status_code=400)

    for event in group:
        record_job_event(
            managed_job=job,
            stage=event.stage or _canonical_job_stage(str((event.metadata or {}).get("printer_stage") or "")),
            event=JobEvent.CORRECTION,
            actor=actor,
            actor_role="printer",
            source=JobEvent.SOURCE_PRINTER,
            occurred_at=now,
            metadata={
                "revert": True,
                "printer_undo": True,
                "printer_stage": (event.metadata or {}).get("printer_stage"),
                "printer_action": (event.metadata or {}).get("printer_action"),
                "reverted_event_id": event.id,
                "advance_group_id": (event.metadata or {}).get("advance_group_id"),
                "reason": "mis-tap undo within 2 minutes",
            },
            references=event,
        )

    refreshed = build_printer_progress(assignment, now=now)
    _sync_job_and_assignment(assignment=assignment, job=job, stages=refreshed["stages"])
    # The original timestamps are still in the log; the undo is a new event too.
    record_job_status_event = __import__(
        "jobs.audit_services", fromlist=["record_job_status_event"]
    ).record_job_status_event
    record_job_status_event(
        managed_job=job,
        assignment=assignment,
        actor=actor,
        event_type="printer_stage_undone",
        summary=f"Undid {latest.metadata.get('printer_action_label') or ''}".strip(),
        metadata={"reverted_event_id": latest.id, "advance_group_id": metadata.get("advance_group_id")},
    )
    return assignment, build_printer_progress(assignment, now=timezone.now()), True


__all__ = [
    "BUDGET_MINUTES",
    "PrinterWorkflowError",
    "advance_printer_job",
    "build_printer_progress",
    "stage_plan",
    "undo_printer_advance",
]
