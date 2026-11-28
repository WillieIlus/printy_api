"""Stage-scoped job messages built on the existing quote conversation store.

There is one message table in Printy: ``QuoteRequestMessage``. This module
links those rows to a ManagedJob and a canonical job stage, and exposes only
the conversation the viewer is allowed to see. It never creates a second
message store.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from jobs.models import ManagedJob
from jobs.timing_config import STAGES_BY_KEY, STAGE_ORDER, stage_for_status
from notifications.models import Notification
from notifications.services import notify
from quotes.messaging import create_quote_message
from quotes.models import QuoteRequestMessage


CLIENT = "client"
MANAGER = "manager"
PRINTER = "printer"
ADMIN = "admin"
AUDIENCE_CLIENT = "client"
AUDIENCE_PRINTER = "printer"
VISIBILITY_CLIENT = QuoteRequestMessage.JobVisibility.CLIENT_MANAGER
VISIBILITY_PRINTER = QuoteRequestMessage.JobVisibility.MANAGER_PRINTER


def viewer_role_for_job(job: ManagedJob, user) -> str | None:
    if not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return ADMIN
    if job.client_id and job.client_id == user.id:
        return CLIENT
    if job.broker_id and job.broker_id == user.id:
        return MANAGER
    shop = getattr(job, "assigned_shop", None)
    if shop is not None and getattr(shop, "owner_id", None) == user.id:
        return PRINTER
    return None


def job_for_user(job_id: int, user) -> ManagedJob:
    queryset = ManagedJob.objects.select_related(
        "client",
        "broker",
        "assigned_shop",
        "assigned_shop__owner",
        "source_quote_request",
        "source_quote",
        "created_by",
    )
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return queryset.get(pk=job_id)
    job = queryset.filter(
        Q(client=user) | Q(broker=user) | Q(assigned_shop__owner=user)
    ).distinct().filter(pk=job_id).first()
    if job is None:
        raise PermissionDenied("You do not have access to this job.")
    return job


def stage_label(stage_key: str) -> str:
    stage = STAGES_BY_KEY.get(stage_key)
    return stage.label if stage else stage_key.replace("_", " ").title()


def stage_owner(stage_key: str) -> str:
    stage = STAGES_BY_KEY.get(stage_key)
    return stage.owner if stage else ""


def manager_for_job(job: ManagedJob):
    if job.broker_id:
        return job.broker
    if job.created_by_id and getattr(job.created_by, "role", "") in {"partner", "admin", "super_admin"}:
        return job.created_by
    return None


def printer_for_job(job: ManagedJob):
    return getattr(getattr(job, "assigned_shop", None), "owner", None)


def audience_for_viewer(job: ManagedJob, role: str, requested: str = "") -> str:
    if role == CLIENT:
        return AUDIENCE_CLIENT
    if role == PRINTER:
        return AUDIENCE_PRINTER
    if requested in {AUDIENCE_CLIENT, AUDIENCE_PRINTER}:
        return requested
    return AUDIENCE_CLIENT


def visibility_for_audience(audience: str) -> str:
    return VISIBILITY_PRINTER if audience == AUDIENCE_PRINTER else VISIBILITY_CLIENT


def visible_messages(job: ManagedJob, role: str, audience: str):
    queryset = QuoteRequestMessage.objects.filter(
        managed_job=job,
        visibility=visibility_for_audience(audience),
    ).select_related("sender", "recipient", "quote", "quote_request").order_by("created_at", "id")
    if role == CLIENT:
        return queryset.filter(sender_role__in=[QuoteRequestMessage.SenderRole.CLIENT, QuoteRequestMessage.SenderRole.MANAGER])
    if role == PRINTER:
        return queryset.filter(sender_role__in=[QuoteRequestMessage.SenderRole.MANAGER, QuoteRequestMessage.SenderRole.PRINTER, QuoteRequestMessage.SenderRole.SYSTEM])
    return queryset


def message_recipient_for(job: ManagedJob, role: str, audience: str):
    if role == CLIENT:
        manager = manager_for_job(job)
        if manager is None:
            raise ValidationError("This job has no manager assigned yet.")
        return manager, QuoteRequestMessage.RecipientRole.MANAGER
    if role == PRINTER:
        manager = manager_for_job(job)
        if manager is None:
            raise ValidationError("This job has no manager assigned yet.")
        return manager, QuoteRequestMessage.RecipientRole.MANAGER
    if audience == AUDIENCE_PRINTER:
        printer = printer_for_job(job)
        if printer is None:
            raise ValidationError("This job has no printer assigned yet.")
        return printer, QuoteRequestMessage.RecipientRole.PRINTER
    client = getattr(job, "client", None)
    if client is None:
        raise ValidationError("This job has no client attached.")
    return client, QuoteRequestMessage.RecipientRole.CLIENT


def sender_role_for(role: str) -> str:
    if role == CLIENT:
        return QuoteRequestMessage.SenderRole.CLIENT
    if role == PRINTER:
        return QuoteRequestMessage.SenderRole.PRINTER
    return QuoteRequestMessage.SenderRole.MANAGER


def create_job_message(
    *,
    job: ManagedJob,
    user,
    body: str,
    stage_key: str = "",
    requested_audience: str = "",
):
    role = viewer_role_for_job(job, user)
    if role is None:
        raise PermissionDenied("You do not have access to this job.")
    stage_key = (stage_key or "").strip() or stage_for_status(job.status)
    if stage_key not in STAGE_ORDER:
        raise ValidationError("Choose one of this job's real workflow stages.")
    if role == ADMIN:
        role = MANAGER
    if role == CLIENT and requested_audience == AUDIENCE_PRINTER:
        raise PermissionDenied("Clients can only message their manager.")
    if role == PRINTER and requested_audience == AUDIENCE_CLIENT:
        raise PermissionDenied("Printers can only message the manager.")
    audience = audience_for_viewer(job, role, requested_audience)
    if role == CLIENT and audience != AUDIENCE_CLIENT:
        raise PermissionDenied("Clients can only message their manager.")
    if role == PRINTER and audience != AUDIENCE_PRINTER:
        raise PermissionDenied("Printers can only message the manager.")
    recipient, recipient_role = message_recipient_for(job, role, audience)
    quote_request = getattr(job, "source_quote_request", None)
    if quote_request is None:
        raise ValidationError("This job has no source quote request to attach messages to.")

    conversation_type = (
        QuoteRequestMessage.ConversationType.CLIENT_QUESTION
        if role == CLIENT
        else QuoteRequestMessage.ConversationType.SHOP_REPLY
        if audience == AUDIENCE_CLIENT
        else QuoteRequestMessage.ConversationType.SYSTEM_UPDATE
    )
    message = create_quote_message(
        quote_request=quote_request,
        quote=getattr(job, "source_quote", None),
        managed_job=job,
        stage=stage_key,
        visibility=visibility_for_audience(audience),
        shop=getattr(job, "assigned_shop", None),
        sender=user,
        recipient=recipient,
        sender_role=sender_role_for(role),
        recipient_role=recipient_role,
        message_kind=QuoteRequestMessage.MessageKind.REPLY,
        message_type=QuoteRequestMessage.MessageType.QUOTE_CONVERSATION,
        direction=QuoteRequestMessage.Direction.INBOUND,
        subject=f"Message on {job.managed_reference or 'job'}",
        body=body.strip(),
        metadata={
            "managed_job_id": job.id,
            "job_reference": job.managed_reference,
            "stage": stage_key,
            "audience": audience,
        },
    )
    conversation_type_value = conversation_type
    message.conversation_type = conversation_type_value
    message.save(update_fields=["conversation_type", "updated_at"])
    if recipient is not None and getattr(recipient, "id", None) != getattr(user, "id", None):
        notify(
            recipient=recipient,
            notification_type=Notification.JOB_MESSAGE_RECEIVED,
            message=f"New message on {job.managed_reference or 'your job'}.",
            object_type="managed_job",
            object_id=job.id,
            actor=user,
            params={
                "job_reference": job.managed_reference,
                "stage": stage_key,
                "stage_label": stage_label(stage_key),
                "sender_role": role,
            },
            dedupe=False,
        )
    return message


def sender_label(message: QuoteRequestMessage, viewer_role: str, user) -> str:
    if message.sender_id and message.sender_id == getattr(user, "id", None):
        return "You"
    if viewer_role == CLIENT:
        if message.sender_role == QuoteRequestMessage.SenderRole.MANAGER:
            return getattr(message.sender, "name", "") or "Your manager"
        if message.sender_role == QuoteRequestMessage.SenderRole.SYSTEM:
            return "Printy"
        return "You"
    if viewer_role == PRINTER:
        if message.sender_role == QuoteRequestMessage.SenderRole.MANAGER:
            return getattr(message.sender, "name", "") or "Manager"
        if message.sender_role == QuoteRequestMessage.SenderRole.SYSTEM:
            return "Printy"
        return "You"
    return (
        getattr(message.sender, "name", "")
        or getattr(message.sender, "email", "")
        or ("Printy" if message.sender_role == QuoteRequestMessage.SenderRole.SYSTEM else "Participant")
    )


def _file_payload(files, request):
    rows = []
    for item in files:
        try:
            url = request.build_absolute_uri(item.file.url) if request else item.file.url
        except Exception:
            url = ""
        rows.append(
            {
                "id": item.id,
                "name": item.name or str(item.file.name).rsplit("/", 1)[-1],
                "url": url,
            }
        )
    return rows


def job_files_for(job: ManagedJob, role: str, request=None):
    quote_request = getattr(job, "source_quote_request", None)
    quote = getattr(job, "source_quote", None)
    files = []
    if quote_request is not None:
        files.extend(quote_request.attachments.all())
    if quote is not None:
        files.extend(quote.attachments.all())
    if role == CLIENT:
        return _file_payload(files, request)
    return _file_payload(files, request)


def can_reply_to(job: ManagedJob, role: str, audience: str) -> bool:
    try:
        message_recipient_for(job, role, audience)
        return True
    except ValidationError:
        return False


def stage_message_payload(job: ManagedJob, user, role: str, audience: str, request=None):
    messages = list(visible_messages(job, role, audience))
    unread = 0
    for message in messages:
        if message.recipient_id == getattr(user, "id", None) and message.read_at is None:
            unread += 1
    rows = [
        {
            "id": message.id,
            "body": message.body,
            "stage": message.stage,
            "sender_role": message.sender_role,
            "sender_name": sender_label(message, role, user),
            "is_mine": message.sender_id == getattr(user, "id", None),
            "audience": audience,
            "created_at": message.created_at,
            "read_at": message.read_at,
            "attachments": (message.metadata or {}).get("attachments", []),
        }
        for message in messages
    ]
    latest = rows[-1] if rows else None
    return {
        "audience": audience,
        "visibility": visibility_for_audience(audience),
        "messages": rows,
        "unread_count": unread,
        "latest": latest,
        "files": job_files_for(job, role, request=request),
    }


def job_message_bundle(job: ManagedJob, user, request=None):
    role = viewer_role_for_job(job, user)
    if role is None:
        raise PermissionDenied("You do not have access to this job.")
    if role == ADMIN:
        role = MANAGER
    audiences = [AUDIENCE_CLIENT]
    if role == MANAGER:
        audiences = [AUDIENCE_CLIENT, AUDIENCE_PRINTER]
    elif role == PRINTER:
        audiences = [AUDIENCE_PRINTER]

    audience_payloads = [
        stage_message_payload(job, user, role, audience, request=request)
        for audience in audiences
    ]
    by_stage = {}
    for stage_key in STAGE_ORDER:
        stage_messages = []
        stage_unread = 0
        for payload in audience_payloads:
            for message in payload["messages"]:
                if message["stage"] == stage_key:
                    stage_messages.append(message)
                    if message["read_at"] is None and not message["is_mine"]:
                        stage_unread += 1
        by_stage[stage_key] = {
            "key": stage_key,
            "label": stage_label(stage_key),
            "owner": stage_owner(stage_key),
            "messages": sorted(stage_messages, key=lambda row: row["created_at"]),
            "unread_count": stage_unread,
        }
    latest_candidates = [message for payload in audience_payloads for message in payload["messages"]]
    latest = max(latest_candidates, key=lambda row: row["created_at"], default=None)
    return {
        "job_id": job.id,
        "job_reference": job.managed_reference,
        "current_stage": stage_for_status(job.status),
        "viewer_role": role,
        "audiences": [
            {
                "key": payload["audience"],
                "label": "Client" if payload["audience"] == AUDIENCE_CLIENT else "Printer",
                "unread_count": payload["unread_count"],
                "can_reply": can_reply_to(job, role, payload["audience"]),
            }
            for payload in audience_payloads
        ],
        "stages": [by_stage[key] for key in STAGE_ORDER],
        "total_unread": sum(payload["unread_count"] for payload in audience_payloads),
        "latest": latest,
        "files": job_files_for(job, role, request=request),
        "can_reply": True,
    }


def mark_message_read(message: QuoteRequestMessage, user):
    if message.recipient_id and message.recipient_id == getattr(user, "id", None) and message.read_at is None:
        from django.utils import timezone

        message.read_at = timezone.now()
        message.save(update_fields=["read_at", "updated_at"])
    return message

