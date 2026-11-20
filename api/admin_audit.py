"""Admin audit helper built on Django's existing LogEntry (no new model)."""

from __future__ import annotations

import json

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType


def record_admin_audit(*, user, obj, action: str, reason: str = "", metadata: dict | None = None) -> LogEntry:
    """Write one admin decision to the standard Django admin audit log."""
    content_type = ContentType.objects.get_for_model(obj.__class__)
    payload = {"action": action, "reason": reason, "actor_id": getattr(user, "id", None)}
    if metadata:
        payload.update(metadata)
    return LogEntry.objects.create(
        user=user,
        content_type=content_type,
        object_id=str(getattr(obj, "pk", "")),
        object_repr=str(obj)[:200],
        action_flag=CHANGE,
        change_message=json.dumps(payload),
    )


def last_admin_audit(*, obj) -> dict | None:
    """Return the latest admin decision metadata for an object, if any."""
    content_type = ContentType.objects.get_for_model(obj.__class__)
    entry = (
        LogEntry.objects.filter(content_type=content_type, object_id=str(obj.pk))
        .order_by("-action_time", "-id")
        .first()
    )
    if entry is None:
        return None
    try:
        payload = json.loads(entry.change_message or "{}")
    except (TypeError, ValueError):
        payload = {"message": entry.change_message}
    payload["at"] = entry.action_time.isoformat() if entry.action_time else None
    return payload


__all__ = ["record_admin_audit", "last_admin_audit"]
