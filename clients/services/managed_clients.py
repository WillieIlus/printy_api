"""Managed-client service (law 10.5).

A manager may create a quote request on behalf of a client by entering the
client's name, phone and a confirmation that the client permits contact. This is
the *only* buyer-only power a manager gets (law 9.2 exception). The request is
attributed to the manager (``created_by_manager``), belongs to the client's
record (``on_behalf_of``), and an event is written to the request log.
"""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.services.client_invites import create_client_invite, find_account_by_phone
from accounts.services.phone import normalize_phone
from quotes.choices import QuoteStatus

logger = logging.getLogger(__name__)

User = get_user_model()


class ManagedClientError(Exception):
    """A client-safe error while onboarding a client (message is shown as-is)."""


def _client_display_name(client_user, name: str, phone: str) -> str:
    return str(name or getattr(client_user, "name", "") or getattr(client_user, "email", "") or phone or "Client").strip()


@transaction.atomic
def create_request_for_client(
    *,
    manager,
    name: str,
    phone: str,
    contact_permitted: bool,
    email: str = "",
    company: str = "",
    details: dict | None = None,
    notes: str = "",
    delivery_preference: str = "",
):
    """Create a quote request originated by ``manager`` for a client phone.

    Returns ``(quote_request, client_user, invite)``. ``client_user`` is None
    while the client has only an invite; ``invite`` is None when an existing
    client was reused.
    """
    if not contact_permitted:
        raise ManagedClientError("Confirm you have the client's permission to contact them.")

    try:
        normalized = normalize_phone(phone)
    except Exception:
        raise ManagedClientError("Enter a valid Kenyan phone number, for example 0700000001.")
    if not normalized:
        raise ManagedClientError("A client phone number is required.")

    client_user = find_account_by_phone(normalized)
    if client_user is not None and getattr(client_user, "role", "") != User.Role.CLIENT:
        raise ManagedClientError("That phone number belongs to an existing manager or printer account and cannot be used for a client.")

    invite = None
    if client_user is None:
        invite, linked = create_client_invite(created_by=manager, name=name, phone=phone, email=email, company=company)
        client_user = linked

    cleaned_name = str(name or "").strip()
    cleaned_email = str(email or "").strip().lower()
    cleaned_company = str(company or "").strip()
    pending_client = {
        "name": _client_display_name(client_user, cleaned_name, normalized),
        "phone": normalized,
        "email": cleaned_email,
        "company": cleaned_company,
        "client_user_id": getattr(client_user, "id", None),
    }

    from quotes.models import QuoteRequest

    quote_request = QuoteRequest.objects.create(
        created_by=manager,
        assigned_manager=manager,
        created_by_manager=manager,
        on_behalf_of=client_user,
        customer_name=pending_client["name"],
        customer_email=cleaned_email,
        customer_phone=normalized,
        status=QuoteStatus.SUBMITTED,
        notes=str(notes or "").strip(),
        delivery_preference=delivery_preference or "",
        request_snapshot={
            "created_by_manager_id": manager.id,
            "client_contact_permitted": True,
            "pending_client": pending_client,
            "request_details": details or {},
            "client_event_log": [
                {
                    "event": "manager_created_client_request",
                    "actor_id": manager.id,
                    "at": timezone.now().isoformat(),
                    "client_user_id": getattr(client_user, "id", None),
                }
            ],
        },
    )
    quote_request.request_reference = f"QR-{timezone.now():%Y%m%d}-{quote_request.id:04d}"
    quote_request.save(update_fields=["request_reference", "updated_at"])

    logger.info(
        "manager_created_client_request manager_id=%s quote_request_id=%s client_user_id=%s",
        manager.id,
        quote_request.id,
        getattr(client_user, "id", None),
    )

    if client_user is not None:
        try:
            from notifications.services import notify

            notify(
                recipient=client_user,
                notification_type="quote_request_submitted",
                object_type="quote_request",
                object_id=quote_request.id,
                actor=manager,
                send_email_notification=False,
            )
        except Exception:  # notification is best-effort; the request is already saved
            logger.exception("client_request_notification_failed quote_request_id=%s", quote_request.id)

    return quote_request, client_user, invite


def log_client_event(quote_request, *, event: str, actor=None, **extra) -> None:
    """Append one event to the request's client event log (law 10.5)."""
    snapshot = quote_request.request_snapshot if isinstance(quote_request.request_snapshot, dict) else {}
    log = list(snapshot.get("client_event_log") or [])
    log.append(
        {
            "event": event,
            "actor_id": getattr(actor, "id", None),
            "at": timezone.now().isoformat(),
            **extra,
        }
    )
    snapshot["client_event_log"] = log
    quote_request.request_snapshot = snapshot
    quote_request.save(update_fields=["request_snapshot", "updated_at"])


def visible_client_user_ids(manager) -> set[int]:
    """Client user ids a manager may see (law 10.5).

    Onboarded clients (invites they created) plus clients with a request or job
    they own. A second manager who reuses the phone sees none of this.
    """
    if manager is None or not getattr(manager, "pk", None):
        return set()

    ids: set[int] = set()

    from accounts.models import ClientInvite

    ids.update(
        ClientInvite.objects.filter(created_by=manager, client_user__isnull=False).values_list("client_user_id", flat=True)
    )

    from quotes.models import QuoteRequest

    ids.update(
        QuoteRequest.objects.filter(Q(created_by=manager) | Q(assigned_manager=manager))
        .filter(on_behalf_of__isnull=False)
        .values_list("on_behalf_of_id", flat=True)
    )

    from jobs.models import ManagedJob

    ids.update(
        ManagedJob.objects.filter(Q(broker=manager) | Q(created_by=manager))
        .filter(client__isnull=False)
        .values_list("client_id", flat=True)
    )

    return {pk for pk in ids if pk}


__all__ = [
    "ManagedClientError",
    "create_request_for_client",
    "log_client_event",
    "visible_client_user_ids",
]
