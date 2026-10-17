"""Record real production costs/outcomes on a completed job (law 3.4 / 6.3).

Actuals are **never** inferred or fabricated: they are only written when a
caller supplies real figures, and they are appended to the append-only job
event log so the estimate a buyer accepted and the real cost of doing the work
both survive forever. The variance is derived, not guessed.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError

from jobs.models import JobEvent, ManagedJob

COMPLETION_ACTUALS_EVENT = "completion_actuals"
COMPLETION_STAGE = "completion"

_ACTUAL_INPUT_FIELDS = (
    "actual_production_cost",
    "actual_client_total",
    "material_cost",
    "material_quantity",
    "finishing_cost",
    "delivery_cost",
    "quantity_produced",
    "quantity_delivered",
    "billable_sheets",
    "spoilage_sheets",
    "reprints",
    "turnaround_hours",
    "delivered_at",
    "notes",
)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _money(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{value!r} is not a valid money amount.")


def _decimal_or_none(value: Any, field: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(f"{field} must be a number.")


def _int_or_none(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValidationError(f"{field} must be an integer.")


def estimate_for(managed_job: ManagedJob) -> dict[str, Any]:
    """The accepted estimate the job was created from (read-only)."""
    source_quote = getattr(managed_job, "source_quote", None)
    financials = getattr(source_quote, "financial_split", None) if source_quote else None
    if financials is not None:
        production_cost = financials.production_cost
        client_total = financials.client_total
    else:
        production_cost = None
        client_total = getattr(managed_job, "client_total", None)
    return {
        "production_cost": _money(production_cost),
        "client_total": _money(client_total),
    }


def _variance(actual: str | None, estimate: str | None) -> str | None:
    if actual is None or estimate is None:
        return None
    return _money(Decimal(actual) - Decimal(estimate))


def record_completion_actuals(
    *,
    managed_job: ManagedJob,
    recorded_by=None,
    actual_production_cost: Any = None,
    actual_client_total: Any = None,
    material_cost: Any = None,
    material_quantity: Any = None,
    material_unit: str = "",
    finishing_cost: Any = None,
    delivery_cost: Any = None,
    quantity_produced: Any = None,
    quantity_delivered: Any = None,
    billable_sheets: Any = None,
    spoilage_sheets: Any = None,
    reprints: Any = None,
    turnaround_hours: Any = None,
    stage_durations: dict | None = None,
    delivered_at=None,
    notes: str = "",
) -> JobEvent:
    """Append a completion-actuals event. Never fabricates; requires real data.

    Every figure is optional and stays ``null`` when unknown. A caller that
    supplies nothing real gets a ``ValidationError`` rather than a row of
    misleading zeros.
    """
    values = {
        "actual_production_cost": actual_production_cost,
        "actual_client_total": actual_client_total,
        "material_cost": material_cost,
        "material_quantity": material_quantity,
        "finishing_cost": finishing_cost,
        "delivery_cost": delivery_cost,
        "quantity_produced": quantity_produced,
        "quantity_delivered": quantity_delivered,
        "billable_sheets": billable_sheets,
        "spoilage_sheets": spoilage_sheets,
        "reprints": reprints,
        "turnaround_hours": turnaround_hours,
        "delivered_at": delivered_at,
        "notes": notes,
    }
    has_real_input = any(
        values[field] not in (None, "", {}) for field in _ACTUAL_INPUT_FIELDS
    ) or bool(stage_durations)
    if not has_real_input:
        raise ValidationError(
            "Refusing to record completion actuals with no real figures (never fabricate)."
        )

    estimate = estimate_for(managed_job)
    actual_production = _money(actual_production_cost)
    actual_client = _money(actual_client_total)
    metadata = {
        "actual_production_cost": actual_production,
        "actual_client_total": actual_client,
        "material_cost": _money(material_cost),
        "material_quantity": _decimal_or_none(material_quantity, "material_quantity"),
        "material_unit": (material_unit or "").strip() or None,
        "finishing_cost": _money(finishing_cost),
        "delivery_cost": _money(delivery_cost),
        "quantity_produced": _int_or_none(quantity_produced, "quantity_produced"),
        "quantity_delivered": _int_or_none(quantity_delivered, "quantity_delivered"),
        "billable_sheets": _int_or_none(billable_sheets, "billable_sheets"),
        "spoilage_sheets": _int_or_none(spoilage_sheets, "spoilage_sheets"),
        "reprints": _int_or_none(reprints, "reprints"),
        "turnaround_hours": _int_or_none(turnaround_hours, "turnaround_hours"),
        "stage_durations": _as_dict(stage_durations),
        "delivered_at": delivered_at.isoformat() if hasattr(delivered_at, "isoformat") else delivered_at,
        "notes": notes or "",
        "production_shop_id": managed_job.assigned_shop_id,
        "estimate": estimate,
        "variance": {
            "production_cost": _variance(actual_production, estimate["production_cost"]),
            "client_total": _variance(actual_client, estimate["client_total"]),
        },
    }
    return JobEvent.objects.create(
        managed_job=managed_job,
        stage=COMPLETION_STAGE,
        event=COMPLETION_ACTUALS_EVENT,
        actor=recorded_by,
        source=JobEvent.SOURCE_MANAGER if recorded_by is not None else JobEvent.SOURCE_SYSTEM,
        metadata=metadata,
    )


def completion_actuals_for(managed_job: ManagedJob) -> list[dict]:
    """All recorded completion-actuals payloads for a job, oldest first."""
    return [
        event.metadata
        for event in managed_job.job_events.filter(event=COMPLETION_ACTUALS_EVENT).order_by(
            "occurred_at", "id"
        )
    ]
