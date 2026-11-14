"""Single source of role-based field exposure for the canonical pricing result.

Law 1.1-1.3:
- The engine produces ONE canonical result object per spec (see
  ``build_canonical_result``).
- Every role receives a projection of that result built from ONE per-role
  allowlist defined here (buyer, manager, printer, anonymous).
- Fields not on a role's allowlist must not appear in that role's response,
  payload, logs, or error messages. Hiding a field only in the UI is a
  violation.

No view or serializer may hand-pick pricing fields; they must call
``project_canonical`` (or ``build_canonical_result`` + ``project_canonical``).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from services.pricing.breakdown_projection import line_items_from_preview

_MONEY_QUANTIZER = Decimal("0.01")

ROLE_BUYER = "buyer"
ROLE_MANAGER = "manager"
ROLE_PRINTER = "printer"
ROLE_ANONYMOUS = "anonymous"

ROLES = (ROLE_BUYER, ROLE_MANAGER, ROLE_PRINTER, ROLE_ANONYMOUS)

# Every field the canonical result can carry. Anything not in this tuple is
# internal and must never be projected to any role.
CANONICAL_FIELDS = (
    "price",
    "per_piece",
    "price_range",
    "currency",
    "quantity",
    "can_calculate",
    "reason",
    "spec_summary",
    "ready_date",
    "delivery",
    "job_status",
    "current_holder",
    "press_sheet",
    "layout",
    "ups",
    "good_sheets",
    "spoilage",
    "sheets_billed",
    "method",
    "cost_breakdown",
    "cost_lines",
    "manager_margin",
    "fee_tier",
    "printer_quoted_price",
    "printer_payout",
    "printer_side_fee",
    # --- timeliness (law Part 4, 6.x field matrix) ------------------------
    "due_date",
    "current_stage",
    "stage_label",
    "stage_owner",
    "next_action",
    "stage_sla_hours",
    "time_remaining_minutes",
    "is_late",
    "lateness_minutes",
    "stage_timestamps",
    "revision_count",
    "due_date_updated",
    "due_date_updated_old",
    "due_date_updated_new",
    "due_date_acknowledged_at",
    "on_time_rate",
)

# Timing fields exposed to the buyer: due date, current stage, owner, next
# action and time remaining (law 6.4), plus the "Date updated" acknowledgement
# state (law 6.5).
_BUYER_TIMING_FIELDS = frozenset(
    {
        "due_date",
        "current_stage",
        "stage_label",
        "stage_owner",
        "next_action",
        "stage_sla_hours",
        "time_remaining_minutes",
        "is_late",
        "due_date_updated",
        "due_date_updated_old",
        "due_date_updated_new",
        "due_date_acknowledged_at",
    }
)

# The manager additionally sees per-stage SLAs, timestamps and revision history
# (law 6.7).
_MANAGER_TIMING_FIELDS = _BUYER_TIMING_FIELDS | frozenset(
    {"lateness_minutes", "stage_timestamps", "revision_count"}
)

# The printer sees the schedule and per-stage timestamps, never financial or
# reliability internals.
_PRINTER_TIMING_FIELDS = frozenset(
    {
        "due_date",
        "current_stage",
        "stage_label",
        "stage_owner",
        "next_action",
        "stage_sla_hours",
        "time_remaining_minutes",
        "is_late",
        "stage_timestamps",
    }
)

# The public calculator shows an on-time score only when the backend actually
# returns one (law 6.7); it is never invented client-side.
_ANONYMOUS_TIMING_FIELDS = frozenset({"on_time_rate"})

# --- Law 1.3 field matrix -------------------------------------------------
# BUYER: total price, per-piece price, spec summary, ready date, delivery cost,
# job status and current holder. Never imposition/press sheet/ups/good sheets/
# spoilage/sheets billed/cost breakdown/margin/fee tier/printer payout.
_BUYER_FIELDS = frozenset(
    {
        "price",
        "per_piece",
        "price_range",
        "currency",
        "quantity",
        "can_calculate",
        "reason",
        "spec_summary",
        "ready_date",
        "delivery",
        "job_status",
        "current_holder",
    }
) | _BUYER_TIMING_FIELDS

# MANAGER: everything the buyer sees plus the full production + financial view.
_MANAGER_FIELDS = _BUYER_FIELDS | _MANAGER_TIMING_FIELDS | frozenset(
    {
        "press_sheet",
        "layout",
        "ups",
        "good_sheets",
        "spoilage",
        "sheets_billed",
        "method",
        "cost_breakdown",
        "cost_lines",
        "manager_margin",
        "fee_tier",
        "printer_quoted_price",
        "printer_payout",
        "printer_side_fee",
    }
)

# PRINTER: spec summary, due date, delivery, and the production view plus own
# payout. Never buyer total/per-piece, manager margin, cost breakdown beyond
# own payout, or fee tier.
_PRINTER_FIELDS = frozenset(
    {
        "currency",
        "quantity",
        "can_calculate",
        "reason",
        "spec_summary",
        "ready_date",
        "delivery",
        "job_status",
        "current_holder",
        "press_sheet",
        "layout",
        "ups",
        "good_sheets",
        "spoilage",
        "sheets_billed",
        "method",
        "printer_quoted_price",
        "printer_payout",
    }
) | _PRINTER_TIMING_FIELDS

# ANONYMOUS: the buyer projection in its gated form (law 4.3): a price range or
# the default-spec price, plus the spec summary. Never imposition, sheet
# counts, cost breakdown, margin, fee tier, or payout.
_ANONYMOUS_FIELDS = frozenset(
    {
        "price",
        "per_piece",
        "price_range",
        "currency",
        "quantity",
        "can_calculate",
        "reason",
        "spec_summary",
        "ready_date",
        "delivery",
    }
) | _ANONYMOUS_TIMING_FIELDS

ROLE_ALLOWLISTS: dict[str, frozenset[str]] = {
    ROLE_BUYER: _BUYER_FIELDS,
    ROLE_MANAGER: _MANAGER_FIELDS,
    ROLE_PRINTER: _PRINTER_FIELDS,
    ROLE_ANONYMOUS: _ANONYMOUS_FIELDS,
}

# Fields that must never reach a buyer or anonymous payload (law 1.3). Used by
# tests and by assert_projection_is_clean().
BUYER_FORBIDDEN_FIELDS = frozenset(
    {
        "press_sheet",
        "layout",
        "ups",
        "good_sheets",
        "spoilage",
        "sheets_billed",
        "method",
        "cost_breakdown",
        "cost_lines",
        "manager_margin",
        "fee_tier",
        "printer_quoted_price",
        "printer_payout",
        "printer_side_fee",
    }
)

ANONYMOUS_FORBIDDEN_FIELDS = BUYER_FORBIDDEN_FIELDS


# Canonical account roles and Batch-6 actor roles -> projection role. The
# X-Printy-Active-Role header selects among the roles a user actually holds;
# an unauthenticated request is always anonymous.
_ACTOR_TO_ROLE = {
    "client": ROLE_BUYER,
    "buyer": ROLE_BUYER,
    "customer": ROLE_BUYER,
    "partner": ROLE_MANAGER,
    "broker": ROLE_MANAGER,
    "manager": ROLE_MANAGER,
    "admin": ROLE_MANAGER,
    "ops": ROLE_MANAGER,
    "super_admin": ROLE_MANAGER,
    "production": ROLE_PRINTER,
    "shop": ROLE_PRINTER,
    "printer": ROLE_PRINTER,
    "public": ROLE_ANONYMOUS,
    "anonymous": ROLE_ANONYMOUS,
}


def projection_role_for_actor(actor: str | None) -> str:
    """Map an account/actor role to the canonical projection role."""
    if not actor:
        return ROLE_ANONYMOUS
    return _ACTOR_TO_ROLE.get(str(actor).strip().lower(), ROLE_ANONYMOUS)


def allowlist_for(role: str | None) -> frozenset[str]:
    """Return the field allowlist for a role (anonymous is the safe default)."""
    if role in ROLE_ALLOWLISTS:
        return ROLE_ALLOWLISTS[role]  # type: ignore[index]
    return ROLE_ALLOWLISTS[ROLE_ANONYMOUS]


def project_canonical(canonical: Mapping[str, Any], role: str | None) -> dict[str, Any]:
    """Project a canonical result down to the fields a role may see."""
    allowed = allowlist_for(role)
    return {key: value for key, value in canonical.items() if key in allowed}


def assert_projection_is_clean(projected: Mapping[str, Any], role: str | None) -> None:
    """Raise ValueError if a projection leaks a field the role may not see."""
    leaked = set(projected) - allowlist_for(role)
    if leaked:
        raise ValueError(
            f"role '{role}' projection leaks forbidden fields: {sorted(leaked)}"
        )


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _build_spoilage(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    keys = (
        "fixed_waste_sheets",
        "variable_waste_sheets",
        "waste_sheets_added",
        "variable_waste_rate",
        "maximum_spoilage_rate",
        "maximum_spoilage_sheets",
        "spoilage_capped",
        "production_sheets",
        "max_billable_sheets",
    )
    spoilage = {key: payload.get(key) for key in keys if payload.get(key) is not None}
    return spoilage or None


def _build_cost_breakdown(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    breakdown = payload.get("breakdown") or {}
    if not isinstance(breakdown, Mapping):
        return None
    breakdown = dict(breakdown)
    totals = payload.get("totals") or {}
    cost = {
        "totals": dict(totals) if isinstance(totals, Mapping) else totals,
        "paper": breakdown.get("paper"),
        "printing": breakdown.get("printing"),
        "material": breakdown.get("material"),
        "dimensions": breakdown.get("dimensions"),
        "per_sheet_pricing": breakdown.get("per_sheet_pricing"),
        "finishings": breakdown.get("finishings"),
    }
    return {key: value for key, value in cost.items() if value is not None} or None


def _build_cost_lines(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Law 7.4a: itemized engine cost lines (label, quantity, unit rate, amount).

    Built from the one engine's cost components so the manager's printer option
    shows sheets (size/gsm/sides/finish), lamination, cutting and any other
    finishing, and the lines sum to the printer's quoted price.
    """
    preview = dict(payload)
    breakdown = payload.get("breakdown")
    if isinstance(breakdown, Mapping) and "breakdown" not in preview:
        preview["breakdown"] = breakdown
    lines = line_items_from_preview(preview)
    return lines or None


