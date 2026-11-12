"""Speed tiers, odd-hours windows and measured time estimates (prompt 6).

One rule, one multiplier: the price uplift is the HIGHER of the selected speed
tier and the odd-hours window, never their product. The uplift is added to the
printer's production cost only, so the printer receives the full uplift through
the existing protected-floor split; manager margin and Printy fee are then
calculated on that uplifted base.

Nothing here knows a printer's name. Buyer payloads never include shop
identity or a cost breakdown.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db.models import Q
from django.utils import timezone

from jobs.completion_actuals import COMPLETION_ACTUALS_EVENT
from jobs.models import JobEvent
from jobs.timing import add_working_hours, is_working_day
from pricing.models import (
    EstimatePolicy,
    PublicHoliday,
    ShopSpeedTierOptIn,
    ShopTimeWindowOptIn,
    SpeedTier,
    TimeWindowRule,
)
from services.pricing.speed_config import (
    STANDARD_TIER,
    derived_turnaround_hours,
    multiplier_for_tier,
    surcharge_percent_for_tier,
)

MONEY_QUANT = Decimal("0.01")
DEFAULT_DAY_START = time(8, 0)
DEFAULT_DAY_END = time(18, 0)
DEFAULT_TIMEZONE_NAME = "Africa/Nairobi"


def _as_time(value, fallback: time) -> time:
    """Shop hours may come back as ``time`` or as a stored "HH:MM" string."""
    if isinstance(value, time):
        return value
    if isinstance(value, str) and value.strip():
        parts = value.strip().split(":")
        try:
            return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except (TypeError, ValueError):
            return fallback
    return fallback


def shop_business_hours(shop) -> dict[str, Any]:
    """The shop's own business hours, defaulting to the platform calendar (law 20)."""
    day_start = _as_time(getattr(shop, "opening_time", None), DEFAULT_DAY_START)
    day_end = _as_time(getattr(shop, "closing_time", None), DEFAULT_DAY_END)
    if day_end <= day_start:
        day_start, day_end = DEFAULT_DAY_START, DEFAULT_DAY_END
    timezone_name = str(getattr(shop, "timezone", "") or DEFAULT_TIMEZONE_NAME)
    return {"day_start": day_start, "day_end": day_end, "timezone_name": timezone_name}


def _tier_by_key(key: str) -> SpeedTier | None:
    return SpeedTier.objects.filter(key=key).first()


def shop_standard_hours(shop, *, product_category: str | None = None) -> int | None:
    """The shop's declared standard turnaround, else the seeded standard target."""
    declared = getattr(shop, "standard_turnaround_hours", None)
    if declared:
        return max(1, int(declared))
    tier = _tier_by_key(STANDARD_TIER)
    return tier.target_hours_for(product_category) if tier else None


def shop_tier_target_hours(*, shop, tier: SpeedTier, product_category: str | None = None) -> int | None:
    """Turnaround hours for one tier: shop override, else derived from standard.

    Standard always uses the shop's declared standard turnaround. Faster/Fastest
    honour a per-shop override, otherwise they derive from the standard using the
    configured factors (law 19)."""
    if tier.key == STANDARD_TIER:
        return shop_standard_hours(shop, product_category=product_category)
    optin = ShopSpeedTierOptIn.objects.filter(shop=shop, tier=tier).first()
    if optin is not None and optin.target_hours:
        return max(1, int(optin.target_hours))
    standard = shop_standard_hours(shop, product_category=product_category)
    derived = derived_turnaround_hours(tier.key, standard)
    if derived:
        return derived
    return tier.target_hours_for(product_category)


def _money(value: Any) -> Decimal:
    return Decimal(str(value or "0")).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _minutes_label(hours: float | int) -> str:
    total = int(round(float(hours) * 60))
    if total < 60:
        return f"{total} min"
    whole, rest = divmod(total, 60)
    return f"{whole}h {rest}m" if rest else f"{whole}h"


def local_time(moment: datetime):
    return timezone.localtime(moment)


