from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.utils import timezone


URGENCY_MULTIPLIERS: dict[str, Decimal] = {
    "standard": Decimal("1.00"),
    "same_day": Decimal("1.12"),
    "express": Decimal("1.20"),
    "after_hours": Decimal("1.10"),
    "emergency": Decimal("1.35"),
}

PRIORITY_LEVELS: dict[str, int] = {
    "standard": 1,
    "same_day": 2,
    "express": 3,
    "after_hours": 4,
    "emergency": 5,
}


def _money(value: Any) -> Decimal:
    try:
        if value in (None, ""):
            return Decimal("0")
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _set_money(target: dict[str, Any], key: str, value: Decimal) -> None:
    target[key] = str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def normalize_urgency_type(value: str | None, *, turnaround_hours: int | None = None, turnaround_label: str | None = None) -> str:
    raw = (value or "").strip().lower()
    if raw in PRIORITY_LEVELS:
        return raw

    label = (turnaround_label or "").strip().lower()
    if "same day" in label:
        return "same_day"
    if "express" in label:
        return "express"

    if turnaround_hours is not None:
        if turnaround_hours <= 4:
            return "emergency"
        if turnaround_hours <= 8:
            return "after_hours"
        if turnaround_hours <= 12:
            return "same_day"
        if turnaround_hours <= 24:
            return "express"
    return "standard"


def determine_operational_priority(
    *,
    urgency_type: str | None,
    turnaround_hours: int | None = None,
    turnaround_label: str | None = None,
) -> int:
    normalized = normalize_urgency_type(
        urgency_type,
        turnaround_hours=turnaround_hours,
        turnaround_label=turnaround_label,
    )
    return PRIORITY_LEVELS.get(normalized, 1)


def calculate_urgency_adjustment(
    *,
    base_amount: Any,
    urgency_type: str | None,
    turnaround_hours: int | None = None,
    turnaround_label: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_urgency_type(
        urgency_type,
        turnaround_hours=turnaround_hours,
        turnaround_label=turnaround_label,
    )
    base = _money(base_amount)
    multiplier = URGENCY_MULTIPLIERS.get(normalized, Decimal("1.00"))
    if normalized == "standard" or base <= 0 or multiplier <= Decimal("1.00"):
        return {
            "urgency_type": normalized,
            "urgency_multiplier": str(multiplier),
            "urgency_fee": "0.00",
        }

    fee = (base * (multiplier - Decimal("1.00"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {
        "urgency_type": normalized,
        "urgency_multiplier": str(multiplier),
        "urgency_fee": str(fee),
    }


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, timezone.get_current_timezone())
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
    return None


def calculate_after_hours_adjustment(
    *,
    base_amount: Any,
    urgency_type: str | None,
    requested_deadline: Any = None,
    requested_delivery_time: Any = None,
    opening_time: time | None = None,
    closing_time: time | None = None,
) -> dict[str, Any]:
    normalized = normalize_urgency_type(urgency_type)
    base = _money(base_amount)
    if base <= 0:
        return {"after_hours_fee": "0.00", "is_after_hours": False}

    is_after_hours = normalized in {"after_hours", "emergency"}
    candidate = _coerce_datetime(requested_delivery_time) or _coerce_datetime(requested_deadline)
    if candidate is not None:
        start = opening_time or time(8, 0)
        end = closing_time or time(18, 0)
        candidate_time = candidate.timetz().replace(tzinfo=None)
        if candidate_time < start or candidate_time > end:
            is_after_hours = True

    if not is_after_hours:
        return {"after_hours_fee": "0.00", "is_after_hours": False}

    fee = (base * Decimal("0.08")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"after_hours_fee": str(fee), "is_after_hours": True}


def apply_priority_pricing(
    pricing_payload: dict[str, Any],
    *,
    urgency_type: str | None,
    turnaround_hours: int | None = None,
    turnaround_label: str | None = None,
    requested_deadline: Any = None,
    requested_delivery_time: Any = None,
    opening_time: time | None = None,
    closing_time: time | None = None,
) -> dict[str, Any]:
    payload = dict(pricing_payload or {})
    totals = dict(_as_dict(payload.get("totals")))
    calculation_result = dict(_as_dict(payload.get("calculation_result")))
    line_items = list(calculation_result.get("line_items") or [])

    base_total = _money(totals.get("grand_total") or totals.get("estimated_total") or totals.get("subtotal"))
    urgency = calculate_urgency_adjustment(
        base_amount=base_total,
        urgency_type=urgency_type,
        turnaround_hours=turnaround_hours,
        turnaround_label=turnaround_label,
    )
    after_hours = calculate_after_hours_adjustment(
        base_amount=base_total,
        urgency_type=urgency["urgency_type"],
        requested_deadline=requested_deadline,
        requested_delivery_time=requested_delivery_time,
        opening_time=opening_time,
        closing_time=closing_time,
    )
    urgency_fee = _money(urgency["urgency_fee"])
    after_hours_fee = _money(after_hours["after_hours_fee"])
    total_adjustment = urgency_fee + after_hours_fee

    if urgency_fee > 0:
        label = {
            "same_day": "Same-Day Turnaround",
            "express": "Priority Production",
            "after_hours": "Priority Production",
            "emergency": "Emergency Production",
        }.get(urgency["urgency_type"], "Priority Production")
        line_items.append({"label": label, "amount": str(urgency_fee), "formula": "operational capacity premium"})

    if after_hours_fee > 0:
        line_items.append({"label": "After-Hours Production", "amount": str(after_hours_fee), "formula": "off-hours operational premium"})

    if total_adjustment > 0:
        grand_total = base_total + total_adjustment
        if totals:
            _set_money(totals, "grand_total", grand_total)
            if "estimated_total" in totals:
                _set_money(totals, "estimated_total", grand_total)
        payload["total"] = str(grand_total)
        warnings = list(payload.get("warnings") or [])
        if urgency["urgency_type"] != "standard":
            warnings.append("Priority turnaround pricing reflects scarce production capacity and coordination pressure.")
        payload["warnings"] = list(dict.fromkeys(warnings))

    calculation_result["line_items"] = line_items
    payload["calculation_result"] = calculation_result
    payload["totals"] = totals
    payload["urgency_type"] = urgency["urgency_type"]
    payload["urgency_multiplier"] = urgency["urgency_multiplier"]
    payload["urgency_fee"] = urgency["urgency_fee"]
    payload["after_hours_fee"] = after_hours["after_hours_fee"]
    payload["requested_deadline"] = requested_deadline.isoformat() if isinstance(requested_deadline, datetime) else requested_deadline
    payload["requested_delivery_time"] = requested_delivery_time.isoformat() if isinstance(requested_delivery_time, datetime) else requested_delivery_time
    payload["operational_priority_level"] = determine_operational_priority(
        urgency_type=urgency["urgency_type"],
        turnaround_hours=turnaround_hours,
        turnaround_label=turnaround_label,
    )
    payload["priority_pricing"] = {
        "client_label": {
            "standard": "Standard Turnaround",
            "same_day": "Same-Day Turnaround",
            "express": "Priority Production",
            "after_hours": "After-Hours Production",
            "emergency": "Emergency Production",
        }.get(urgency["urgency_type"], "Standard Turnaround"),
        "urgency_fee": urgency["urgency_fee"],
        "after_hours_fee": after_hours["after_hours_fee"],
        "operational_priority_level": payload["operational_priority_level"],
    }
    return payload