def _build_method(payload: Mapping[str, Any]) -> Any:
    breakdown = payload.get("breakdown") or {}
    if not isinstance(breakdown, Mapping):
        return None
    printing = breakdown.get("printing") or {}
    if isinstance(printing, Mapping) and printing:
        return {
            "machine_name": printing.get("machine_name"),
            "color_mode": printing.get("color_mode"),
            "sides": printing.get("sides"),
            "pricing_mode": payload.get("pricing_mode"),
        }
    material = breakdown.get("material") or {}
    if isinstance(material, Mapping) and material:
        return {
            "material_label": material.get("label"),
            "unit": material.get("unit"),
            "pricing_mode": payload.get("pricing_mode"),
        }
    return {"pricing_mode": payload.get("pricing_mode")}


# --- calculator role economics --------------------------------------------
# The public calculator prices one canonical market estimate. The same split
# (production cost + manager markup + Printy fee) is exposed to each role as a
# projection, never recomputed in the UI:
#   printer -> raw production lines + own payout
#   manager -> lines with the manager's markup + client-inclusive total
#   buyer/anonymous -> no lines at all (the inclusive price is the whole story)
CALCULATOR_MANAGER_FIELDS = frozenset(
    {
        "role",
        "currency",
        "markup_percent",
        "lines",
        "production_total",
        "markup_total",
        "manager_total",
        "printy_fee",
        "client_total_inclusive",
    }
)
CALCULATOR_PRINTER_FIELDS = frozenset(
    {
        "role",
        "currency",
        "lines",
        "production_total",
        "printer_payout",
    }
)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, InvalidOperation, TypeError, ValueError):
        return None


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def _money_text(value: Any) -> str:
    amount = _decimal(value)
    return str(_quantize(amount)) if amount is not None else "0.00"


