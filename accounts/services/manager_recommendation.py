"""Public manager recommendation service.

The homepage asks one question: "who can handle this job, at this speed?" This
module answers it from real manager profiles and managed jobs. It never stores
active load or on-time percentage as columns - both are derived on every call
so they cannot drift - and it never prices a manager. The three tier prices are
read from the existing public calculator calculation; every manager therefore
sees exactly the same price for the same job.

Nothing in this module may import or expose shop identity, production cost,
margin or an internal primary key. The only manager identifier that crosses the
public boundary is ``UserProfile.public_id``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from accounts.models import ManagerLock, User, UserProfile
from jobs.choices import ManagedJobStatus
from jobs.models import ManagedJob
from jobs.timing import (
    STAGES,
    STAGES_BY_KEY,
    add_working_hours,
    on_time_rate,
    stage_for_status,
    total_stage_sla_working_hours,
)
from quotes.models import QuoteRequest
from services.pricing.calculator_preview import build_public_calculator_preview
from services.pricing.speed_config import (
    DESIGN_SPEED_TIER_KEYS,
    DESIGN_SPEED_TIER_LABELS,
    derived_turnaround_hours,
)

MONEY_QUANTIZER = Decimal("0.01")
STATUS_AVAILABLE_NOW = "available_now"
STATUS_FREE_THIS_WEEK = "free_this_week"
STATUS_IN_QUEUE = "in_queue"
AVAILABILITY_RANK = {
    STATUS_AVAILABLE_NOW: 0,
    STATUS_FREE_THIS_WEEK: 1,
    STATUS_IN_QUEUE: 2,
}

TERMINAL_JOB_STATUSES = {
    ManagedJobStatus.COMPLETED,
    ManagedJobStatus.CANCELLED,
    ManagedJobStatus.DISPUTED,
}

_NUMBER_WORDS = {
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
    10: "Ten",
}


def _money(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    except (TypeError, ValueError, ArithmeticError):
        return None


def _display_name(profile: UserProfile) -> str:
    return (
        str(profile.public_display_name or "").strip()
        or str(getattr(profile.user, "name", "") or "").strip()
    )


def _product_type_from_snapshot(snapshot: dict | None) -> str:
    data = snapshot if isinstance(snapshot, dict) else {}
    nested = data.get("request_snapshot") if isinstance(data.get("request_snapshot"), dict) else {}
    calculator = data.get("calculator_inputs") if isinstance(data.get("calculator_inputs"), dict) else {}
    return str(
        nested.get("product_type")
        or data.get("product_type")
        or calculator.get("product_type")
        or ""
    ).strip().lower()


def _product_types_for_manager(*, quoted_requests=None, managed_jobs=None) -> set[str]:
    product_types: set[str] = set()
    for request in quoted_requests or []:
        product_type = _product_type_from_snapshot(getattr(request, "request_snapshot", None))
        if product_type:
            product_types.add(product_type)
    for job in managed_jobs or []:
        quote_request = getattr(job, "source_quote_request", None)
        product_type = _product_type_from_snapshot(getattr(quote_request, "request_snapshot", None))
        if product_type:
            product_types.add(product_type)
    return product_types


def _can_handle_product(*, profile: UserProfile, product_type: str, product_types: set[str]) -> bool:
    """Mirror the existing manager-capability rule.

    There is no dedicated manager capability table today: the platform derives
    product history from quote requests and managed jobs. A partner-profile
    manager is allowed to review any supported product (the same rule the
    existing intake recommendation uses), while other verified managers must
    have handled the product before.
    """
    if not product_type:
        return True
    if getattr(profile.user, "partner_profile_enabled", False):
        return True
    return product_type in product_types


def _remaining_working_hours(job: ManagedJob) -> Decimal:
    stage_key = stage_for_status(getattr(job, "status", None))
    keys = [stage.key for stage in STAGES]
    try:
        start_index = keys.index(stage_key)
    except ValueError:
        start_index = 0
    return sum((stage.sla_working_hours for stage in STAGES[start_index:]), Decimal("0"))


def _earliest_start(*, active_jobs: list[ManagedJob], now: datetime) -> datetime:
    if not active_jobs:
        return now
    ordered = sorted(
        active_jobs,
        key=lambda job: (
            getattr(job, "due_date", None) or now + timedelta(days=3650),
            getattr(job, "created_at", None) or now,
            job.pk or 0,
        ),
    )
    cursor = now
    for job in ordered:
        hours = _remaining_working_hours(job)
        if hours > 0:
            cursor = add_working_hours(cursor, hours)
    return cursor


def _queue_label(count: int) -> str:
    number = _NUMBER_WORDS.get(count, str(count))
    return f"{number} job{'s' if count != 1 else ''} in the queue"


def _availability(*, active_count: int, earliest_start: datetime, now: datetime) -> dict[str, Any]:
    if active_count == 0:
        return {
            "status": STATUS_AVAILABLE_NOW,
            "label": "Available now",
            "active_jobs": active_count,
        }
    if earliest_start <= now + timedelta(days=7):
        return {
            "status": STATUS_FREE_THIS_WEEK,
            "label": "Free this week",
            "active_jobs": active_count,
        }
    return {
        "status": STATUS_IN_QUEUE,
        "label": _queue_label(active_count),
        "active_jobs": active_count,
    }


def _ready_label(moment: datetime) -> str:
    local = timezone.localtime(moment)
    return local.strftime("%a %d %b, %H:%M")


def _photo_url(profile: UserProfile, *, request=None) -> str | None:
    """Return an absolute photo URL only when the stored file actually exists."""
    photo = getattr(profile, "manager_photo", None)
    if not photo or not getattr(photo, "name", ""):
        return None
    storage = photo.storage
    try:
        if not storage.exists(photo.name):
            return None
        url = photo.url
    except (OSError, ValueError, NotImplementedError):
        return None
    if request is not None:
        try:
            return request.build_absolute_uri(url)
        except Exception:
            return url
    return url


def manager_photo_url(profile: UserProfile, *, request=None) -> str | None:
    """Public helper used by the lock listing as well as recommendations."""
    return _photo_url(profile, request=request)


def locked_manager_payloads(*, buyer, request=None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Return the buyer's locked managers as the same public card shape."""
    now = now or timezone.now()
    rows: list[dict[str, Any]] = []
    locks = (
        ManagerLock.objects.filter(buyer=buyer)
        .select_related("manager", "manager__profile")
        .order_by("created_at", "id")
    )
    for lock in locks:
        manager = lock.manager
        try:
            profile = manager.profile
        except UserProfile.DoesNotExist:
            continue
        snapshot = _manager_snapshot(profile=profile, now=now, enforce_capacity=False)
        if snapshot is None:
            continue
        rows.append(
            {
                "public_id": str(profile.public_id),
                "display_name": snapshot["display_name"],
                "photo_url": manager_photo_url(profile, request=request),
                "verified": bool(profile.manager_verified),
                "availability": snapshot["availability"],
                "at_capacity": snapshot["at_capacity"],
                "on_time_pct": snapshot["on_time_pct"],
                "completed_jobs": snapshot["completed_jobs"],
                "locked_at": lock.created_at.isoformat(),
            }
        )
    return rows


