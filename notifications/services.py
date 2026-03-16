"""
Lightweight notification creation for quote marketplace events.
"""
from django.utils import timezone

from .models import Notification


def notify(
    recipient,
    notification_type,
    message="",
    object_type="",
    object_id=None,
    actor=None,
):
    """
    Create an in-app notification.

    Args:
        recipient: User to notify (recipient)
        notification_type: One of Notification.TYPE_CHOICES
        message: Human-readable message
        object_type: e.g. "quote_request", "shop_quote", "production_order"
        object_id: PK of the reference object
        actor: User who triggered the event (optional)
    """
    return Notification.objects.create(
        user=recipient,
        actor=actor,
        notification_type=notification_type,
        message=message or "",
        object_type=object_type or "",
        object_id=object_id,
    )
