from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from math import ceil
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

from shops.models import OpeningHours, Shop


DEFAULT_TIMEZONE = "Africa/Nairobi"
FALLBACK_WORKDAY_HOURS = 8
TURNAROUND_LABEL_RULES: tuple[tuple[int, str], ...] = (
    (4, "Express"),
    (10, "Same day"),
    (24, "Next day"),
    (48, "Standard"),
)


@dataclass(frozen=True)
class TurnaroundEstimate:
    working_hours: int
    ready_at: datetime
    label: str
    human_ready_text: str


def get_shop_timezone(shop: Shop | None) -> ZoneInfo:
    tz_name = getattr(shop, "timezone", "") or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def to_shop_local(value: datetime | None, shop: Shop | None) -> datetime:
    base = value or timezone.now()
    if timezone.is_naive(base):
        base = timezone.make_aware(base, timezone.utc)
    return timezone.localtime(base, get_shop_timezone(shop))


def time_from_string(value: str | None, fallback: time) -> time:
    if not value:
        return fallback
    try:
        hour_value, minute_value = str(value).split(":", 1)
        return time(hour=int(hour_value), minute=int(minute_value))
    except (TypeError, ValueError):
        return fallback


def get_shop_schedule(shop: Shop | None) -> dict[int, dict[str, object]]:
    default_open = getattr(shop, "opening_time", None) or time(8, 0)
    default_close = getattr(shop, "closing_time", None) or time(18, 0)
    schedule: dict[int, dict[str, object]] = {
        weekday: {"is_closed": weekday >= 6, "start": default_open, "end": default_close}
        for weekday in range(1, 8)
    }
    if not shop:
        return schedule

    hours = list(getattr(shop, "opening_hours", OpeningHours.objects.none()).all())
    if not hours:
        return schedule

    for row in hours:
        schedule[row.weekday] = {
            "is_closed": row.is_closed,
            "start": time_from_string(getattr(row, "from_hour", None), default_open),
            "end": time_from_string(getattr(row, "to_hour", None), default_close),
        }
    return schedule


def schedule_summary(shop: Shop | None) -> str:
    day_names = {
        1: "Mon",
        2: "Tue",
        3: "Wed",
        4: "Thu",
        5: "Fri",
        6: "Sat",
        7: "Sun",
    }
    parts: list[str] = []
    for weekday, details in get_shop_schedule(shop).items():
        if details["is_closed"]:
            parts.append(f"{day_names[weekday]} closed")
            continue
        start_value = format_clock(details["start"])
        end_value = format_clock(details["end"])
        parts.append(f"{day_names[weekday]} {start_value} - {end_value}")
    return ", ".join(parts)


def _combine(local_dt: datetime, value: time) -> datetime:
    combined = datetime.combine(local_dt.date(), value)
    return combined.replace(tzinfo=local_dt.tzinfo)


def _slot_for_day(local_dt: datetime, shop: Shop | None) -> tuple[datetime | None, datetime | None, bool]:
    details = get_shop_schedule(shop)[local_dt.isoweekday()]
    if details["is_closed"]:
        return None, None, True
    start_dt = _combine(local_dt, details["start"])
    end_dt = _combine(local_dt, details["end"])
    if end_dt <= start_dt:
        return None, None, True
    return start_dt, end_dt, False


