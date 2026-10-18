"""Per-viewer read state for quotes and jobs (law 7.11).

Read state is stored per (viewer, item type, item id) and exposed as an
``unread`` boolean plus the ``viewer_read_at`` timestamp on the list and detail
payloads. Setting read state never touches the item's status, section or
attention/muted flags.
"""

from __future__ import annotations

from typing import Any, Iterable

from django.utils import timezone

from .models import ViewerItemReadState

QUOTE_REQUEST = ViewerItemReadState.ITEM_QUOTE_REQUEST
JOB = ViewerItemReadState.ITEM_JOB


def _is_viewer(viewer: Any) -> bool:
    return bool(viewer) and getattr(viewer, "is_authenticated", False)


def mark_read(*, viewer: Any, item_type: str, item_id: int, at=None):
    """Mark an item read for a viewer and return the read timestamp.

    Idempotent: reopening an already-read item does not move the timestamp.
    """
    if not _is_viewer(viewer) or not item_id:
        return None
    existing = ViewerItemReadState.objects.filter(
        viewer=viewer, item_type=item_type, item_id=item_id
    ).first()
    if existing and existing.read_at:
        return existing.read_at
    timestamp = at or timezone.now()
    ViewerItemReadState.objects.update_or_create(
        viewer=viewer,
        item_type=item_type,
        item_id=item_id,
        defaults={"read_at": timestamp},
    )
    return timestamp


def read_map(*, viewer: Any, item_type: str, item_ids: Iterable[int]) -> dict[int, Any]:
    """Return ``{item_id: read_at}`` for the viewer's read items (batched)."""
    if not _is_viewer(viewer):
        return {}
    ids = [int(item_id) for item_id in item_ids if item_id]
    if not ids:
        return {}
    return {
        row.item_id: row.read_at
        for row in ViewerItemReadState.objects.filter(
            viewer=viewer, item_type=item_type, item_id__in=ids
        )
    }


def read_fields(read_at) -> dict[str, Any]:
    """The two read-state fields returned on every list/detail item."""
    return {"viewer_read_at": read_at, "unread": read_at is None}
