"""Client link service (law 10.6).

A link is a signed, single-job token. ``create_client_link`` makes a new one and
replaces any previous active link for the same request; ``revoke_client_link``
kills it; ``resolve_active_link`` validates and ``record_link_use`` logs every
open.
"""
from __future__ import annotations

import logging

from django.utils import timezone

from clients.models import ClientLink, ClientLinkUse, default_link_expiry

logger = logging.getLogger(__name__)


def create_client_link(*, quote_request, manager=None) -> ClientLink:
    """Create a new link and replace any previously active link."""
    now = timezone.now()
    ClientLink.objects.filter(
        quote_request=quote_request,
        revoked_at__isnull=True,
        replaced_at__isnull=True,
    ).update(replaced_at=now, updated_at=now)
    return ClientLink.objects.create(
        quote_request=quote_request,
        created_by=manager,
        expires_at=default_link_expiry(),
    )


def revoke_client_link(link: ClientLink, *, manager=None) -> ClientLink:
    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.save(update_fields=["revoked_at", "updated_at"])
    return link


def resolve_active_link(token: str) -> ClientLink | None:
    """Return the link for a signed token iff it is active, else ``None``."""
    link = ClientLink.resolve(token)
    if link is None:
        return None
    if not link.is_active_now:
        return None
    return link


def latest_link_for(quote_request) -> ClientLink | None:
    """The most recently created link for a request, active or not (law 10.6)."""
    return quote_request.client_links.order_by("-created_at", "-id").first()


def link_status(link: ClientLink | None) -> str:
    """Manager-facing link status: sent, viewed, expired, revoked or replaced.

    A manager sees whether the client link was delivered (``sent``), opened
    (``viewed``), aged out (``expired``) or killed by the manager (``revoked``).
    Deriving the label from stored fields keeps the UI honest: nothing is
    guessed client-side.
    """
    if link is None:
        return "none"
    if link.revoked_at is not None:
        return "revoked"
    if link.replaced_at is not None:
        return "replaced"
    if link.is_expired:
        return "expired"
    if link.use_count > 0 or link.last_used_at is not None:
        return "viewed"
    return "sent"


def record_link_use(link: ClientLink, *, ip_address: str | None = None, user_agent: str = "") -> ClientLinkUse:
    """Log one use of the link and update its counters."""
    use = ClientLinkUse.objects.create(
        link=link,
        ip_address=ip_address or None,
        user_agent=str(user_agent or "")[:400],
        opened_at=timezone.now(),
    )
    link.use_count += 1
    link.last_used_at = use.opened_at
    link.save(update_fields=["use_count", "last_used_at", "updated_at"])
    logger.info(
        "client_link_used link_id=%s quote_request_id=%s use_count=%s",
        link.id,
        link.quote_request_id,
        link.use_count,
    )
    return use


__all__ = [
    "create_client_link",
    "revoke_client_link",
    "resolve_active_link",
    "record_link_use",
    "latest_link_for",
    "link_status",
]
