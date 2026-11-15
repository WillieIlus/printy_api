"""Derived manager relationships for a client (LAW 13).

A client's managers are never stored as a list. They are computed from facts
the platform already owns:

  * the client's ``UserProfile.origin_manager`` - the manager who created them,
  * every manager the client explicitly locked on the public shortlist,
  * every manager assigned to one of the client's quote requests, and
  * every manager who has a PAID ``ManagedJob`` for that client.

This module is display-only. It reads and never writes, so it cannot reach a fee decision:
which manager a job pays, and how much, comes only from the stored ``origin_manager`` and the
job's own fee snapshot (pricing/services/fee_waiver.py reads ``origin_manager_id`` directly and
never asks for a manager list). Nothing here may be imported by pricing code.

The returned payload is an explicit allowlist: it carries no money, no shop identity and no
production cost, because the client reads it.
"""

from __future__ import annotations

from django.db.models import Count, Max

PAID_PAYMENT_STATUSES = (
    "confirmed",
    "release_ready",
    "payout_on_hold",
    "released",
)

SOURCE_ORIGIN = "origin"
SOURCE_QUOTE_REQUEST = "quote_request"
SOURCE_LOCKED = "locked"
SOURCE_PAID_JOB = "paid_job"

# Canonical source order: the client's account manager, then active work, their
# shortlist, and finally completed paid work. The panel renders this order.
SOURCE_ORDER = (
    SOURCE_ORIGIN,
    SOURCE_QUOTE_REQUEST,
    SOURCE_LOCKED,
    SOURCE_PAID_JOB,
)
SOURCE_PRIORITY = {source: index for index, source in enumerate(SOURCE_ORDER)}


def _manager_photo(profile) -> str:
    return str(getattr(profile, "avatar", "") or "").strip()


def _first_name(user) -> str:
    first = str(getattr(user, "first_name", "") or "").strip()
    if first:
        return first
    name = str(getattr(user, "name", "") or "").strip()
    return name.split()[0] if name else ""


def _manager_row(user, profile) -> dict[str, object]:
    return {
        "id": user.id,
        "first_name": _first_name(user),
        "name": str(getattr(user, "name", "") or "").strip(),
        "photo": _manager_photo(profile),
        "speciality": str(getattr(profile, "manager_speciality", "") or "").strip(),
        "verified": bool(getattr(profile, "manager_verified", False)),
        "is_printy": bool(getattr(profile, "is_system_account", False)),
    }


def _relationship_priority(sources: set[str]) -> int:
    return min(SOURCE_PRIORITY[source] for source in sources)


def managers_for_client(client_user) -> list[dict[str, object]]:
    """The managers a client is related to, origin first then active work.

    A manager related for more than one reason appears once, with every source
    listed. Origin always sorts first; managers working on a current quote sort
    next, then the client's locked shortlist, then paid work.
    """
    from accounts.models import ManagerLock, UserProfile
    from jobs.models import ManagedJob
    from quotes.models import QuoteRequest

    if client_user is None or getattr(client_user, "pk", None) is None:
        return []

    sources: dict[int, set[str]] = {}
    quote_request_counts: dict[int, int] = {}
    last_quote_request_at: dict[int, object] = {}
    paid_job_counts: dict[int, int] = {}
    last_paid_job_at: dict[int, object] = {}

    profile = (
        UserProfile.objects.select_related("origin_manager", "origin_manager__profile")
        .filter(user=client_user)
        .first()
    )
    origin_manager = getattr(profile, "origin_manager", None) if profile is not None else None
    if origin_manager is not None and origin_manager.is_active:
        sources.setdefault(origin_manager.id, set()).add(SOURCE_ORIGIN)

    # The shortlist a buyer explicitly built on the public manager cards. It is
    # a preference, not an assignment, but it is exactly the relationship the
    # dashboard should reflect before any paid work exists.
    for manager_id in (
        ManagerLock.objects.filter(buyer=client_user)
        .order_by("created_at", "id")
        .values_list("manager_id", flat=True)
    ):
        sources.setdefault(manager_id, set()).add(SOURCE_LOCKED)

    # Every manager already assigned to one of the buyer's quote requests. This
    # is what makes a freshly submitted request show its manager immediately,
    # with no paid job required.
    assigned_requests = (
        QuoteRequest.objects.filter(created_by=client_user, assigned_manager__isnull=False)
        .values("assigned_manager_id")
        .annotate(total=Count("id"), last=Max("created_at"))
    )
    for aggregate in assigned_requests:
        manager_id = aggregate["assigned_manager_id"]
        sources.setdefault(manager_id, set()).add(SOURCE_QUOTE_REQUEST)
        quote_request_counts[manager_id] = aggregate["total"]
        last_quote_request_at[manager_id] = aggregate["last"]

    paid_jobs = (
        ManagedJob.objects.filter(
            client=client_user,
            broker__isnull=False,
            broker__is_active=True,
            payment_status__in=PAID_PAYMENT_STATUSES,
        )
        .values("broker_id")
        .annotate(paid_jobs=Count("id"), last_paid_job_at=Max("created_at"))
    )
    for aggregate in paid_jobs:
        manager_id = aggregate["broker_id"]
        sources.setdefault(manager_id, set()).add(SOURCE_PAID_JOB)
        paid_job_counts[manager_id] = aggregate["paid_jobs"]
        last_paid_job_at[manager_id] = aggregate["last_paid_job_at"]

    if not sources:
        return []

    profiles = {
        row.user_id: row
        for row in UserProfile.objects.filter(user_id__in=sources).select_related("user")
    }

    from accounts.models import User

    rows: list[dict[str, object]] = []
    for manager_user in User.objects.filter(pk__in=sources, is_active=True):
        manager_profile = profiles.get(manager_user.id)
        manager_sources = sources[manager_user.id]
        is_printy = bool(getattr(manager_profile, "is_system_account", False))
        # Printy is the fallback manager the platform assigns, not a person a
        # client works with. Keep it out of the panel unless it is the manager
        # actually holding the client's request.
        if is_printy and SOURCE_QUOTE_REQUEST not in manager_sources:
            continue
        row = _manager_row(manager_user, manager_profile)
        row["sources"] = [source for source in SOURCE_ORDER if source in manager_sources]
        row["paid_jobs"] = paid_job_counts.get(manager_user.id, 0)
        row["last_paid_job_at"] = last_paid_job_at.get(manager_user.id)
        row["quote_requests"] = quote_request_counts.get(manager_user.id, 0)
        row["last_quote_request_at"] = last_quote_request_at.get(manager_user.id)
        rows.append(row)

    rows.sort(
        key=lambda row: (
            _relationship_priority(set(row["sources"])),
            -(row["quote_requests"] or 0),
            -(row["paid_jobs"] or 0),
            str(row["name"]).lower(),
        )
    )
    return rows