def active_tiers() -> list[SpeedTier]:
    return list(SpeedTier.objects.filter(is_active=True).order_by("sort_order", "id"))


def active_windows() -> list[TimeWindowRule]:
    return list(TimeWindowRule.objects.filter(is_active=True).order_by("multiplier", "id"))


def holiday_dates() -> set:
    return set(
        PublicHoliday.objects.filter(is_active=True).values_list("date", flat=True)
    )


def shop_tier_available(*, shop, tier: SpeedTier) -> bool:
    """Standard is always available. Every other tier needs an active opt-in."""
    if tier.key == STANDARD_TIER:
        return True
    if shop is None:
        return False
    return ShopSpeedTierOptIn.objects.filter(shop=shop, tier=tier, is_active=True).exists()


def shop_window_available(*, shop, rule: TimeWindowRule) -> bool:
    if rule.multiplier <= 1:
        return True
    if shop is None:
        return False
    return ShopTimeWindowOptIn.objects.filter(shop=shop, rule=rule, is_active=True).exists()


def _window_matches(rule: TimeWindowRule, moment: datetime, *, holidays: set[date]) -> bool:
    local = local_time(moment)
    is_holiday = local.date() in holidays
    if rule.covers_public_holidays and is_holiday:
        return True
    weekdays = rule.weekdays if isinstance(rule.weekdays, list) else []
    has_day_rule = bool(weekdays) or rule.start_time is not None or rule.end_time is not None
    # A rule that exists only to cover public holidays must never match an
    # ordinary working day, or every job would be surcharged all year.
    if rule.covers_public_holidays and not has_day_rule:
        return False
    if weekdays and local.isoweekday() not in {int(day) for day in weekdays}:
        return False
    if rule.start_time is None or rule.end_time is None:
        return True
    current = local.time().replace(tzinfo=None)
    start = rule.start_time
    end = rule.end_time
    if start <= end:
        return start <= current < end
    # Overnight window, e.g. 22:00 -> 06:00.
    return current >= start or current < end


def matching_windows(*, moment: datetime) -> list[TimeWindowRule]:
    """All active odd-hours rules that cover *moment*, regardless of opt-in."""
    holidays = holiday_dates()
    return [
        rule
        for rule in active_windows()
        if rule.multiplier > 1 and _window_matches(rule, moment, holidays=holidays)
    ]


def applicable_window(*, moment: datetime, shop=None) -> TimeWindowRule | None:
    """The highest-multiplier window that covers *moment* and the shop accepts."""
    candidates = [
        rule for rule in matching_windows(moment=moment) if shop_window_available(shop=shop, rule=rule)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda rule: (rule.multiplier, rule.id))


def _ready_at(*, shop, start: datetime, target_hours: int) -> datetime:
    """Ready-by always rolls through the shop's business hours (law 20)."""
    return add_working_hours(start, target_hours, **shop_business_hours(shop))


def _within_shop_business_hours(shop, moment: datetime) -> bool:
    """True when ``moment`` is inside the shop's open working time.

    Law 20 computes every ready-by inside business hours, so a ready time that
    falls inside the shop's own hours can never be an odd-hours promise. The
    window rules stay available for shops that trade later than a window starts.
    """
    hours = shop_business_hours(shop)
    local = moment.astimezone(ZoneInfo(hours["timezone_name"]))
    if not is_working_day(local.date()):
        return False
    clock = local.timetz().replace(tzinfo=None)
    return hours["day_start"] <= clock <= hours["day_end"]


