"""The one role-aware job card projection (law 23-31).

The card is an envelope: top-level ``id`` plus a ``jobs`` list. Today the list
has exactly one job; the envelope shape is deliberate so a future grouped card
does not require a new API shape.

Nothing in this module mutates. It reads the existing resolver, timing, event,
message, file and payment rows and projects them for one viewer role.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from api.visibility import (
    CLIENT_ACTOR,
    OPS_ACTOR,
    PARTNER_ACTOR,
    SHOP_ACTOR,
)
from jobs.action_resolver import resolve_next_step
from jobs.choices import JobFileStatus, JobFileType
from jobs.file_services import get_visible_job_files_for_actor
from jobs.job_codes import job_code
from jobs.messaging import (
    ADMIN,
    AUDIENCE_CLIENT,
    AUDIENCE_PRINTER,
    CLIENT,
    MANAGER,
    PRINTER,
    sender_label,
    visible_messages,
)
from jobs.stage_mapping import canonical_stage_for_job, STAGE_LABELS
from jobs.timing import timing_fields
from services.pricing.role_projection import (
    ROLE_BUYER,
    ROLE_MANAGER,
    ROLE_PRINTER,
    project_canonical,
)

VIEWER_TO_ACTOR = {
    "buyer": CLIENT_ACTOR,
    "manager": PARTNER_ACTOR,
    "printer": SHOP_ACTOR,
    "admin": OPS_ACTOR,
}

VIEWER_TO_PROJECTION = {
    "buyer": ROLE_BUYER,
    "manager": ROLE_MANAGER,
    "printer": ROLE_PRINTER,
    "admin": ROLE_MANAGER,
}

VIEWER_TO_MESSAGE_ROLE = {
    "buyer": CLIENT,
    "manager": MANAGER,
    "printer": PRINTER,
    "admin": MANAGER,
}

FILE_TYPE_LABELS = {
    JobFileType.ARTWORK: "Artwork uploaded",
    JobFileType.CUSTOMER_UPLOAD: "Artwork uploaded",
    JobFileType.BROKER_REVISION: "Artwork updated",
    JobFileType.PROOF: "Proof uploaded",
    JobFileType.PRINT_READY: "Print file ready",
    JobFileType.DELIVERY_EVIDENCE: "Delivery evidence shared",
}

FILE_STATUS_LABELS = {
    JobFileStatus.UPLOADED: "Uploaded",
    JobFileStatus.MANAGER_REVIEW: "With your manager",
    JobFileStatus.MANAGER_APPROVED: "Approved",
    JobFileStatus.MANAGER_REJECTED: "Changes requested",
    JobFileStatus.PROOF_UPLOADED: "Proof uploaded",
    JobFileStatus.PROOF_APPROVED: "Proof approved",
    JobFileStatus.PROOF_REJECTED: "Proof rejected",
    JobFileStatus.REVISION_REQUESTED: "Revision requested",
    JobFileStatus.PRINT_READY: "Ready to print",
    JobFileStatus.UNDER_REVIEW: "Under review",
    JobFileStatus.APPROVED: "Approved",
    JobFileStatus.REJECTED: "Rejected",
    JobFileStatus.REPLACED: "Replaced",
}

PAYMENT_LABELS = {
    "pending": "Payment pending",
    "processing": "Payment processing",
    "paid": "Payment confirmed",
    "failed": "Payment failed",
    "cancelled": "Payment cancelled",
    "expired": "Payment expired",
}

# Only these JobStatusEvent rows become thread notes. Financial and internal
# ops events are deliberately excluded from buyer/printer threads.
SAFE_STATUS_EVENTS = {
    "quote_accepted": "Quote accepted",
    "managed_job_created": "Job created",
    "assignment_created": "Production arranged",
    "assignment_status_changed": "Production update",
    "artwork_confirmation_requested": "Artwork confirmation requested",
    "artwork_confirmation_approved": "Artwork confirmed",
    "artwork_confirmation_rejected": "Artwork changes requested",
    "proof_approved": "Proof approved",
    "proof_rejected": "Proof rejected",
    "revision_requested": "Revision requested",
    "job_delivered": "Delivered",
    "job_completed": "Completed",
    "issue_raised": "Issue raised",
    "next_action_performed": "Action recorded",
    "dispute_resolved": "Dispute resolved",
}
MANAGER_STATUS_EVENTS = SAFE_STATUS_EVENTS | {
    "payment_confirmed": "Payment confirmed",
    "settlement_release_ready": "Payout ready",
    "payout_released_manual": "Payout released",
    "lateness_hold": "Payout held for review",
    "lateness_hold_cleared": "Payout hold cleared",
}
ADMIN_STATUS_EVENTS = MANAGER_STATUS_EVENTS | {
    "printer_stage_advanced": "Printer stage advanced",
    "printer_stage_undone": "Printer stage undone",
    "file_uploaded": "File uploaded",
    "file_replaced": "File replaced",
}
PRINTER_STATUS_EVENTS = {
    "assignment_status_changed": "Production update",
    "artwork_confirmation_requested": "Artwork confirmation requested",
    "artwork_confirmation_approved": "Artwork confirmed",
    "artwork_confirmation_rejected": "Artwork changes requested",
    "proof_approved": "Proof approved",
    "proof_rejected": "Proof rejected",
    "revision_requested": "Revision requested",
    "job_delivered": "Delivered",
    "job_completed": "Completed",
    "issue_raised": "Issue raised",
    "next_action_performed": "Action recorded",
    "dispute_resolved": "Dispute resolved",
}

SECONDARY_ACTIONS = {
    "buyer": [
        {"code": "message", "label": "Message"},
        {"code": "history", "label": "History"},
    ],
    "manager": [
        {"code": "hold", "label": "Hold"},
        {"code": "adjust", "label": "Adjust"},
        {"code": "message", "label": "Message"},
        {"code": "history", "label": "History"},
    ],
    "printer": [
        {"code": "message", "label": "Message"},
        {"code": "history", "label": "History"},
    ],
    "admin": [
        {"code": "hold", "label": "Hold"},
        {"code": "adjust", "label": "Adjust"},
        {"code": "message", "label": "Message"},
        {"code": "history", "label": "History"},
    ],
}


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _role_of_user(user) -> str:
    if user is None:
        return "system"
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return "admin"
    role = str(getattr(user, "role", "") or "").lower()
    if role in {"client", "buyer", "customer"}:
        return "buyer"
    if role in {"partner", "broker", "manager"}:
        return "manager"
    if role in {"production", "shop", "shop_owner", "printer"}:
        return "printer"
    return "system"


def _project_actor_role(role: str, viewer_role: str) -> str:
    role = str(role or "").lower()
    if viewer_role == "buyer" and role in {"printer", "shop", "production"}:
        # The buyer's contract is with the manager, never the production shop.
        return "manager"
    if viewer_role == "printer" and role in {"buyer", "client", "customer"}:
        # The printer never receives the client's identity; the role label is
        # enough for the thread.
        return "client"
    return role


def _actor_label(role: str) -> str:
    return {
        "buyer": "Client",
        "manager": "Manager",
        "printer": "Printer",
        "admin": "Printy",
        "system": "Printy",
    }.get(role, "Printy")


def _safe_actor_name(*, role: str, actor, viewer_role: str, user) -> str:
    if actor is not None and getattr(actor, "id", None) == getattr(user, "id", None):
        return "You"
    if viewer_role == "buyer":
        if role == "manager":
            return getattr(actor, "name", "") or "Your manager"
        return "Your manager"
    if viewer_role == "printer":
        if role == "manager":
            return getattr(actor, "name", "") or "Manager"
        return "Client" if role == "client" else "Printy"
    if viewer_role == "manager":
        if role == "buyer":
            return getattr(actor, "name", "") or getattr(actor, "email", "") or "Client"
        if role == "printer":
            return getattr(actor, "name", "") or "Printer"
        return getattr(actor, "name", "") or "Printy"
    return (
        getattr(actor, "name", "")
        or getattr(actor, "email", "")
        or _actor_label(role)
    )


def _event_title(event) -> str:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    label = str(metadata.get("printer_stage_label") or metadata.get("printer_action_label") or "").strip()
    event_name = str(getattr(event, "event", "") or "")
    if event_name == "stage_started":
        return f"{label} started" if label else "Stage started"
    if event_name == "stage_completed":
        return label or "Stage completed"
    if event_name == "handoff":
        return "Handed off"
    if event_name == "delay":
        return "Stage delay recorded"
    if event_name == "date_revision":
        return "Due date updated"
    if event_name == "correction":
        return "Stage correction recorded"
    return "Job updated"


def _thread_stage(value: str | None, job) -> str:
    key = str(value or "")
    if key in STAGE_LABELS:
        return key
    return canonical_stage_for_job(job)


def _thread_item(*, item_id: str, item_type: str, at, viewer_role: str, actor_role: str, actor_name: str, title: str, body: str = "", stage: str | None = None, metadata: dict | None = None) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": item_type,
        "at": at,
        "actor": {"role": actor_role, "name": actor_name},
        "title": title,
        "body": body,
        "stage": _thread_stage(stage, None) if stage in STAGE_LABELS else None,
        "metadata": metadata or {},
        "_sort_at": at,
        "_sort_id": item_id,
    }


def _stage_events(job, viewer_role: str, user) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in job.job_events.all():
        actor_role = _project_actor_role(getattr(event, "actor_role", "") or "system", viewer_role)
        actor_name = _safe_actor_name(
            role=actor_role,
            actor=getattr(event, "actor", None),
            viewer_role=viewer_role,
            user=user,
        )
        metadata = (
            {
                "event": event.event,
                "printer_stage": (event.metadata or {}).get("printer_stage"),
            }
            if viewer_role == "admin"
            else {}
        )
        items.append(
            _thread_item(
                item_id=f"stage-{event.id}",
                item_type="stage",
                at=event.occurred_at,
                viewer_role=viewer_role,
                actor_role=actor_role,
                actor_name=actor_name,
                title=_event_title(event),
                stage=event.stage,
                metadata=metadata,
            )
        )
    return items


def _messages(job, viewer_role: str, user, request=None) -> list[dict[str, Any]]:
    message_role = VIEWER_TO_MESSAGE_ROLE[viewer_role]
    if viewer_role in {"buyer"}:
        audiences = [AUDIENCE_CLIENT]
    elif viewer_role == "printer":
        audiences = [AUDIENCE_PRINTER]
    else:
        audiences = [AUDIENCE_CLIENT, AUDIENCE_PRINTER]
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for audience in audiences:
        for message in visible_messages(job, message_role, audience):
            if message.id in seen:
                continue
            seen.add(message.id)
            sender_role = _project_actor_role(
                {
                    "client": "buyer",
                    "manager": "manager",
                    "printer": "printer",
                    "system": "system",
                }.get(str(message.sender_role or ""), "system"),
                viewer_role,
            )
            items.append(
                _thread_item(
                    item_id=f"message-{message.id}",
                    item_type="message",
                    at=message.created_at,
                    viewer_role=viewer_role,
                    actor_role=sender_role,
                    actor_name=sender_label(message, message_role, user),
                    title="Message",
                    body=message.body,
                    stage=message.stage,
                    metadata={"audience": audience} if viewer_role == "admin" else {},
                )
            )
    return items


def _files(job, viewer_role: str, user) -> list[dict[str, Any]]:
    actor = VIEWER_TO_ACTOR[viewer_role]
    items: list[dict[str, Any]] = []
    for job_file in get_visible_job_files_for_actor(managed_job=job, actor=actor):
        file_type = str(job_file.file_type or "")
        uploader_role = _project_actor_role(_role_of_user(getattr(job_file, "uploaded_by", None)), viewer_role)
        uploader_name = _safe_actor_name(
            role=uploader_role,
            actor=getattr(job_file, "uploaded_by", None),
            viewer_role=viewer_role,
            user=user,
        )
        if viewer_role == "printer":
            # File names can contain the client's name. The printer gets a
            # generic label, never the original filename.
            filename = "Artwork file" if file_type in {
                JobFileType.ARTWORK,
                JobFileType.CUSTOMER_UPLOAD,
                JobFileType.BROKER_REVISION,
            } else "Print file"
        else:
            filename = job_file.original_filename or "Job file"
        items.append(
            _thread_item(
                item_id=f"file-{job_file.id}",
                item_type="artwork" if file_type in {
                    JobFileType.ARTWORK,
                    JobFileType.CUSTOMER_UPLOAD,
                    JobFileType.BROKER_REVISION,
                    JobFileType.PROOF,
                } else "file",
                at=job_file.created_at,
                viewer_role=viewer_role,
                actor_role=uploader_role,
                actor_name=uploader_name,
                title=FILE_TYPE_LABELS.get(file_type, "File shared"),
                body=filename,
                metadata={
                    "status": FILE_STATUS_LABELS.get(str(job_file.status or ""), "Shared"),
                },
            )
        )
    return items


def _payments(job, viewer_role: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    include_amount = viewer_role in {"buyer", "manager", "admin"}
    for payment in job.canonical_payments.all():
        status = str(payment.status or "").lower()
        metadata: dict[str, Any] = {}
        if include_amount:
            metadata = {
                "amount": str(payment.amount),
                "currency": payment.currency,
            }
        items.append(
            _thread_item(
                item_id=f"payment-{payment.id}",
                item_type="payment",
                at=payment.confirmed_at or payment.created_at,
                viewer_role=viewer_role,
                actor_role="system",
                actor_name="Printy",
                title=PAYMENT_LABELS.get(status, "Payment update"),
                metadata=metadata,
            )
        )
    return items


def _status_notes(job, viewer_role: str, user) -> list[dict[str, Any]]:
    if viewer_role == "buyer":
        allowed = SAFE_STATUS_EVENTS
    elif viewer_role == "manager":
        allowed = MANAGER_STATUS_EVENTS
    elif viewer_role == "printer":
        allowed = PRINTER_STATUS_EVENTS
    else:
        allowed = ADMIN_STATUS_EVENTS

    items: list[dict[str, Any]] = []
    for event in job.events.all():
        event_type = str(event.event_type or "")
        title = allowed.get(event_type)
        if not title:
            continue
        actor_role = _project_actor_role(_role_of_user(getattr(event, "actor", None)), viewer_role)
        actor_name = _safe_actor_name(
            role=actor_role,
            actor=getattr(event, "actor", None),
            viewer_role=viewer_role,
            user=user,
        )
        items.append(
            _thread_item(
                item_id=f"note-{event.id}",
                item_type="note",
                at=event.created_at,
                viewer_role=viewer_role,
                actor_role=actor_role,
                actor_name=actor_name,
                title=title,
                body=event.summary if viewer_role in {"manager", "admin"} else "",
                metadata={"event_type": event_type} if viewer_role == "admin" else {},
            )
        )
    return items


def build_thread(job, *, viewer_role: str, user) -> list[dict[str, Any]]:
    items = []
    items.extend(_stage_events(job, viewer_role, user))
    items.extend(_messages(job, viewer_role, user))
    items.extend(_files(job, viewer_role, user))
    items.extend(_payments(job, viewer_role))
    items.extend(_status_notes(job, viewer_role, user))
    items.sort(key=lambda item: (item.get("_sort_at") or timezone.now(), item.get("_sort_id") or ""))
    for item in items:
        item.pop("_sort_at", None)
        item.pop("_sort_id", None)
        item["at"] = _iso(item.get("at"))
    return items


def _due_block(job, viewer_role: str) -> dict[str, Any]:
    timing = timing_fields(job)
    projected = project_canonical(timing, VIEWER_TO_PROJECTION[viewer_role])
    # The card already has one stage, owner and next_action. Do not repeat the
    # legacy status-based versions inside the due block.
    for key in ("current_stage", "stage_label", "stage_owner", "next_action"):
        projected.pop(key, None)
    return projected


def _manager_payload(job) -> dict[str, Any] | None:
    manager = getattr(job, "broker", None)
    if manager is None:
        return None
    return {"id": manager.id, "name": getattr(manager, "name", "") or "Manager"}


def _client_payload(job) -> dict[str, Any] | None:
    client = getattr(job, "client", None)
    if client is None:
        return None
    return {
        "id": client.id,
        "name": getattr(client, "name", "") or getattr(client, "email", "") or "Client",
    }


def _shop_payload(job) -> dict[str, Any] | None:
    shop = getattr(job, "assigned_shop", None)
    if shop is None:
        return None
    return {"id": shop.id, "name": getattr(shop, "name", "") or "Production shop"}


def _intake_path(job) -> str:
    """Derive manager-sourced vs direct-shop from the existing intake marker."""
    quote = getattr(job, "source_quote", None)
    if quote is not None:
        try:
            from jobs.services.managed_job_creation import _is_direct_shop_intake

            if _is_direct_shop_intake(quote):
                return "direct_shop"
        except Exception:  # pragma: no cover - projection must never fail
            pass
    return "manager" if getattr(job, "broker_id", None) else "direct_shop"


def _owner_label(step: dict[str, Any], viewer_role: str) -> str:
    if step.get("completed"):
        return "No action needed"
    owner = str(step.get("owner_role") or "none")
    if viewer_role == "admin":
        return "No action needed" if owner == "none" else f"Owner: {_actor_label(owner)}"
    label = str(step.get("waiting_label") or "").strip()
    if label:
        return label
    if owner == "none":
        return "No action needed"
    return f"Waiting on the {_actor_label(owner).lower()}"


def build_job_card(job, *, viewer_role: str, user) -> dict[str, Any]:
    """Build the role-aware card envelope for one managed job."""
    resolver_role = "manager" if viewer_role == "admin" else viewer_role
    step = resolve_next_step(job, resolver_role)
    stage_key = str(step.get("canonical_stage") or canonical_stage_for_job(job))
    stage_key = stage_key if stage_key in STAGE_LABELS else "quote_accepted"
    action = step.get("next_action") or {}
    action_code = str(action.get("key") or "none")
    next_action = {
        "code": action_code,
        "label": str(action.get("label") or "No action needed"),
        "actor": str(action.get("actor") or step.get("owner_role") or "none"),
        "params": action.get("params") or {"job_id": job.id},
    }

    item: dict[str, Any] = {
        "id": job.id,
        "code": job_code(job),
        "title": job.title,
        "stage": {"key": stage_key, "label": STAGE_LABELS[stage_key]},
        "owner": {
            "role": str(step.get("owner_role") or "none"),
            "label": _owner_label(step, viewer_role),
        },
        "next_action": next_action,
        "due": _due_block(job, viewer_role),
        "thread": build_thread(job, viewer_role=viewer_role, user=user),
        "secondary_actions": SECONDARY_ACTIONS[viewer_role],
        "intake_path": _intake_path(job),
    }

    if viewer_role in {"manager", "admin"}:
        shop = _shop_payload(job)
        if shop is not None:
            item["shop"] = shop
        client = _client_payload(job)
        if client is not None:
            item["client"] = client
    if viewer_role in {"buyer", "printer", "admin"}:
        manager = _manager_payload(job)
        if manager is not None:
            item["manager"] = manager
    if viewer_role == "admin":
        item["internal_status"] = str(getattr(job, "status", "") or "")
        item["internal_status_label"] = getattr(
            job, "get_status_display", lambda: getattr(job, "status", "")
        )()

    return {
        "id": f"job-card-{job.id}",
        "viewer": {"role": viewer_role},
        "jobs": [item],
    }


__all__ = ["build_job_card", "build_thread", "SECONDARY_ACTIONS"]