def _advance_to_next_slot(local_dt: datetime, shop: Shop | None) -> datetime:
    probe = local_dt
    for _ in range(14):
        start_dt, _, is_closed = _slot_for_day(probe, shop)
        if not is_closed and start_dt:
            return start_dt
        probe = (probe + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_dt


def normalize_start_datetime(start_at: datetime | None, shop: Shop | None) -> datetime:
    local_dt = to_shop_local(start_at, shop)
    cutoff = getattr(shop, "same_day_cutoff_time", None) if shop else None
    start_dt, end_dt, is_closed = _slot_for_day(local_dt, shop)

    if cutoff and local_dt.time() > cutoff:
        local_dt = _combine(local_dt, end_dt.time()) if end_dt else local_dt
        start_dt, end_dt, is_closed = _slot_for_day(local_dt, shop)

    if is_closed or not start_dt or not end_dt:
        return _advance_to_next_slot(local_dt, shop)
    if local_dt <= start_dt:
        return start_dt
    if local_dt >= end_dt:
        return _advance_to_next_slot((local_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0), shop)
    return local_dt


def add_working_hours(start_at: datetime | None, working_hours: int | float, shop: Shop | None) -> datetime:
    remaining_seconds = max(float(working_hours), 0) * 3600
    current = normalize_start_datetime(start_at, shop)
    if remaining_seconds <= 0:
        return current

    while remaining_seconds > 0:
        start_dt, end_dt, is_closed = _slot_for_day(current, shop)
        if is_closed or not start_dt or not end_dt:
            current = _advance_to_next_slot((current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0), shop)
            continue
        if current < start_dt:
            current = start_dt
        available_seconds = (end_dt - current).total_seconds()
        if available_seconds <= 0:
            current = _advance_to_next_slot((current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0), shop)
            continue
        consumed = min(available_seconds, remaining_seconds)
        current = current + timedelta(seconds=consumed)
        remaining_seconds -= consumed
        if remaining_seconds > 0:
            current = _advance_to_next_slot((current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0), shop)
    return current


def turnaround_label_for_hours(hours: int | None) -> str:
    normalized = int(hours or 0)
    if normalized <= 0:
        return "On request"
    for max_hours, label in TURNAROUND_LABEL_RULES:
        if normalized <= max_hours:
            return label
    return "Extended"


def humanize_working_hours(hours: int | None) -> str:
    normalized = int(hours or 0)
    if normalized <= 0:
        return "On request"
    return f"{normalized} working hour" + ("" if normalized == 1 else "s")


def format_clock(value: time | datetime) -> str:
    if isinstance(value, datetime):
        value = value.timetz().replace(tzinfo=None)
    hour = value.hour % 12 or 12
    suffix = "AM" if value.hour < 12 else "PM"
    return f"{hour}:{value.minute:02d} {suffix}"


def ready_text(ready_at: datetime | None, shop: Shop | None, now: datetime | None = None) -> str:
    if not ready_at:
        return "Ready time on request"
    local_ready = to_shop_local(ready_at, shop)
    local_now = to_shop_local(now, shop)
    time_label = format_clock(local_ready)
    day_delta = (local_ready.date() - local_now.date()).days
    if day_delta == 0:
        return f"Ready today by {time_label}"
    if day_delta == 1:
        return f"Ready tomorrow by {time_label}"
    return f"Ready by {local_ready.strftime('%A')}, {time_label}"


def derive_product_turnaround_hours(product, rush: bool = False) -> int | None:
    if not product:
        return None
    if rush and getattr(product, "rush_available", False) and getattr(product, "rush_turnaround_hours", None):
        production_hours = int(product.rush_turnaround_hours)
    elif getattr(product, "standard_turnaround_hours", None):
        production_hours = int(product.standard_turnaround_hours)
    elif getattr(product, "turnaround_days", None):
        production_hours = int(product.turnaround_days) * FALLBACK_WORKDAY_HOURS
    else:
        return None
    queue_hours = int(getattr(product, "queue_hours", 0) or 0)
    buffer_hours = int(getattr(product, "buffer_hours", 0) or 0)
    return production_hours + queue_hours + buffer_hours


def estimate_turnaround(*, shop: Shop | None, working_hours: int | None, start_at: datetime | None = None) -> TurnaroundEstimate | None:
    if working_hours is None or int(working_hours) <= 0:
        return None
    normalized_hours = int(working_hours)
    ready_at = add_working_hours(start_at, normalized_hours, shop)
    return TurnaroundEstimate(
        working_hours=normalized_hours,
        ready_at=ready_at,
        label=turnaround_label_for_hours(normalized_hours),
        human_ready_text=ready_text(ready_at, shop, now=start_at),
    )


def legacy_days_from_hours(hours: int | None) -> int | None:
    if hours is None or int(hours) <= 0:
        return None
    return max(1, ceil(int(hours) / FALLBACK_WORKDAY_HOURS))
