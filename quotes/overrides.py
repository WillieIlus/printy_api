"""Stored quote overrides + quote versions (law 2.1-2.4, 3.4).

Law 2.1: every manager adjustment (spoilage, paper substitution, markup, sheet
count) is stored on the job as a record with who, when, field, old value and new
value.
Law 2.2: role views are recomputed from the one canonical result plus these
overrides; no view keeps its own price or sheet copy.
Law 2.3: when an adjustment changes the buyer total/per-piece on an unaccepted
quote, the buyer view is flagged ``price_updated`` with the old/new totals and
the buyer must acknowledge before payment can start.
Law 2.4 / 3.4: an accepted or paid quote is never silently changed; a sent quote
is locked (a second send is rejected) and edits create a new version.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from quotes.choices import QuoteOfferStatus
from quotes.models import (
    BUYER_PRICE_OVERRIDE_FIELDS,
    QuoteOverride,
    QuoteOverrideField,
    QuoteRequest,
    QuoteVersion,
)

PAID_PAYMENT_STATUS = "paid"

VALID_OVERRIDE_FIELDS = frozenset(choice.value for choice in QuoteOverrideField)


class QuoteLockedError(ValidationError):
    """Raised when an adjustment targets an accepted or paid quote (law 2.4)."""


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def is_quote_locked(quote_request: QuoteRequest) -> bool:
    """True if the quote is accepted or has a paid payment (law 2.4/3.4)."""
    if quote_request.quotes.filter(status=QuoteOfferStatus.ACCEPTED).exists():
        return True
    return quote_request.quotes.filter(payments__status=PAID_PAYMENT_STATUS).exists()


def active_overrides(quote_request: QuoteRequest) -> list[QuoteOverride]:
    """Return the overrides on a job in effective order."""
    return list(quote_request.price_overrides.order_by("effective_at", "id"))


def _buyer_total(quote_request: QuoteRequest) -> Decimal | None:
    quote = quote_request.quotes.exclude(status=QuoteOfferStatus.PENDING).order_by(
        "-created_at", "-id"
    ).first()
    if quote is None:
        return None
    return _to_decimal(getattr(quote, "total", None))


def _extract_total(value: Any) -> Decimal | None:
    if isinstance(value, Mapping):
        return _to_decimal(value.get("total") or value.get("new_total"))
    return _to_decimal(value)


@transaction.atomic
def record_override(
    *,
    quote_request: QuoteRequest,
    field: str,
    new_value: Any,
    old_value: Any = None,
    user=None,
    reason: str = "",
    old_total: Any = None,
    new_total: Any = None,
    new_due_date=None,
    date_cause: str = "manager",
) -> QuoteOverride:
    """Store a manager adjustment on the job (law 2.1).

    When a manager change moves the due date, pass ``new_due_date`` and a
    non-silent "Date updated" revision is recorded on the same override
    (law 6.5).
    """
    field = str(field)
    if field not in VALID_OVERRIDE_FIELDS:
        raise ValidationError(f"Unknown override field '{field}'.")
    if is_quote_locked(quote_request):
        raise QuoteLockedError(
            "This quote is accepted or paid and can no longer be changed. "
            "Create a new quote version instead (law 2.4/3.4)."
        )
    override = QuoteOverride.objects.create(
        quote_request=quote_request,
        field=field,
        old_value=old_value,
        new_value=new_value,
        reason=reason or "",
        created_by=user,
    )
    if field in BUYER_PRICE_OVERRIDE_FIELDS:
        _flag_buyer_price_updated(quote_request, override, old_total, new_total)
    if new_due_date is not None:
        from jobs.timing_services import record_date_revision

        record_date_revision(
            quote_request=quote_request,
            new_date=new_due_date,
            cause=date_cause,
            reason=reason or f"Due date moved by {field} override",
            user=user,
            override=override,
        )
    return override


def _buyer_total_from_override(override: QuoteOverride, key: str) -> Decimal | None:
    value = getattr(override, f"{key}_value") or {}
    if isinstance(value, Mapping):
        return _to_decimal(value.get("total"))
    return None


def _flag_buyer_price_updated(
    quote_request: QuoteRequest,
    override: QuoteOverride,
    old_total: Any,
    new_total: Any,
) -> None:
    old_total = _to_decimal(old_total) or _to_decimal(
        (override.old_value or {}).get("total") if isinstance(override.old_value, Mapping) else override.old_value
    )
    new_total = _to_decimal(new_total) or _to_decimal(
        (override.new_value or {}).get("total") if isinstance(override.new_value, Mapping) else override.new_value
    )
    quote_request.price_updated = True
    if old_total is not None:
        quote_request.price_updated_old_total = old_total
    if new_total is not None:
        quote_request.price_updated_new_total = new_total
    quote_request.price_acknowledged_at = None
    quote_request.price_acknowledged_by = None
    quote_request.save(
        update_fields=[
            "price_updated",
            "price_updated_old_total",
            "price_updated_new_total",
            "price_acknowledged_at",
            "price_acknowledged_by",
            "updated_at",
        ]
    )


def acknowledge_price_update(*, quote_request: QuoteRequest, user) -> QuoteRequest:
    """Buyer acknowledges a price update so payment may proceed (law 2.3)."""
    quote_request.price_updated = False
    quote_request.price_acknowledged_at = timezone.now()
    quote_request.price_acknowledged_by = user
    quote_request.save(
        update_fields=[
            "price_updated",
            "price_acknowledged_at",
            "price_acknowledged_by",
            "updated_at",
        ]
    )
    return quote_request


def _merge_spoilage(current: Any, new_value: Any) -> Any:
    if isinstance(new_value, Mapping):
        merged = dict(current) if isinstance(current, Mapping) else {}
        merged.update(new_value)
        return merged
    return {"override": new_value}


def _apply_one(canonical: dict[str, Any], override: QuoteOverride) -> dict[str, Any]:
    field = override.field
    new_value = override.new_value
    result = dict(canonical)

    if field == QuoteOverrideField.SPOILAGE:
        result["spoilage"] = _merge_spoilage(result.get("spoilage"), new_value)
    elif field == QuoteOverrideField.SHEET_COUNT:
        if isinstance(new_value, Mapping):
            if "good_sheets" in new_value:
                result["good_sheets"] = new_value.get("good_sheets")
            if "sheets_billed" in new_value:
                result["sheets_billed"] = new_value.get("sheets_billed")
        else:
            result["sheets_billed"] = new_value
    elif field == QuoteOverrideField.MARKUP:
        amount = _to_decimal(new_value.get("amount")) if isinstance(new_value, Mapping) else _to_decimal(new_value)
        total = _to_decimal(new_value.get("total")) if isinstance(new_value, Mapping) else None
        if amount is not None:
            current_price = _to_decimal(result.get("price")) or Decimal("0")
            result["price"] = current_price + amount
            result["manager_margin"] = amount
        elif total is not None:
            result["price"] = total
    elif field == QuoteOverrideField.PAPER_SUBSTITUTION:
        if isinstance(new_value, Mapping):
            label = new_value.get("label")
            if label:
                spec = result.get("spec_summary")
                spec = dict(spec) if isinstance(spec, Mapping) else {}
                spec["paper"] = label
                result["spec_summary"] = spec
            delta = _to_decimal(new_value.get("price_delta"))
            total = _to_decimal(new_value.get("total"))
            if delta is not None:
                current_price = _to_decimal(result.get("price")) or Decimal("0")
                result["price"] = current_price + delta
            elif total is not None:
                result["price"] = total
        elif isinstance(new_value, str):
            spec = result.get("spec_summary")
            spec = dict(spec) if isinstance(spec, Mapping) else {}
            spec["paper"] = new_value
            result["spec_summary"] = spec
    return result


def apply_overrides_to_canonical(
    canonical: Mapping[str, Any],
    overrides: Iterable[QuoteOverride],
) -> dict[str, Any]:
    """Recompute the canonical result from the engine result + overrides (law 2.2).

    No role view keeps its own price or sheet copy: every view is projected from
    the result of this function.
    """
    result = dict(canonical)
    for override in overrides:
        result = _apply_one(result, override)
    return result


def create_quote_version(
    *,
    quote_request: QuoteRequest,
    quote=None,
    reason: str = "",
    user=None,
    snapshot: Mapping[str, Any] | None = None,
) -> QuoteVersion:
    """Create the next immutable version of a quote (law 3.4)."""
    last = quote_request.versions.aggregate(m=Max("version_number"))["m"] or 0
    total = getattr(quote, "total", None) if quote is not None else None
    per_piece = None
    if quote is not None:
        per_piece = getattr(quote, "unit_price", None) or getattr(quote, "per_piece", None)
    return QuoteVersion.objects.create(
        quote_request=quote_request,
        quote=quote,
        version_number=last + 1,
        total=_to_decimal(total),
        per_piece=_to_decimal(per_piece),
        snapshot=dict(snapshot) if snapshot else None,
        reason=reason or "",
        created_by=user,
    )


__all__ = [
    "QuoteLockedError",
    "VALID_OVERRIDE_FIELDS",
    "is_quote_locked",
    "active_overrides",
    "record_override",
    "acknowledge_price_update",
    "apply_overrides_to_canonical",
    "create_quote_version",
]