def _line_value(line: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = line.get(key)
        if value not in (None, ""):
            return value
    return None


def _quantity_payload(value: Decimal | None) -> int | str | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return str(value.normalize())


def build_calculator_economics(
    *,
    cost_lines: Any,
    production_total: Any,
    manager_markup: Any,
    client_total: Any,
    printy_fee: Any,
    printer_payout: Any,
    currency: Any = "KES",
) -> dict[str, Any] | None:
    """Build the one canonical cost/markup/fee result for the calculator.

    The buyer estimate, the manager's billed lines and the printer's production
    lines all read from this object. Manager billed amounts are distributed
    across the production lines pro-rata so the lines still sum to the split.
    """
    production = _decimal(production_total)
    if production is None or production <= 0:
        return None
    production = _quantize(production)
    markup = _quantize(_decimal(manager_markup) or Decimal("0.00"))
    manager_total = _quantize(production + markup)

    normalized: list[dict[str, Any]] = []
    for line in cost_lines or []:
        if not isinstance(line, Mapping):
            continue
        total = _decimal(_line_value(line, ("total_kes", "total")))
        if total is None:
            continue
        total = _quantize(total)
        quantity = _decimal(_line_value(line, ("quantity", "qty")))
        rate = _decimal(_line_value(line, ("unit_price_kes", "unit_price", "rate")))
        if rate is None:
            rate = _quantize(total / quantity) if quantity and quantity > 0 else total
        normalized.append(
            {
                "label": str(line.get("label") or line.get("component") or "Production"),
                "spec": str(line.get("spec") or ""),
                "quantity": quantity,
                "unit": str(line.get("unit") or ""),
                "rate": _quantize(rate),
                "production_total": total,
            }
        )

    if not normalized:
        normalized.append(
            {
                "label": "Production",
                "spec": "",
                "quantity": None,
                "unit": "",
                "rate": production,
                "production_total": production,
            }
        )

    # The engine's item lines must reconcile with the split's production cost.
    # Any residual (rounding, a total component the line builder did not itemise)
    # rides on the final line rather than inventing an unexplained extra line.
    line_sum = sum((line["production_total"] for line in normalized), Decimal("0.00"))
    if line_sum != production:
        normalized[-1]["production_total"] = _quantize(
            normalized[-1]["production_total"] + (production - line_sum)
        )

    ratio = manager_total / production
    billed_sum = Decimal("0.00")
    for line in normalized:
        billed = _quantize(line["production_total"] * ratio)
        line["billed_total"] = billed
        billed_sum += billed
    if billed_sum != manager_total:
        normalized[-1]["billed_total"] = _quantize(
            normalized[-1]["billed_total"] + (manager_total - billed_sum)
        )
    for line in normalized:
        line["markup_amount"] = _quantize(line["billed_total"] - line["production_total"])

    markup_percent = (
        _quantize(markup / production * Decimal("100")) if production > 0 else Decimal("0.00")
    )
    return {
        "currency": str(currency or "KES"),
        "markup_percent": markup_percent,
        "lines": normalized,
        "production_total": production,
        "markup_total": markup,
        "manager_total": manager_total,
        "printy_fee": _quantize(_decimal(printy_fee) or Decimal("0.00")),
        "client_total_inclusive": _quantize(
            _decimal(client_total) if _decimal(client_total) is not None else manager_total
        ),
        "printer_payout": (
            _quantize(_decimal(printer_payout))
            if _decimal(printer_payout) is not None
            else None
        ),
    }


def project_calculator_economics(economics: Mapping[str, Any] | None, role: str | None) -> dict[str, Any] | None:
    """Project the canonical calculator economics onto a role's allowlist."""
    if role not in {ROLE_MANAGER, ROLE_PRINTER}:
        return None
    payload = economics or {}
    if not isinstance(payload, Mapping):
        return None
    production = _decimal(payload.get("production_total"))
    if production is None or production <= 0:
        return None
    lines = [line for line in (payload.get("lines") or []) if isinstance(line, Mapping)]
    if not lines:
        return None

    if role == ROLE_MANAGER:
        manager_lines = [
            {
                "label": line.get("label"),
                "spec": line.get("spec") or "",
                "quantity": _quantity_payload(_decimal(line.get("quantity"))),
                "unit": line.get("unit") or "",
                # Billed figures: what the manager charges, including markup.
                "rate": _money_text(
                    (_decimal(line.get("billed_total")) or Decimal("0.00"))
                    / (_decimal(line.get("quantity")) or Decimal("1.00"))
                ),
                "total": _money_text(line.get("billed_total")),
                # Production figures: what the printer charges.
                "production_rate": _money_text(line.get("rate")),
                "production_total": _money_text(line.get("production_total")),
                "markup_amount": _money_text(line.get("markup_amount")),
            }
            for line in lines
        ]
        result = {
            "role": ROLE_MANAGER,
            "currency": payload.get("currency") or "KES",
            "markup_percent": _money_text(payload.get("markup_percent")),
            "lines": manager_lines,
            "production_total": _money_text(payload.get("production_total")),
            "markup_total": _money_text(payload.get("markup_total")),
            "manager_total": _money_text(payload.get("manager_total")),
            "printy_fee": _money_text(payload.get("printy_fee")),
            "client_total_inclusive": _money_text(payload.get("client_total_inclusive")),
        }
        return {key: result[key] for key in result if key in CALCULATOR_MANAGER_FIELDS}

    printer_lines = [
        {
            "label": line.get("label"),
            "spec": line.get("spec") or "",
            "quantity": _quantity_payload(_decimal(line.get("quantity"))),
            "unit": line.get("unit") or "",
            "rate": _money_text(line.get("rate")),
            "total": _money_text(line.get("production_total")),
        }
        for line in lines
    ]
    result = {
        "role": ROLE_PRINTER,
        "currency": payload.get("currency") or "KES",
        "lines": printer_lines,
        "production_total": _money_text(payload.get("production_total")),
    }
    payout = _decimal(payload.get("printer_payout"))
    if payout is not None:
        result["printer_payout"] = _money_text(payout)
    return {key: result[key] for key in result if key in CALCULATOR_PRINTER_FIELDS}


def build_canonical_result(
    engine_payload: Mapping[str, Any],
    *,
    spec_summary: Any = None,
    ready_date: Any = None,
    delivery: Any = None,
    job_status: Any = None,
    current_holder: Any = None,
    financials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the one canonical result object from an engine payload.

    ``financials`` (manager margin, fee tier, printer payout, printer-side fee)
    is normally a persisted QuoteFinancialSplit, assembled at serialize time so
    the engine stays pure (law 1.1: the engine produces the canonical result of
    the calculation; role financials are attached from the stored split).
    """
    payload = dict(engine_payload)
    totals = payload.get("totals") or {}
    breakdown = payload.get("breakdown") or {}
    imposition = breakdown.get("imposition") or {} if isinstance(breakdown, Mapping) else {}
    paper = breakdown.get("paper") or {} if isinstance(breakdown, Mapping) else {}

    financials = financials or {}

    canonical: dict[str, Any] = {
        "price": _first(totals.get("grand_total"), totals.get("total_job_price")),
        "per_piece": totals.get("unit_price"),
        "currency": payload.get("currency") or "KES",
        "quantity": payload.get("quantity"),
        "can_calculate": payload.get("can_calculate", True),
        "reason": payload.get("reason", ""),
        "spec_summary": spec_summary,
        "ready_date": ready_date,
        "delivery": delivery,
        "job_status": job_status,
        "current_holder": current_holder,
        "press_sheet": _first(payload.get("parent_sheet_name"), paper.get("sheet_size")),
        "layout": imposition.get("layout"),
        "ups": payload.get("copies_per_sheet"),
        "good_sheets": payload.get("good_sheets"),
        "spoilage": _build_spoilage(payload),
        "sheets_billed": payload.get("billable_sheets"),
        "method": _build_method(payload),
        "cost_breakdown": _build_cost_breakdown(payload),
        # Law 7.4a: itemized cost lines, exposed to the manager projection only.
        "cost_lines": _build_cost_lines(payload),
        "manager_margin": _first(
            financials.get("manager_margin"),
            financials.get("markup_amount"),
            financials.get("broker_margin_amount"),
        ),
        "fee_tier": _first(
            financials.get("fee_tier"),
            financials.get("platform_service_percent"),
            financials.get("printy_fee"),
        ),
        # Law 7.10a: what the printer charges on their own rate card, BEFORE any
        # Printy printer-side fee is deducted and before any manager markup is
        # added. Distinct from printer_payout (rate card minus the printer-side
        # fee). For a raw engine payload with no persisted split this is the
        # VAT-exclusive rate-card subtotal.
        "printer_quoted_price": _first(
            financials.get("printer_quoted_price"),
            financials.get("production_cost"),
            totals.get("subtotal"),
        ),
        "printer_payout": _first(
            financials.get("printer_payout"),
            financials.get("shop_payout"),
        ),
        "printer_side_fee": _first(
            financials.get("printer_side_fee"),
            financials.get("platform_service_amount"),
        ),
    }
    return {key: canonical.get(key) for key in CANONICAL_FIELDS}


__all__ = [
    "ROLE_BUYER",
    "ROLE_MANAGER",
    "ROLE_PRINTER",
    "ROLE_ANONYMOUS",
    "ROLES",
    "CANONICAL_FIELDS",
    "ROLE_ALLOWLISTS",
    "BUYER_FORBIDDEN_FIELDS",
    "ANONYMOUS_FORBIDDEN_FIELDS",
    "allowlist_for",
    "project_canonical",
    "assert_projection_is_clean",
    "build_canonical_result",
    "build_calculator_economics",
    "project_calculator_economics",
    "CALCULATOR_MANAGER_FIELDS",
    "CALCULATOR_PRINTER_FIELDS",
    "projection_role_for_actor",
]
