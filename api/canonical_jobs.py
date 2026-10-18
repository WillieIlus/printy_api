"""One canonical job result, projected per role (law 1.1-1.3).

The pricing engine already produced one canonical result per spec; a job stores
that engine output (and the persisted financial split). This module assembles
the one canonical result object for a ``ManagedJob`` from those stored pieces,
applies the stored manager overrides (law 2.2), and projects it down to a
role's allowlist. Manager and printer job views render these projections; no
view keeps its own price, sheet or margin copy.
"""

from __future__ import annotations

from typing import Any, Mapping

from quotes.models import QuoteOverride
from quotes.overrides import active_overrides, apply_overrides_to_canonical
from services.pricing.role_projection import (
    ROLE_BUYER,
    ROLE_MANAGER,
    ROLE_PRINTER,
    build_canonical_result,
    project_canonical,
)

_SPOILAGE_KEYS = (
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


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (dict, list)) and not value:
            continue
        return value
    return None


def _sources(job: Any) -> list[dict[str, Any]]:
    """Ordered engine-payload sources, highest priority first.

    The engine output is stored inside JSON blobs: ``Quote.response_snapshot``
    (keys ``production_preview``, ``pricing``, ``totals``) and
    ``QuoteRequest.request_snapshot`` (keys ``production_preview_snapshot``,
    ``pricing_snapshot``/``pricing_preview_snapshot``, ``calculator_inputs``).
    """
    quote = getattr(job, "source_quote", None)
    request = getattr(job, "source_quote_request", None)
    response_snapshot = _as_dict(getattr(quote, "response_snapshot", None))
    request_snapshot = _as_dict(getattr(request, "request_snapshot", None))
    return [
        _as_dict(response_snapshot.get("production_preview")),
        _as_dict(request_snapshot.get("production_preview_snapshot")),
        _as_dict(response_snapshot.get("production")),
        _as_dict(response_snapshot.get("pricing_snapshot")),
        _as_dict(request_snapshot.get("pricing_snapshot")),
        _as_dict(request_snapshot.get("pricing_preview_snapshot")),
        response_snapshot,
        request_snapshot,
    ]


def _pick(sources: list[dict[str, Any]], key: str) -> Any:
    return _first(*[source.get(key) for source in sources])


