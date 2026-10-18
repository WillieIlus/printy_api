"""Stored quote-version snapshots (law 3.4 / 4 / 6.3 / 10.2).

A ``QuoteVersion`` is the write-once record of exactly what a buyer was shown,
including the pricing-rule version and product/spec fingerprint in force at the
moment it was offered. Historical versions are never recomputed from today's
rates: later pricing-rule changes only affect *new* offers.

This module is the single place that builds those snapshots and appends them.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from pricing.services.platform_fee_policy import get_active_platform_fee_policy
from quotes.spec_fingerprint import compute_spec_fingerprint
from services.pricing.result_contract import CONTRACT_VERSION

PRICING_RULES_SOURCE = "printy"

# Fee-policy fields that define the money split in force. Kept explicit (rather
# than reflecting all fields) so unrelated policy edits do not churn the version.
_POLICY_FIELDS = (
    "policy_version",
    "under_production_threshold",
    "under_production_fee_rate",
    "standard_production_fee_rate",
    "standard_markup_fee_rate",
    "high_markup_threshold",
    "high_markup_fee_rate",
    "high_production_threshold",
    "high_production_fee_rate",
    "high_production_markup_fee_rate",
    "maximum_manager_markup_multiple",
)


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def compute_pricing_rule_version() -> str:
    """Identify the pricing engine + active fee policy that produced an offer.

    Stored on every quote version and item pricing snapshot so a historical
    total can always be traced back to the rules that generated it (law 10.2).
    """
    policy = get_active_platform_fee_policy()
    policy_fingerprint = {
        field: _str(getattr(policy, field, "")) for field in _POLICY_FIELDS
    }
    payload = {
        "source": PRICING_RULES_SOURCE,
        "engine_contract": CONTRACT_VERSION,
        "policy": policy_fingerprint,
    }
    digest = _hash(payload)[:16]
    return f"{PRICING_RULES_SOURCE}-engine-v{CONTRACT_VERSION}-{digest}"


def _version_money_fields(quote) -> dict:
    """Law 10.2(a) terms in force for an offered quote version."""
    if quote is None:
        return {
            "printer": None,
            "printer_quoted_price": None,
            "manager_margin": None,
            "fees": None,
            "buyer_price": None,
            "due_date": None,
        }
    financials = getattr(quote, "financial_split", None)
    production_option = getattr(quote, "production_option", None)
    production_cost = getattr(financials, "production_cost", None)
    if production_cost is None and production_option is not None:
        production_cost = getattr(production_option, "production_cost", None)
    return {
        "printer": getattr(quote, "shop", None),
        "printer_quoted_price": production_cost,
        "manager_margin": getattr(financials, "manager_markup", None),
        "fees": getattr(financials, "printy_fee", None),
        "buyer_price": getattr(financials, "client_total", None)
        or getattr(quote, "total", None),
        "due_date": getattr(
            getattr(quote, "quote_request", None), "due_date", None
        )
        or getattr(quote, "estimated_ready_at", None),
    }


def _item_snapshots(quote_request) -> list[dict]:
    rows = []
    for item in quote_request.items.all().order_by("pk"):
        rows.append(
            {
                "id": item.pk,
                "title": getattr(item, "title", ""),
                "item_type": getattr(item, "item_type", ""),
                "product_id": getattr(item, "product_id", None),
                "quantity": getattr(item, "quantity", None),
                "unit_price": _str(getattr(item, "unit_price", None)),
                "line_total": _str(getattr(item, "line_total", None)),
                "item_spec_snapshot": _as_dict(getattr(item, "item_spec_snapshot", None)),
                "pricing_snapshot": _as_dict(getattr(item, "pricing_snapshot", None)),
            }
        )
    return rows


def _split_snapshot(quote) -> dict:
    financials = getattr(quote, "financial_split", None) if quote is not None else None
    if financials is None:
        return {}
    fields = (
        "production_cost",
        "manager_markup",
        "gross_margin",
        "printy_fee",
        "shop_payout",
        "manager_payout",
        "broker_payout",
        "broker_client_price",
        "client_total",
        "currency",
        "pricing_tier",
        "applied_policy_version",
        "applied_markup_multiple",
    )
    return {field: _str(getattr(financials, field, None)) for field in fields}


def build_quote_version_snapshot(*, quote_request, quote=None) -> dict:
    """Build the immutable snapshot payload for one offered version."""
    request_snapshot = _as_dict(getattr(quote_request, "request_snapshot", None))
    items = _item_snapshots(quote_request)
    split = _split_snapshot(quote)
    customer_pricing = _as_dict(request_snapshot.get("customer_pricing"))

    final_total = None
    if quote is not None and getattr(quote, "total", None) is not None:
        final_total = _str(quote.total)
    elif split.get("client_total") is not None:
        final_total = split["client_total"]
    elif customer_pricing.get("final_client_price") is not None:
        final_total = _str(customer_pricing.get("final_client_price"))

    terms = _version_money_fields(quote)
    return {
        "quote_request_id": quote_request.pk,
        "request_reference": getattr(quote_request, "request_reference", ""),
        "request_status": getattr(quote_request, "status", ""),
        "quote_id": getattr(quote, "pk", None),
        "quote_reference": getattr(quote, "quote_reference", ""),
        "quote_status": getattr(quote, "status", ""),
        "printer_id": getattr(terms["printer"], "pk", None),
        "printer_quoted_price": _str(terms["printer_quoted_price"]),
        "manager_margin": _str(terms["manager_margin"]),
        "fees": _str(terms["fees"]),
        "buyer_price": _str(terms["buyer_price"]),
        "due_date": terms["due_date"].isoformat() if hasattr(terms["due_date"], "isoformat") else terms["due_date"],
        "currency": split.get("currency") or customer_pricing.get("currency") or "KES",
        "spec_fingerprint": compute_spec_fingerprint(quote_request),
        "pricing_rule_version": compute_pricing_rule_version(),
        "engine_contract_version": CONTRACT_VERSION,
        "specification": request_snapshot.get("matched_specs")
        or request_snapshot.get("specification")
        or request_snapshot.get("spec"),
        "quantities": [
            {"item_id": row["id"], "quantity": row["quantity"]} for row in items
        ],
        "items": items,
        "delivery": {
            "preference": getattr(quote_request, "delivery_preference", ""),
            "address": getattr(quote_request, "delivery_address", ""),
            "location_id": request_snapshot.get("delivery_location_id"),
        },
        "pricing_calculation": _as_dict(request_snapshot.get("pricing_snapshot"))
        or customer_pricing,
        "financial_split": split,
        "final_total": final_total,
        "captured_at": timezone.now().isoformat(),
    }


@transaction.atomic
def record_quote_version(
    *,
    quote_request,
    quote=None,
    user=None,
    reason: str = "",
    outcome: str | None = None,
):
    """Append a new write-once version and supersede the previous open one.

    Editing a sent quote always produces a *new* version; the version the buyer
    already saw keeps its snapshot and is marked ``superseded`` (law 3.4).
    """
    from quotes.models import QuoteVersion, QuoteVersionOutcome

    if outcome is None:
        outcome = QuoteVersionOutcome.OPEN
    resolved_quote = quote
    if resolved_quote is None:
        resolved_quote = quote_request.get_latest_response()
    previous_open = QuoteVersion.objects.filter(
        quote_request=quote_request, outcome=QuoteVersionOutcome.OPEN
    )
    if resolved_quote is not None:
        previous_open = previous_open.filter(quote_id=resolved_quote.pk)
    for previous in previous_open:
        previous.outcome = QuoteVersionOutcome.SUPERSEDED
        previous.save(update_fields=["outcome", "updated_at"])

    snapshot = build_quote_version_snapshot(
        quote_request=quote_request, quote=resolved_quote
    )
    terms = _version_money_fields(resolved_quote)
    last_number = (
        QuoteVersion.objects.filter(quote_request=quote_request).aggregate(
            highest=Max("version_number")
        )["highest"]
        or 0
    )
    return QuoteVersion.objects.create(
        quote_request=quote_request,
        quote=resolved_quote,
        version_number=last_number + 1,
        total=getattr(resolved_quote, "total", None),
        snapshot=snapshot,
        reason=reason,
        outcome=outcome,
        spec_fingerprint=snapshot.get("spec_fingerprint", ""),
        pricing_rule_version=snapshot.get("pricing_rule_version", ""),
        printer=terms["printer"],
        printer_quoted_price=terms["printer_quoted_price"],
        manager_margin=terms["manager_margin"],
        fees=terms["fees"],
        buyer_price=terms["buyer_price"],
        due_date=terms["due_date"],
        created_by=user,
    )


def mark_quote_outcome(
    *,
    quote_request,
    outcome: str,
    quote=None,
    create_if_missing: bool = False,
    user=None,
    reason: str = "",
):
    """Close the latest version with a terminal outcome, preserving history.

    Existing versions are updated in place (only the outcome moves); when no
    version exists a new one is appended if ``create_if_missing`` is set so the
    accepted/rejected/expired terms are still captured (law 4 / 10.2).
    """
    from quotes.models import QuoteVersion

    versions = QuoteVersion.objects.filter(quote_request=quote_request)
    if quote is not None:
        versions = versions.filter(quote_id=quote.pk)
    version = versions.order_by("-version_number", "-id").first()
    if version is None:
        if not create_if_missing:
            return None
        return record_quote_version(
            quote_request=quote_request,
            quote=quote,
            user=user,
            reason=reason,
            outcome=outcome,
        )
    if version.outcome != outcome:
        version.outcome = outcome
        version.save(update_fields=["outcome", "updated_at"])
    return version
