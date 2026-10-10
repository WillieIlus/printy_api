"""Structured notification creation for quote marketplace events.

``notify`` writes a structured record (``template_key`` + ``params`` + priority)
rather than a pre-rendered sentence. Text is rendered per role/language at read
time (see ``notifications.rendering``), and state-transition events are deduped
on ``(event_type, entity_id, recipient)`` so the same transition cannot fan out
duplicate notifications.
"""
from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail

from .models import Notification

# Events that represent a one-time state transition and should be deduped on
# (event_type, entity, recipient). Conversation-style events are excluded so a
# shop can ask several questions on the same request.
NON_DEDUPED_TYPES = {
    Notification.SHOP_QUESTION_ASKED,
    Notification.BUYER_CLARIFICATION_SENT,
    Notification.JOB_STATUS_UPDATED,
}

ACTION_REQUIRED_TYPES = {
    Notification.QUOTE_REQUEST_SUBMITTED,
    Notification.SHOP_QUOTE_SENT,
    Notification.SHOP_QUOTE_REVISED,
    Notification.SHOP_QUESTION_ASKED,
    Notification.BUYER_CLARIFICATION_SENT,
    Notification.SHOP_QUOTE_ACCEPTED,
    Notification.ARTWORK_REQUIRED,
    Notification.JOB_READY_TO_START,
}


def resolve_priority(notification_type, priority=None):
    if priority:
        return priority
    if notification_type in ACTION_REQUIRED_TYPES:
        return Notification.PRIORITY_ACTION_REQUIRED
    return Notification.PRIORITY_INFORMATIONAL


def build_idempotency_key(*, template_key, object_type, object_id, recipient_id):
    if object_id is None:
        return ""
    return f"{template_key}:{object_type}:{object_id}:{recipient_id}"


def notify(
    recipient,
    notification_type,
    message="",
    object_type="",
    object_id=None,
    actor=None,
    template_key=None,
    params=None,
    priority=None,
    idempotency_key=None,
    dedupe=True,
    send_email_notification=False,
    email_subject="",
    email_message="",
):
    """Create (or return) an in-app notification.

    Args:
        recipient: User to notify.
        notification_type: One of Notification.TYPE_CHOICES.
        message: Legacy fallback body; rendered only when no template applies.
        object_type: e.g. "quote_request", "quote", "managed_job".
        object_id: PK of the referenced entity.
        actor: User who triggered the event (optional).
        template_key: Rendering key; defaults to ``notification_type``.
        params: Structured values interpolated at read time.
        priority: ``action_required`` | ``informational`` (defaults by type).
        idempotency_key: Explicit dedupe key; auto-derived when omitted.
        dedupe: Set False for events that legitimately repeat.
    """
    template_key = template_key or notification_type
    priority = resolve_priority(notification_type, priority)

    key = idempotency_key
    if key is None and dedupe and notification_type not in NON_DEDUPED_TYPES:
        key = build_idempotency_key(
            template_key=template_key,
            object_type=object_type or "",
            object_id=object_id,
            recipient_id=getattr(recipient, "id", None),
        )

    defaults = {
        "notification_type": notification_type,
        "actor": actor,
        "message": message or "",
        "object_type": object_type or "",
        "object_id": object_id,
        "template_key": template_key,
        "params": params or {},
        "priority": priority,
    }

    if key:
        notification, created = Notification.objects.get_or_create(
            user=recipient,
            idempotency_key=key,
            defaults=defaults,
        )
    else:
        notification = Notification.objects.create(
            user=recipient,
            idempotency_key="",
            **defaults,
        )
        created = True

    if created and send_email_notification and getattr(recipient, "email", ""):
        send_mail(
            subject=email_subject or "Printy update",
            message=email_message or message or "",
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[recipient.email],
            fail_silently=True,
        )
    return notification


def notify_quote_event(
    *,
    recipient,
    notification_type,
    message,
    object_type,
    object_id,
    actor=None,
    template_key=None,
    params=None,
    priority=None,
    idempotency_key=None,
    dedupe=True,
):
    return notify(
        recipient=recipient,
        notification_type=notification_type,
        message=message,
        object_type=object_type,
        object_id=object_id,
        actor=actor,
        template_key=template_key,
        params=params,
        priority=priority,
        idempotency_key=idempotency_key,
        dedupe=dedupe,
        send_email_notification=False,
    )