def estimate_for_spec(
    *,
    product_category: str | None,
    shop=None,
    declared_hours: int | None = None,
) -> dict[str, Any]:
    """Median of real completed-job durations, else the printer's declared time.

    ``source`` is exactly "measured" or "declared". Below the configured minimum
    job count the answer is the declared target and never a sample figure.
    """
    policy = EstimatePolicy.objects.filter(is_active=True).order_by("-updated_at", "-id").first()
    minimum = int(policy.minimum_measured_jobs) if policy else 5

    query = JobEvent.objects.filter(event=COMPLETION_ACTUALS_EVENT)
    if shop is not None:
        query = query.filter(managed_job__assigned_shop=shop)
    durations: list[float] = []
    job_ids: set[int] = set()
    for event in query.select_related("managed_job"):
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        products = metadata.get("products") or []
        product_label = str(metadata.get("product") or "").lower()
        category_key = (product_category or "").lower()
        if category_key and category_key not in product_label and category_key not in [
            str(item).lower() for item in products
        ]:
            continue
        stage_durations = metadata.get("stage_durations")
        minutes = 0.0
        if isinstance(stage_durations, dict):
            for value in stage_durations.values():
                try:
                    minutes += float(value)
                except (TypeError, ValueError):
                    continue
        elif metadata.get("turnaround_hours") not in (None, ""):
            try:
                minutes = float(metadata["turnaround_hours"]) * 60
            except (TypeError, ValueError):
                minutes = 0.0
        if minutes > 0:
            durations.append(minutes)
            job_ids.add(event.managed_job_id)

    if len(job_ids) >= minimum and durations:
        median_hours = statistics.median(durations) / 60
        return {
            "hours": round(median_hours, 2),
            "label": _minutes_label(median_hours),
            "source": "measured",
            "jobs_counted": len(job_ids),
        }

    hours = declared_hours
    return {
        "hours": hours,
        "label": _minutes_label(hours) if hours else "",
        "source": "declared" if hours else "",
        "jobs_counted": len(job_ids),
    }


