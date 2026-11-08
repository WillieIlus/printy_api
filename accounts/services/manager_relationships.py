"""Derived manager relationships for a client (LAW 13).

A client's managers are never stored as a list. They are computed from two facts:

  * the client's ``UserProfile.origin_manager`` - the manager who created them, and
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
SOURCE_PAID_JOB = "paid_job"


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
    }


def managers_for_client(client_user) -> list[dict[str, object]]:
    """The managers a client is related to, origin first then most recent paid work.

    A manager who is both the origin manager and has paid jobs appears once, with both
    sources listed. Origin always sorts first.
    """
    from accounts.models import UserProfile
    from jobs.models import ManagedJob

    if client_user is None or getattr(client_user, "pk", None) is None:
        return []

    rows: dict[int, dict[str, object]] = {}

    profile = (
        UserProfile.objects.select_related("origin_manager", "origin_manager__profile")
        .filter(user=client_user)
        .first()
    )
    origin_manager = getattr(profile, "origin_manager", None) if profile is not None else None
    if origin_manager is not None and origin_manager.is_active:
        origin_profile = getattr(origin_manager, "profile", None)
        row = _manager_row(origin_manager, origin_profile)
        row["sources"] = [SOURCE_ORIGIN]
        row["paid_jobs"] = 0
        row["last_paid_job_at"] = None
        rows[origin_manager.id] = row

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
        existing = rows.get(manager_id)
        if existing is not None:
            existing["sources"] = [SOURCE_ORIGIN, SOURCE_PAID_JOB]
            existing["paid_jobs"] = aggregate["paid_jobs"]
            existing["last_paid_job_at"] = aggregate["last_paid_job_at"]
            continue
        manager = (
            UserProfile.objects.select_related("user")
            .filter(user_id=manager_id)
            .first()
        )
        if manager is None:
            # A broker without a profile row still counts as a manager.
            from accounts.models import User

            manager_user = User.objects.filter(pk=manager_id).first()
            if manager_user is None:
                continue
            row = _manager_row(manager_user, None)
        else:
            row = _manager_row(manager.user, manager)
        row["sources"] = [SOURCE_PAID_JOB]
        row["paid_jobs"] = aggregate["paid_jobs"]
        row["last_paid_job_at"] = aggregate["last_paid_job_at"]
        rows[manager_id] = row

    ordered = sorted(
        rows.values(),
        key=lambda row: (
            0 if SOURCE_ORIGIN in row["sources"] else 1,
            -(row["paid_jobs"] or 0),
            str(row["name"]).lower(),
        ),
    )
    return ordered
