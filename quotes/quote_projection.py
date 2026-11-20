"""One read-only Quote projection over the legacy quote shapes.

Law 27: one vocabulary — Quote, not request/offer/response/draft.
This module adapts the existing models without renaming or removing anything:

* ``quotes.models.QuoteRequest``      (buyer request)
* ``quotes.models.Quote``             (manager/shop offer/response)
* ``quotes.models.CalculatorDraft``   (saved draft)

The projection is an envelope, like the job card: ``id`` + a ``quotes`` list
(length 1 today). Every legacy status maps to exactly one of Draft, Sent,
Accepted, Declined, Expired. The next action is computed here, server-side.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from accounts.services.roles import is_broker
from quotes.choices import CalculatorDraftStatus, QuoteOfferStatus, QuoteStatus
from quotes.models import CalculatorDraft, Quote, QuoteRequest
from services.pricing.spec_title import build_calculator_spec_text

CANONICAL_STATUSES: tuple[str, ...] = (
    "Draft",
    "Sent",
    "Accepted",
    "Declined",
    "Expired",
)

STATUS_KEYS = {
    "Draft": "draft",
    "Sent": "sent",
    "Accepted": "accepted",
    "Declined": "declined",
    "Expired": "expired",
}

_QUOTE_STATUS_MAP = {
    "pending": "Draft",
    "modified": "Sent",
    "sent": "Sent",
    "revised": "Sent",
    "accepted": "Accepted",
    "rejected": "Declined",
    "declined": "Declined",
    "expired": "Expired",
    "superseded": "Expired",
}

_REQUEST_STATUS_MAP = {
    "draft": "Draft",
    "submitted": "Sent",
    "viewed": "Sent",
    "awaiting_shop_action": "Sent",
    "awaiting_client_reply": "Sent",
    "quoted": "Sent",
    "accepted": "Accepted",
    "rejected": "Declined",
    "closed": "Declined",
    "cancelled": "Declined",
    "expired": "Expired",
}

_DRAFT_STATUS_MAP = {
    "draft": "Draft",
    "sent": "Sent",
    "archived": "Expired",
}


def canonical_status_for_quote(quote: Quote) -> str:
    if getattr(quote, "client_quote_status", "") == "superseded":
        return "Expired"
    if getattr(quote, "is_expired", False) and quote.status != QuoteOfferStatus.ACCEPTED:
        return "Expired"
    return _QUOTE_STATUS_MAP.get(str(quote.status or ""), "Draft")


def canonical_status_for_request(request: QuoteRequest) -> str:
    return _REQUEST_STATUS_MAP.get(str(request.status or ""), "Draft")


def canonical_status_for_draft(draft: CalculatorDraft) -> str:
    return _DRAFT_STATUS_MAP.get(str(draft.status or ""), "Draft")


def canonical_status_for_legacy(kind: str, raw_status: str | None) -> str:
    """Map any legacy raw status string, for adapters and tests."""
    key = str(raw_status or "")
    if kind == "quote":
        if key == "superseded":
            return "Expired"
        return _QUOTE_STATUS_MAP.get(key, "Draft")
    if kind == "request":
        return _REQUEST_STATUS_MAP.get(key, "Draft")
    if kind == "draft":
        return _DRAFT_STATUS_MAP.get(key, "Draft")
    return "Draft"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _request_for(source) -> QuoteRequest | None:
    if isinstance(source, Quote):
        return source.quote_request
    if isinstance(source, QuoteRequest):
        return source
    return None


def _calculator_inputs(source) -> dict[str, Any]:
    if isinstance(source, CalculatorDraft):
        return _as_dict(source.calculator_inputs_snapshot)
    request = _request_for(source)
    if request is None:
        return {}
    snapshot = _as_dict(request.request_snapshot)
    inputs = snapshot.get("calculator_inputs")
    if isinstance(inputs, dict):
        return inputs
    return {}


def _product_word(value: Any) -> str:
    text = str(value or "").strip().replace("_", " ").replace("-", " ")
    return " ".join(text.split()).title()


def _spec_for(source) -> dict[str, Any]:
    inputs = _calculator_inputs(source)
    request = _request_for(source)
    first_item = request.items.first() if request is not None else None
    product = (
        inputs.get("product_type")
        or inputs.get("product_name")
        or inputs.get("product_label")
        or getattr(first_item, "title", "")
    )
    quantity = inputs.get("quantity") or getattr(first_item, "quantity", None)
    words = build_calculator_spec_text(inputs, fallback="")
    head_parts = []
    if quantity:
        head_parts.append(str(quantity))
    if product:
        head_parts.append(_product_word(product))
    head = " ".join(head_parts).strip()
    summary = " · ".join(part for part in (head, words) if part)
    if not summary and first_item is not None:
        summary = str(getattr(first_item, "spec_text", "") or getattr(first_item, "title", "") or "").strip()
    return {
        "summary": summary or "Print quote",
        "product": _product_word(product),
        "quantity": int(quantity) if str(quantity or "").isdigit() else None,
    }


def _money(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except Exception:
        return None


def _snapshot_price(*values: Any) -> Any:
    for value in values:
        if isinstance(value, dict):
            for key in (
                "client_total",
                "final_client_price",
                "grand_total",
                "estimated_total",
                "total",
                "price",
            ):
                if value.get(key) not in (None, ""):
                    return value[key]
        elif value not in (None, ""):
            return value
    return None


def _quote_client_total(quote: Quote) -> Any:
    split = getattr(quote, "financial_split", None)
    if split is not None:
        return getattr(split, "client_total", None)
    request = quote.quote_request
    snapshot = _as_dict(getattr(request, "request_snapshot", None))
    return _snapshot_price(
        _as_dict(snapshot.get("pricing_snapshot")),
        _as_dict(snapshot.get("pricing_preview_snapshot")),
        _as_dict(quote.response_snapshot).get("customer_pricing"),
        quote.total,
    )


def _quote_printer_payout(quote: Quote) -> Any:
    split = getattr(quote, "financial_split", None)
    if split is not None:
        return getattr(split, "shop_payout", None)
    option = getattr(quote, "production_option", None)
    return getattr(option, "production_cost", None)


def _price_for(source, viewer_role: str) -> dict[str, Any] | None:
    if isinstance(source, Quote):
        if viewer_role == "printer":
            amount = _quote_printer_payout(source)
            label = "Your payout"
        else:
            amount = _quote_client_total(source)
            label = "Client total"
        currency = (
            getattr(getattr(source, "financial_split", None), "currency", None)
            or "KES"
        )
    elif isinstance(source, QuoteRequest):
        snapshot = _as_dict(source.request_snapshot)
        amount = _snapshot_price(
            _as_dict(snapshot.get("pricing_snapshot")),
            _as_dict(snapshot.get("pricing_preview_snapshot")),
        )
        label = "Estimated total"
        currency = "KES"
    elif isinstance(source, CalculatorDraft):
        amount = _snapshot_price(_as_dict(source.pricing_snapshot))
        label = "Estimated total"
        currency = "KES"
    else:
        amount, label, currency = None, "Total", "KES"
    if amount is None:
        return None
    return {"amount": _money(amount), "currency": currency, "label": label}


def _manager_for(source):
    request = _request_for(source)
    if request is None:
        return None
    manager = getattr(request, "assigned_manager", None) or getattr(request, "created_by_manager", None)
    if manager is not None:
        return manager
    if isinstance(source, Quote) and source.created_by is not None and is_broker(source.created_by):
        return source.created_by
    return None


def _client_name(source) -> str:
    request = _request_for(source)
    if request is None:
        return "Client"
    return (
        getattr(request, "customer_name", "")
        or getattr(getattr(request, "created_by", None), "name", "")
        or getattr(getattr(request, "on_behalf_of", None), "name", "")
        or "Client"
    )


def _manager_name(source) -> str:
    manager = _manager_for(source)
    if manager is None:
        return "Printy"
    return getattr(manager, "name", "") or getattr(manager, "email", "") or "Your manager"


def _shop_for(source):
    if isinstance(source, Quote):
        return source.shop
    if isinstance(source, QuoteRequest):
        return source.shop
    if isinstance(source, CalculatorDraft):
        return source.direct_intake_shop
    return None


def _from_to(source, viewer_role: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(source, CalculatorDraft):
        return (
            {"role": "buyer", "name": "You" if viewer_role == "buyer" else "Client"},
            {"role": "none", "name": "Draft"},
        )
    if isinstance(source, QuoteRequest):
        if viewer_role == "buyer":
            return (
                {"role": "buyer", "name": "You"},
                {"role": "manager", "name": _manager_name(source)},
            )
        if viewer_role == "manager":
            return (
                {"role": "buyer", "name": _client_name(source)},
                {"role": "manager", "name": "You"},
            )
        if viewer_role == "printer":
            return (
                {"role": "manager", "name": _manager_name(source)},
                {"role": "printer", "name": "Your shop"},
            )
        return (
            {"role": "buyer", "name": _client_name(source)},
            {"role": "manager", "name": _manager_name(source)},
        )
    # Quote
    if viewer_role == "buyer":
        return (
            {"role": "manager", "name": _manager_name(source)},
            {"role": "buyer", "name": "You"},
        )
    if viewer_role == "manager":
        return (
            {"role": "manager", "name": "You"},
            {"role": "buyer", "name": _client_name(source)},
        )
    if viewer_role == "printer":
        return (
            {"role": "manager", "name": _manager_name(source)},
            {"role": "printer", "name": "Your shop"},
        )
    return (
        {"role": "manager", "name": _manager_name(source)},
        {"role": "buyer", "name": _client_name(source)},
    )


def _action(code: str, label: str, actor: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"code": code, "label": label, "actor": actor, "params": params}


def resolve_quote_next_action(
    *,
    status: str,
    viewer_role: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """One next action for a quote, role-aware. No money is computed here."""
    if viewer_role == "admin":
        return _action(code="none", label="No action needed", actor="none", params=params)
    if viewer_role == "printer":
        return _action(code="none", label="No action needed", actor="none", params=params)

    if status == "Draft":
        if viewer_role == "buyer":
            return _action("continue_quote", "Continue your quote", "buyer", params)
        if viewer_role == "manager":
            return _action("prepare_quote", "Prepare and send the quote", "manager", params)
    if status == "Sent":
        if viewer_role == "buyer":
            return _action("review_quote", "Review and accept the quote", "buyer", params)
        if viewer_role == "manager":
            return _action("none", "Waiting on the client", "buyer", params)
    if status == "Accepted":
        if viewer_role == "buyer":
            return _action("pay_quote", "Pay into escrow", "buyer", params)
        if viewer_role == "manager":
            return _action("none", "Waiting on payment", "buyer", params)
    if status == "Declined":
        if viewer_role == "buyer":
            return _action("start_quote", "Start a new quote", "buyer", params)
        if viewer_role == "manager":
            return _action("revise_quote", "Send a revised quote", "manager", params)
    if status == "Expired":
        if viewer_role == "buyer":
            return _action("start_quote", "Start a new quote", "buyer", params)
        if viewer_role == "manager":
            return _action("revise_quote", "Send a fresh quote", "manager", params)
    return _action("none", "No action needed", "none", params)


def _base_item(source, viewer_role: str) -> dict[str, Any]:
    if isinstance(source, Quote):
        kind, pk = "quote", source.pk
        request = source.quote_request
        code = source.quote_reference or f"Q-{source.pk}"
        params = {"quote_id": source.pk, "request_id": request.pk}
        status = canonical_status_for_quote(source)
        raw_status = source.status
    elif isinstance(source, QuoteRequest):
        kind, pk = "request", source.pk
        code = source.request_reference or f"QR-{source.pk}"
        params = {"request_id": source.pk}
        status = canonical_status_for_request(source)
        raw_status = source.status
    else:
        kind, pk = "draft", source.pk
        code = source.draft_reference or f"DRAFT-{source.pk}"
        params = {"draft_id": source.pk}
        status = canonical_status_for_draft(source)
        raw_status = source.status

    from_role, to_role = _from_to(source, viewer_role)
    item: dict[str, Any] = {
        "id": f"{kind}:{pk}",
        "legacy_type": kind,
        "legacy_id": pk,
        "code": code,
        "spec": _spec_for(source),
        "price": _price_for(source, viewer_role),
        "from": from_role,
        "to": to_role,
        "status": status,
        "status_key": STATUS_KEYS[status],
        "next_action": resolve_quote_next_action(
            status=status,
            viewer_role=viewer_role,
            params=params,
        ),
    }

    if viewer_role in {"manager", "admin"}:
        shop = _shop_for(source)
        if shop is not None:
            item["shop"] = {"id": shop.id, "name": shop.name}
        request = _request_for(source)
        if request is not None:
            item["client"] = {"name": _client_name(source)}
    if viewer_role == "printer":
        shop = _shop_for(source)
        if shop is not None:
            item["shop"] = {"id": shop.id, "name": shop.name}
    if viewer_role == "admin":
        item["internal_status"] = raw_status
    return item


def build_quote_card(source, *, viewer_role: str) -> dict[str, Any]:
    kind = "quote" if isinstance(source, Quote) else "request" if isinstance(source, QuoteRequest) else "draft"
    return {
        "id": f"quote-card-{kind}-{source.pk}",
        "viewer": {"role": viewer_role},
        "quotes": [_base_item(source, viewer_role)],
    }


__all__ = [
    "CANONICAL_STATUSES",
    "STATUS_KEYS",
    "canonical_status_for_quote",
    "canonical_status_for_request",
    "canonical_status_for_draft",
    "canonical_status_for_legacy",
    "resolve_quote_next_action",
    "build_quote_card",
]
