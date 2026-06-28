"""
Lightweight notification creation for quote marketplace events.
"""
from django.conf import settings
from django.core.mail import send_mail

from .models import Notification


def notify(
    recipient,
    notification_type,
    message="",
    object_type="",
    object_id=None,
    actor=None,
    send_email_notification=False,
    email_subject="",
    email_message="",
):
    """
    Create an in-app notification.

    Args:
        recipient: User to notify (recipient)
        notification_type: One of Notification.TYPE_CHOICES
        message: Human-readable message
        object_type: e.g. "quote_request", "quote", "production_order"
        object_id: PK of the reference object
        actor: User who triggered the event (optional)
    """
    notification = Notification.objects.create(
        user=recipient,
        actor=actor,
        notification_type=notification_type,
        message=message or "",
        object_type=object_type or "",
        object_id=object_id,
    )
    if send_email_notification and getattr(recipient, "email", ""):
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
):
    return notify(
        recipient=recipient,
        notification_type=notification_type,
        message=message,
        object_type=object_type,
        object_id=object_id,
        actor=actor,
        send_email_notification=False,
    )
