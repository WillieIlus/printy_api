"""Stage-owner notifications for managed jobs (NextStepCard STEP 3)."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from jobs.models import ManagedJob
from notifications.models import Notification
from quotes.models import EmailOutbox


ACTION_REQUIRED_STAGES = {"artwork_approval", "delivery", "ready"}


def _recipient(job: ManagedJob, owner_role: str):
    owner_role = (owner_role or "").strip().lower()
    if owner_role == "buyer":
        return getattr(job, "client", None)
    if owner_role == "manager":
        return getattr(job, "broker", None)
    if owner_role == "printer":
        return getattr(getattr(job, "assigned_shop", None), "owner", None)
    return None


def _idempotency_key(job: ManagedJob, stage: str, owner_role: str, kind: str = "entry") -> str:
    return f"managed-job:{job.id}:stage:{stage}:owner:{owner_role}:{kind}"


def _whatsapp_hook(job: ManagedJob, recipient, owner_role: str, stage: str, kind: str) -> None:
    """Integration point for WhatsApp/SMS. No-op until a provider is wired."""
    return None


def notify_stage_owner(
    *,
    managed_job: ManagedJob,
    stage: str,
    owner_role: str,
    actor=None,
    note: str = "",
) -> dict[str, int]:
    recipient = _recipient(managed_job, owner_role)
    if recipient is None or not getattr(recipient, "email", None):
        return {"notification": 0, "email": 0}
    key = _idempotency_key(managed_job, stage, owner_role, "entry")
    priority = (
        Notification.PRIORITY_ACTION_REQUIRED
        if stage in ACTION_REQUIRED_STAGES or owner_role in {"buyer", "manager", "printer"}
        else Notification.PRIORITY_INFORMATIONAL
    )
    notification_created = 0
    try:
        _, created = Notification.objects.get_or_create(
            user=recipient,
            idempotency_key=key,
            defaults={
                "actor": actor,
                "notification_type": Notification.JOB_STATUS_UPDATED,
                "object_type": "managed_job",
                "object_id": managed_job.id,
                "message": note or f"Job {managed_job.managed_reference} is now in stage '{stage}'.",
                "template_key": "stage_owner_entry",
                "params": {"stage": stage, "owner_role": owner_role, "job_id": managed_job.id},
                "priority": priority,
            },
        )
        notification_created = 1 if created else 0
    except Exception:  # pragma: no cover - unique constraint race
        notification_created = 0

    email_created = 0
    already = EmailOutbox.objects.filter(recipient=recipient, context__idempotency_key=key).exists()
    if not already:
        EmailOutbox.objects.create(
            recipient=recipient,
            recipient_email=recipient.email,
            subject=f"Printy: action needed on {managed_job.managed_reference}",
            body=note or f"Job {managed_job.managed_reference} is now in stage '{stage}'.",
            status=EmailOutbox.Status.PENDING,
            quote_request=getattr(managed_job, "source_quote_request", None),
            context={"idempotency_key": key, "stage": stage, "owner_role": owner_role},
        )
        email_created = 1
    _whatsapp_hook(managed_job, recipient, owner_role, stage, "entry")
    return {"notification": notification_created, "email": email_created}


def _owner_role_for_status(status: str) -> str:
    status = (status or "").strip().lower()
    if status in ("draft", "quoted", "awaiting_payment", "payment_confirmed"):
        return "buyer"
    if status in ("assigned",):
        return "manager"
    if status in ("in_production", "finishing", "ready"):
        return "printer"
    if status in ("delivered",):
        return "buyer"
    return ""


def _owner_role_for(managed_job: ManagedJob) -> str:
    try:
        from jobs.action_resolver import resolve_next_step

        return str(resolve_next_step(managed_job, "manager").get("owner_role") or "")
    except Exception:  # pragma: no cover - resolver must never block reminders
        return _owner_role_for_status(str(getattr(managed_job, "status", "") or ""))


def ensure_stage_reminders(managed_job: ManagedJob, *, now=None) -> dict[str, int]:
    now = now or timezone.now()
    status = str(getattr(managed_job, "status", "") or "")
    owner_role = _owner_role_for(managed_job)
    recipient = _recipient(managed_job, owner_role)
    if recipient is None:
        return {"reminder": 0, "escalation": 0}
    waiting_since = getattr(managed_job, "updated_at", None) or getattr(managed_job, "created_at", None) or now
    age = now - waiting_since
    created = {"reminder": 0, "escalation": 0}
    if age >= timedelta(hours=12):
        key = _idempotency_key(managed_job, status, owner_role, "reminder-12h")
        if not EmailOutbox.objects.filter(recipient=recipient, context__idempotency_key=key).exists():
            EmailOutbox.objects.create(
                recipient=recipient,
                recipient_email=recipient.email,
                subject=f"Reminder: action needed on {managed_job.managed_reference}",
                body=f"Job {managed_job.managed_reference} is still waiting for your action.",
                status=EmailOutbox.Status.PENDING,
                quote_request=getattr(managed_job, "source_quote_request", None),
                context={"idempotency_key": key, "stage": status, "owner_role": owner_role, "kind": "reminder-12h"},
            )
            created["reminder"] = 1
    if age >= timedelta(hours=24):
        manager = getattr(managed_job, "broker", None)
        if manager is not None and manager.id != getattr(recipient, "id", None):
            key = _idempotency_key(managed_job, status, "manager", "escalation-24h")
            if not EmailOutbox.objects.filter(recipient=manager, context__idempotency_key=key).exists():
                EmailOutbox.objects.create(
                    recipient=manager,
                    recipient_email=manager.email,
                    subject=f"Escalation: {managed_job.managed_reference} waiting 24h",
                    body=f"Job {managed_job.managed_reference} has been waiting over 24 hours.",
                    status=EmailOutbox.Status.PENDING,
                    quote_request=getattr(managed_job, "source_quote_request", None),
                    context={"idempotency_key": key, "stage": status, "owner_role": "manager", "kind": "escalation-24h"},
                )
                created["escalation"] = 1
    return created