def build_speed_options(
    *,
    shop,
    product_category: str | None,
    base_production_cost: Any,
    start_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every tier the printer can actually meet, in display order.

    A tier is returned only when it is active, the printer opted in, a target
    turnaround exists, and the printer accepts the odd-hours window that the
    resulting ready time falls into. Nothing is greyed out downstream.
    """
    base = _money(base_production_cost)
    if base <= 0:
        return []
    start = start_at or timezone.now()
    options: list[dict[str, Any]] = []
    for tier in active_tiers():
        if not shop_tier_available(shop=shop, tier=tier):
            continue
        target = shop_tier_target_hours(shop=shop, tier=tier, product_category=product_category)
        if not target:
            continue
        ready_at = _ready_at(shop=shop, start=start, target_hours=target)
        if _within_shop_business_hours(shop, ready_at):
            matching: list[TimeWindowRule] = []
            window = None
        else:
            matching = matching_windows(moment=ready_at)
            window = applicable_window(moment=ready_at, shop=shop)
        if matching and window is None:
            # The ready time lands in odd hours this printer has not accepted,
            # so the tier cannot actually be met and is not offered.
            continue
        window_multiplier = Decimal(str(window.multiplier)) if window else Decimal("1.00")
        # Law 18: the surcharge percentage is configuration, not a DB value.
        speed_multiplier = multiplier_for_tier(tier.key)
        multiplier = max(speed_multiplier, window_multiplier)
        production_total = _money(base * multiplier)
        uplift = _money(production_total - base)
        rule = "time_window" if window_multiplier > speed_multiplier else "speed_tier"
        window_rule = None
        if window is not None:
            window_rule = {
                "key": window.key,
                "label": window.label,
                "multiplier": str(window_multiplier),
            }
        estimate = estimate_for_spec(
            product_category=product_category,
            shop=shop,
            declared_hours=target,
        )
        options.append(
            {
                "tier": {
                    "key": tier.key,
                    "label": tier.label,
                    "multiplier": str(speed_multiplier),
                },
                "speed_multiplier": str(speed_multiplier),
                "surcharge_percent": surcharge_percent_for_tier(tier.key),
                "turnaround_hours": target,
                "is_estimate": True,
                "time_window": window_rule,
                "applied_rule": rule,
                "multiplier": str(multiplier),
                "base_production_cost": str(base),
                "production_total": str(production_total),
                "uplift": str(uplift),
                "rush_premium": str(uplift) if tier.key != STANDARD_TIER else "0.00",
                "ready_by": ready_at.isoformat(),
                "ready_label": estimate["label"] or _minutes_label(target),
                "estimate": estimate,
                "surcharge_line": (
                    f"{window.label}: +{int((window_multiplier - 1) * 100)}%"
                    if window is not None and window_multiplier > speed_multiplier
                    else (
                        f"{tier.label}: +{int((speed_multiplier - 1) * 100)}%"
                        if speed_multiplier > 1
                        else ""
                    )
                ),
            }
        )
    return options


def select_speed_option(*, options: list[dict[str, Any]], tier_key: str) -> dict[str, Any] | None:
    for option in options:
        if option["tier"]["key"] == tier_key:
            return option
    return None


def apply_speed_selection(*, quote_request, option: dict[str, Any]) -> dict[str, Any]:
    """Freeze the chosen option on the request. The due date is set at acceptance."""
    snapshot = {
        **option,
        "selected_at": timezone.now().isoformat(),
        "locked": False,
    }
    quote_request.speed_tier = option["tier"]["key"]
    quote_request.time_window_rule = (option.get("time_window") or {}).get("key", "")
    quote_request.speed_multiplier = Decimal(str(option["multiplier"]))
    quote_request.production_uplift = Decimal(str(option["uplift"]))
    quote_request.rush_premium = Decimal(str(option.get("rush_premium") or "0.00"))
    quote_request.rush_premium_status = "" if quote_request.rush_premium <= 0 else "pending"
    quote_request.speed_snapshot = snapshot
    quote_request.save(
        update_fields=[
            "speed_tier",
            "time_window_rule",
            "speed_multiplier",
            "production_uplift",
            "rush_premium",
            "rush_premium_status",
            "speed_snapshot",
            "updated_at",
        ]
    )
    return snapshot


def lock_speed_at_acceptance(*, quote_request, accepted_at: datetime) -> None:
    """Mark the speed terms locked together with the price and due date."""
    snapshot = quote_request.speed_snapshot if isinstance(quote_request.speed_snapshot, dict) else {}
    if not snapshot:
        return
    snapshot = {**snapshot, "locked": True, "locked_at": accepted_at.isoformat()}
    quote_request.speed_snapshot = snapshot
    quote_request.save(update_fields=["speed_snapshot", "updated_at"])


def due_date_for_request(*, quote_request, fallback) -> datetime:
    """The accepted ready time when a speed option exists, else the SLA date."""
    snapshot = quote_request.speed_snapshot if isinstance(quote_request.speed_snapshot, dict) else {}
    ready_by = snapshot.get("ready_by")
    if ready_by:
        try:
            parsed = datetime.fromisoformat(str(ready_by).replace("Z", "+00:00"))
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            return parsed
        except (TypeError, ValueError):
            pass
    return fallback


def buyer_speed_payload(*, options: list[dict[str, Any]]) -> dict[str, Any]:
    """Buyer-safe options: price and time only, never shop identity or a breakdown."""
    return {
        "options": [
            {
                "key": option["tier"]["key"],
                "label": option["tier"]["label"],
                "price": option["production_total"],
                "price_change_percent": int((Decimal(option["multiplier"]) - 1) * 100),
                "surcharge_percent": int(option.get("surcharge_percent") or 0),
                "turnaround_hours": option.get("turnaround_hours"),
                "ready_by": option["ready_by"],
                "ready_label": option["ready_label"],
                "is_estimate": True,
                "estimate_source": option["estimate"]["source"],
                "surcharge_line": option["surcharge_line"],
            }
            for option in options
        ],
        "estimate": options[0]["estimate"] if options else None,
    }


__all__ = [
    "STANDARD_TIER",
    "active_tiers",
    "active_windows",
    "applicable_window",
    "apply_speed_selection",
    "build_speed_options",
    "buyer_speed_payload",
    "due_date_for_request",
    "estimate_for_spec",
    "holiday_dates",
    "lock_speed_at_acceptance",
    "select_speed_option",
    "shop_tier_available",
    "shop_window_available",
]