def _nested_breakdown(sources: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source in reversed(sources):
        breakdown = _as_dict(source.get("breakdown"))
        if breakdown:
            merged.update(breakdown)
    return merged


def _financials_for_job(job: Any) -> dict[str, Any]:
    quote = getattr(job, "source_quote", None)
    split = getattr(quote, "financial_split", None)
    if split is None:
        from jobs.settlement_compat import get_financial_split_for_job

        return _as_dict(get_financial_split_for_job(job))
    return {
        "manager_margin": getattr(split, "gross_margin", None),
        "markup_amount": getattr(split, "manager_markup", None),
        "broker_margin_amount": getattr(split, "gross_margin", None),
        "fee_tier": getattr(split, "pricing_tier", None),
        "printy_fee": getattr(split, "printy_fee", None),
        "platform_service_amount": getattr(split, "printy_fee", None),
        "printer_payout": getattr(split, "shop_payout", None),
        "printer_side_fee": getattr(split, "printer_side_fee", None),
        "production_cost": getattr(split, "production_cost", None),
    }


def build_job_engine_payload(job: Any) -> dict[str, Any]:
    """Assemble the engine-payload shape ``build_canonical_result`` consumes."""
    quote = getattr(job, "source_quote", None)
    request = getattr(job, "source_quote_request", None)
    response_snapshot = _as_dict(getattr(quote, "response_snapshot", None))
    request_snapshot = _as_dict(getattr(request, "request_snapshot", None))
    calculator_inputs = _as_dict(request_snapshot.get("calculator_inputs"))
    sources = _sources(job)
    breakdown = _nested_breakdown(sources)
    paper = _as_dict(breakdown.get("paper"))
    imposition = _as_dict(breakdown.get("imposition"))
    printing = _as_dict(breakdown.get("printing"))
    quote_totals = _as_dict(response_snapshot.get("totals"))
    response_totals = quote_totals
    quote_pricing = _as_dict(response_snapshot.get("pricing"))
    quantity_pricing = _as_dict(response_snapshot.get("quantity_pricing_snapshot"))

    grand_total = _first(
        quote_totals.get("grand_total"),
        response_totals.get("grand_total"),
        quote_pricing.get("grand_total"),
        _pick(sources, "grand_total"),
    )
    unit_price = _first(
        quote_totals.get("unit_price"),
        response_totals.get("unit_price"),
        quote_pricing.get("unit_price"),
        _pick(sources, "unit_price"),
    )
    quantity = _first(_pick(sources, "quantity"), calculator_inputs.get("quantity"))
    currency = _first(
        _pick(sources, "currency"),
        quote_pricing.get("currency"),
        calculator_inputs.get("currency"),
        "KES",
    )

    payload: dict[str, Any] = {
        "totals": {
            "grand_total": grand_total,
            "unit_price": unit_price,
        },
        "breakdown": {
            "paper": paper,
            "imposition": imposition,
            "printing": printing,
            # Law 7.4a: keep finishing components so the manager's cost lines
            # cover lamination, cutting and any other finishing, not just the
            # sheet and print components.
            "finishings": breakdown.get("finishings") or [],
        },
        "quantity": quantity,
        "currency": currency,
        "can_calculate": _first(_pick(sources, "can_calculate"), True),
        "reason": _pick(sources, "reason") or "",
        "copies_per_sheet": _first(
            _pick(sources, "copies_per_sheet"),
            imposition.get("copies_per_sheet"),
        ),
        "good_sheets": _first(
            _pick(sources, "good_sheets"),
            imposition.get("good_sheets"),
            imposition.get("sheets_required"),
        ),
        "billable_sheets": _first(
            _pick(sources, "billable_sheets"),
            quantity_pricing.get("billable_sheets"),
        ),
        "parent_sheet_name": _first(
            _pick(sources, "parent_sheet_name"),
            paper.get("sheet_size"),
        ),
    }
    for key in _SPOILAGE_KEYS:
        payload[key] = _pick(sources, key)
    return payload


def _spec_summary(job: Any) -> dict[str, Any]:
    request = getattr(job, "source_quote_request", None)
    request_snapshot = _as_dict(getattr(request, "request_snapshot", None))
    calculator_inputs = _as_dict(request_snapshot.get("calculator_inputs"))
    summary = {
        "product_type": _first(calculator_inputs.get("product_type"), job.title),
        "quantity": _first(calculator_inputs.get("quantity"), request_snapshot.get("quantity")),
        "size_label": _first(
            calculator_inputs.get("size_label"),
            calculator_inputs.get("finished_size"),
        ),
        "paper": _first(
            calculator_inputs.get("paper_stock"),
            calculator_inputs.get("resolved_paper"),
        ),
        "print_sides": calculator_inputs.get("print_sides"),
        "color_mode": calculator_inputs.get("color_mode"),
        "finishing": calculator_inputs.get("lamination"),
    }
    return {key: value for key, value in summary.items() if value not in (None, "", [], {})}


def _current_holder(job: Any) -> str:
    shop = getattr(job, "assigned_shop", None)
    if shop is not None and getattr(shop, "name", ""):
        return str(shop.name)
    assignment_status = str(getattr(job, "assignment_status", "") or "").lower()
    if assignment_status and assignment_status not in {"unassigned", "pending"}:
        return "Print Partner"
    return "Print Manager"


def build_job_canonical(job: Any) -> dict[str, Any]:
    """Build the one canonical result for a job (before role projection)."""
    return build_canonical_result(
        build_job_engine_payload(job),
        spec_summary=_spec_summary(job),
        ready_date=getattr(job, "requested_deadline", None),
        delivery=_delivery_for_job(job),
        job_status=getattr(job, "status", None),
        current_holder=_current_holder(job),
        financials=_financials_for_job(job),
    )


def _delivery_for_job(job: Any) -> Any:
    request = getattr(job, "source_quote_request", None)
    request_snapshot = _as_dict(getattr(request, "request_snapshot", None))
    details = _as_dict(request_snapshot.get("request_details"))
    delivery = {
        "address": _first(details.get("delivery_address"), details.get("address")),
        "city": details.get("city"),
        "method": _first(details.get("delivery_method"), details.get("delivery_option")),
    }
    return {key: value for key, value in delivery.items() if value not in (None, "")} or None


def build_job_projection(job: Any, role: str) -> dict[str, Any]:
    """Project the job's canonical result to a role, applying stored overrides."""
    canonical = build_job_canonical(job)
    quote_request = getattr(job, "source_quote_request", None)
    if quote_request is not None:
        canonical = apply_overrides_to_canonical(canonical, active_overrides(quote_request))
    return project_canonical(canonical, role)


def preview_job_override(
    job: Any,
    *,
    field: str,
    new_value: Any,
) -> dict[str, Any]:
    """Return the buyer-facing price impact of a proposed adjustment (law 2.3).

    Nothing is persisted; the caller decides whether to record it.
    """
    canonical = build_job_canonical(job)
    quote_request = getattr(job, "source_quote_request", None)
    existing = active_overrides(quote_request) if quote_request is not None else []
    proposed = list(existing) + [QuoteOverride(field=field, new_value=new_value)]
    adjusted = apply_overrides_to_canonical(canonical, proposed)
    return _override_payload(canonical, adjusted, proposed)


def current_job_override_state(job: Any) -> dict[str, Any]:
    """Return the role projections with the stored overrides applied (law 2.2)."""
    canonical = build_job_canonical(job)
    quote_request = getattr(job, "source_quote_request", None)
    overrides = active_overrides(quote_request) if quote_request is not None else []
    adjusted = apply_overrides_to_canonical(canonical, overrides)
    return _override_payload(canonical, adjusted, overrides)


def _override_payload(canonical: Mapping[str, Any], adjusted: Mapping[str, Any], overrides) -> dict[str, Any]:
    old_price = canonical.get("price")
    new_price = adjusted.get("price")
    changed = old_price != new_price
    return {
        "buyer_price_impact": {
            "old_total": old_price,
            "new_total": new_price,
            "changed": changed,
        },
        "buyer": project_canonical(adjusted, ROLE_BUYER),
        "manager": project_canonical(adjusted, ROLE_MANAGER),
        "printer": project_canonical(adjusted, ROLE_PRINTER),
        "overrides": [
            {
                "field": getattr(override, "field", None),
                "new_value": getattr(override, "new_value", None),
            }
            for override in overrides
        ],
    }


__all__ = [
    "build_job_engine_payload",
    "build_job_canonical",
    "build_job_projection",
    "preview_job_override",
    "current_job_override_state",
]