def _tier_prices(*, preview: dict[str, Any], payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    try:
        quantity = int(payload.get("quantity") or 1)
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(quantity, 1)
    by_key: dict[str, dict[str, Any]] = {}
    for option in preview.get("speed_options") or []:
        if not isinstance(option, dict):
            continue
        key = option.get("key")
        if key not in DESIGN_SPEED_TIER_KEYS:
            continue
        total = _money(option.get("total"))
        if total is None:
            continue
        by_key[key] = {
            "key": key,
            "label": option.get("label") or DESIGN_SPEED_TIER_LABELS[key],
            "total": total,
            "per_piece": (total / Decimal(quantity)).quantize(
                MONEY_QUANTIZER, rounding=ROUND_HALF_UP
            ),
            "turnaround_hours": option.get("turnaround_hours"),
        }

    # A marketing preview carries no turnaround target, so the calculator
    # correctly returns ``turnaround_hours: null``. The recommendation section
    # still needs a real ready-by claim: derive the standard tier from the
    # platform's stage SLAs and the rush tiers from the configured factors.
    # This fallback lives here only; the public calculator payload is untouched.
    fallback_standard_hours = int(total_stage_sla_working_hours())
    for tier_key, price in by_key.items():
        if not price.get("turnaround_hours"):
            price["turnaround_hours"] = derived_turnaround_hours(
                tier_key, fallback_standard_hours
            )
    return by_key


def _manager_snapshot(
    *, profile: UserProfile, now: datetime, enforce_capacity: bool = True
) -> dict[str, Any] | None:
    user = profile.user
    display_name = _display_name(profile)
    if not display_name:
        return None

    active_jobs = list(
        ManagedJob.objects.filter(broker_id=user.id)
        .exclude(status__in=TERMINAL_JOB_STATUSES)
        .only("id", "status", "due_date", "created_at", "assigned_at")
    )
    active_count = len(active_jobs)
    capacity = int(profile.max_concurrent_jobs or 0)
    if enforce_capacity and (capacity <= 0 or active_count >= capacity):
        return None

    completed_jobs = list(
        ManagedJob.objects.filter(broker_id=user.id, status=ManagedJobStatus.COMPLETED)
        .only("id", "due_date", "is_late", "assigned_at", "completed_at")
    )
    threshold = int(getattr(settings, "PRINTY_MANAGER_ON_TIME_MIN_JOBS", 10))
    earliest_start = _earliest_start(active_jobs=active_jobs, now=now)

    latest_assigned = profile.last_assigned_at
    for job in [*active_jobs, *completed_jobs]:
        candidate = getattr(job, "assigned_at", None) or getattr(job, "completed_at", None)
        if candidate is not None and (latest_assigned is None or candidate > latest_assigned):
            latest_assigned = candidate

    quoted_requests = list(
        QuoteRequest.objects.filter(Q(assigned_manager_id=user.id) | Q(created_by_manager_id=user.id))
        .only("id", "request_snapshot")
    )
    managed_jobs_for_history = list(
        ManagedJob.objects.filter(broker_id=user.id)
        .select_related("source_quote_request")
        .only("id", "source_quote_request__request_snapshot")
    )
    product_types = _product_types_for_manager(
        quoted_requests=quoted_requests, managed_jobs=managed_jobs_for_history
    )

    return {
        "profile": profile,
        "display_name": display_name,
        "active_jobs": active_count,
        "at_capacity": capacity <= 0 or active_count >= capacity,
        "earliest_start": earliest_start,
        "availability": _availability(
            active_count=active_count, earliest_start=earliest_start, now=now
        ),
        "on_time_pct": on_time_rate(completed_jobs=completed_jobs, minimum=threshold),
        "completed_jobs": len(completed_jobs),
        "last_assigned_at": latest_assigned,
        "product_types": product_types,
    }


def _candidate_profiles() -> list[UserProfile]:
    return list(
        UserProfile.objects.select_related("user")
        .filter(
            # Only real manager accounts: a broker/partner, or an explicit
            # hybrid partner profile. A verified client profile must never be
            # recommended just because someone ticked verification.
            Q(user__role__in=[User.Role.BROKER, User.Role.PARTNER])
            | Q(user__partner_profile_enabled=True),
            # LAW 14: verification alone is not consent. A manager who has not
            # agreed to be shown publicly must never reach this endpoint.
            manager_public_opt_in=True,
            manager_verified=True,
            accepts_new_jobs=True,
            user__is_active=True,
            is_system_account=False,
        )
        .exclude(max_concurrent_jobs=0)
        .order_by("id")
    )


def recommend_managers(
    *,
    payload: dict[str, Any],
    buyer=None,
    request=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return all three speed tiers with their real qualifying managers."""
    now = now or timezone.now()
    preview = build_public_calculator_preview(payload)
    prices_by_tier = _tier_prices(preview=preview, payload=payload)
    currency = str(preview.get("currency") or "KES")
    product_type = str(payload.get("product_type") or "").strip().lower()

    buyer_is_authenticated = bool(
        buyer is not None and getattr(buyer, "is_authenticated", False)
    )
    locked_ids: set[int] = set()
    if buyer_is_authenticated:
        locked_ids = set(
            ManagerLock.objects.filter(buyer=buyer).values_list("manager_id", flat=True)
        )

    snapshots: list[dict[str, Any]] = []
    for profile in _candidate_profiles():
        snapshot = _manager_snapshot(profile=profile, now=now)
        if snapshot is None:
            continue
        if not _can_handle_product(
            profile=profile,
            product_type=product_type,
            product_types=snapshot["product_types"],
        ):
            continue
        snapshots.append(snapshot)

    tiers: dict[str, dict[str, Any]] = {}
    for tier_key in DESIGN_SPEED_TIER_KEYS:
        label = DESIGN_SPEED_TIER_LABELS[tier_key]
        price = prices_by_tier.get(tier_key)
        if price is None:
            tiers[tier_key] = {
                "key": tier_key,
                "label": label,
                "not_offered": True,
                "managers": [],
            }
            continue

        tier_hours = price.get("turnaround_hours")
        tier_ready_by = (
            add_working_hours(now, Decimal(str(tier_hours))) if tier_hours else None
        )
        # The production-stage SLA is the existing job production time. Cap it
        # at the tier's own turnaround: a rush tier may compress production, but
        # a manager who is free now is never excluded by rush compression.
        production_hours = STAGES_BY_KEY["production"].sla_working_hours
        if tier_hours:
            production_hours = min(production_hours, Decimal(str(tier_hours)))

        ranked: list[dict[str, Any]] = []
        for snapshot in snapshots:
            profile = snapshot["profile"]
            manager_ready_by = None
            qualifies_time = True
            if tier_hours:
                manager_ready_by = add_working_hours(
                    snapshot["earliest_start"], production_hours
                )
                qualifies_time = bool(
                    tier_ready_by is not None and manager_ready_by <= tier_ready_by
                )
            if not qualifies_time:
                continue

            ready_by = tier_ready_by or manager_ready_by
            if tier_ready_by and manager_ready_by:
                ready_by = max(tier_ready_by, manager_ready_by)

            manager_payload = {
                "public_id": str(profile.public_id),
                "display_name": snapshot["display_name"],
                "photo_url": manager_photo_url(profile, request=request),
                "verified": True,
                "availability": snapshot["availability"],
                "ready_by": ready_by.isoformat() if ready_by else None,
                "ready_label": _ready_label(ready_by) if ready_by else "",
                "price": {
                    "total": f"{price['total']:.2f}",
                    "per_piece": f"{price['per_piece']:.2f}",
                    "currency": currency,
                },
                "on_time_pct": snapshot["on_time_pct"],
                "completed_jobs": snapshot["completed_jobs"],
                "recommended": False,
            }
            if buyer_is_authenticated:
                manager_payload["is_locked"] = profile.user_id in locked_ids

            ranked.append(
                {
                    "payload": manager_payload,
                    "availability_rank": AVAILABILITY_RANK.get(
                        snapshot["availability"]["status"], 3
                    ),
                    "active_jobs": snapshot["active_jobs"],
                    "ready_by": ready_by,
                    "on_time_pct": snapshot["on_time_pct"],
                    "last_assigned_at": snapshot["last_assigned_at"],
                }
            )

        def sort_key(entry: dict[str, Any]) -> tuple:
            ready = entry["ready_by"] or datetime.max.replace(tzinfo=dt_timezone.utc)
            on_time = entry["on_time_pct"]
            on_time_key = (0, -on_time) if on_time is not None else (1, 0.0)
            last_assigned = entry["last_assigned_at"] or datetime.min.replace(
                tzinfo=dt_timezone.utc
            )
            return (
                entry["availability_rank"],
                entry["active_jobs"],
                ready,
                on_time_key,
                last_assigned,
            )

        ranked.sort(key=sort_key)
        managers = [entry["payload"] for entry in ranked[:3]]
        if managers:
            managers[0]["recommended"] = True
        tiers[tier_key] = {
            "key": tier_key,
            "label": label,
            "not_offered": False,
            "turnaround_hours": tier_hours,
            "managers": managers,
        }

    return {
        "tiers": tiers,
        "meta": {
            "product_type": product_type,
            "quantity": payload.get("quantity"),
            "currency": currency,
            "generated_at": now.isoformat(),
        },
    }


__all__ = [
    "STATUS_AVAILABLE_NOW",
    "STATUS_FREE_THIS_WEEK",
    "STATUS_IN_QUEUE",
    "TERMINAL_JOB_STATUSES",
    "locked_manager_payloads",
    "manager_photo_url",
    "recommend_managers",
]
