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
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.db.models import Q
from django.utils import timezone

from jobs.completion_actuals import COMPLETION_ACTUALS_EVENT
from jobs.models import JobEvent
from jobs.timing import add_working_hours
from pricing.models import (
    EstimatePolicy,
    PublicHoliday,
    ShopSpeedTierOptIn,
    ShopTimeWindowOptIn,
    SpeedTier,
    TimeWindowRule,
)

STANDARD_TIER = "standard"
MONEY_QUANT = Decimal("0.01")


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
    if rule.covers_public_holidays and local.date() in holidays:
        return True
    weekdays = rule.weekdays if isinstance(rule.weekdays, list) else []
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


def _target_hours(*, tier: SpeedTier, product_category: str | None) -> int | None:
    return tier.target_hours_for(product_category)


def _ready_at(*, start: datetime, tier: SpeedTier, target_hours: int) -> datetime:
    if tier.key == STANDARD_TIER:
        return add_working_hours(start, target_hours)
    # Rush tiers may legitimately run through an evening, Sunday or holiday.
    return start + timedelta(hours=target_hours)


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
        target = _target_hours(tier=tier, product_category=product_category)
        if not target:
            continue
        ready_at = _ready_at(start=start, tier=tier, target_hours=target)
        matching = matching_windows(moment=ready_at)
        window = applicable_window(moment=ready_at, shop=shop)
        if matching and window is None:
            # The ready time lands in odd hours this printer has not accepted,
            # so the tier cannot actually be met and is not offered.
            continue
        window_multiplier = Decimal(str(window.multiplier)) if window else Decimal("1.00")
        speed_multiplier = Decimal(str(tier.multiplier))
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
                "ready_by": option["ready_by"],
                "ready_label": option["ready_label"],
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
