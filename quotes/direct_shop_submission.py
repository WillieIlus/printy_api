from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from accounts.services.broker_resolution import resolve_effective_broker
from accounts.services.roles import is_client
from accounts.services.system_accounts import is_system_account
from payments.services import create_payment_for_quote
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.choices import CalculatorDraftStatus, QuoteOfferStatus, QuoteStatus
from quotes.models import CalculatorDraft, ProductionOption, Quote, QuoteRequest
from quotes.services_workflow import _build_reference
from services.production_matching import build_direct_shop_pricing


class ExistingBrokerRequired(Exception):
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        super().__init__(payload.get("detail") or "This client already has an assigned print manager.")


@dataclass(frozen=True)
class DirectShopSubmissionResult:
    quote_request: QuoteRequest
    production_option: ProductionOption
    quote: Quote
    payment: Any


def _broker_payload(broker: User) -> dict[str, Any]:
    system_account = is_system_account(broker)
    return {
        "id": broker.id,
        "display_name": "Printy" if system_account else getattr(broker, "name", "") or getattr(broker, "email", "") or "Print Manager",
        "short_title": "Managed by Printy" if system_account else "Print Manager",
        "is_printy_fallback": bool(system_account),
        "support_email": "support@printy.ke" if system_account else None,
    }


def _existing_broker_payload(*, broker_id: int) -> dict[str, Any]:
    broker = User.objects.select_related("profile").get(pk=broker_id)
    return {
        "code": "existing_broker_required",
        "detail": "This client already has an assigned print manager.",
        "broker": _broker_payload(broker),
        "next_action": "continue_with_broker",
    }


def _draft_details(draft: CalculatorDraft) -> dict[str, Any]:
    return dict(draft.request_details_snapshot or {})


def _customer_name(*, client: User, details: dict[str, Any]) -> str:
    return str(details.get("customer_name") or "").strip() or getattr(client, "name", "") or getattr(client, "email", "") or "Client"


def _quote_snapshot(*, draft: CalculatorDraft, shop_row: dict[str, Any], details: dict[str, Any], client_price: Decimal) -> dict[str, Any]:
    return {
        "source": "direct_shop_submission",
        "direct_shop_intake": True,
        "draft_reference": draft.draft_reference,
        "calculator_inputs": draft.calculator_inputs_snapshot or {},
        "request_details": details,
        "selected_shop_ids": [draft.direct_intake_shop_id],
        "selected_shop": {
            "id": draft.direct_intake_shop_id,
            "slug": shop_row.get("shop_slug") or "",
            "name": shop_row.get("shop_name") or "",
        },
        "selected_shop_preview": shop_row,
        "customer_pricing": {
            "currency": shop_row.get("currency") or "KES",
            "final_client_price": str(client_price),
            "estimated_total": str(client_price),
        },
        "visibility": {
            "actor": "client",
            "topology_mode": "direct_shop",
            "exposes_internal_economics": False,
        },
    }


def _quote_response_snapshot(*, shop_row: dict[str, Any], client_price: Decimal) -> dict[str, Any]:
    total = str(client_price)
    return {
        "currency": shop_row.get("currency") or "KES",
        "pricing": {"grand_total": total},
        "totals": {"grand_total": total},
        "customer_pricing": {
            "currency": shop_row.get("currency") or "KES",
            "final_client_price": total,
            "estimated_total": total,
        },
        "payment_terms": "Pay through Printy before production starts.",
        "direct_shop_intake": True,
    }


@transaction.atomic
def _create_direct_shop_submission(*, draft: CalculatorDraft, client: User) -> DirectShopSubmissionResult:
    shop = draft.direct_intake_shop
    if shop is None or not shop.is_active:
        raise ValidationError("The selected shop is no longer available.")
    if not getattr(shop, "owner_id", None):
        raise ValidationError("The selected shop has no owner account.")

    details = _draft_details(draft)
    pricing = build_direct_shop_pricing(shop=shop, payload=draft.calculator_inputs_snapshot or {})
    shop_row = pricing["row"]
    split_payload = pricing["split"]
    if not shop_row.get("price_available"):
        raise ValidationError(shop_row.get("reason") or "This shop cannot price the request yet.")
    if split_payload is None:
        raise ValidationError("This shop cannot price the request yet.")
    production_cost = split_payload["production_cost"]
    broker_client_price = split_payload["broker_client_price"]
    client_price = split_payload["client_total"]

    quote_request = QuoteRequest.objects.create(
        shop=None,
        created_by=client,
        assigned_manager=shop.owner,
        customer_name=_customer_name(client=client, details=details),
        customer_email=str(details.get("customer_email") or getattr(client, "email", "") or ""),
        customer_phone=str(details.get("customer_phone") or ""),
        notes=str(details.get("notes") or ""),
        status=QuoteStatus.CLOSED,
        source_draft=draft,
        delivery_preference=str(details.get("delivery_preference") or ""),
        delivery_address=str(details.get("delivery_address") or ""),
        request_snapshot=_quote_snapshot(draft=draft, shop_row=shop_row, details=details, client_price=client_price),
    )
    quote_request.request_reference = _build_reference("QR", quote_request.id)
    quote_request.save(update_fields=["request_reference", "updated_at"])

    production_option = ProductionOption.objects.create(
        quote_request=quote_request,
        shop=shop,
        production_cost=production_cost,
        estimated_turnaround_hours=shop_row.get("turnaround_hours"),
        capacity_status=shop_row.get("price_status") or "",
        score=shop_row.get("score") or None,
        pricing_snapshot=shop_row,
        notes="Direct shop public intake submission.",
        created_by=shop.owner,
    )

    now = timezone.now()
    quote = Quote.objects.create(
        quote_request=quote_request,
        shop=shop,
        production_option=production_option,
        created_by=shop.owner,
        status=QuoteOfferStatus.ACCEPTED,
        total=client_price,
        sent_at=now,
        accepted_at=now,
        sent_to_client_at=now,
        sent_to_client_by=shop.owner,
        client_quote_status="sent",
        note="Direct shop quote prepared from the shop rate card.",
        response_snapshot=_quote_response_snapshot(shop_row=shop_row, client_price=client_price),
    )
    quote.quote_reference = _build_reference("Q", quote.id)
    quote.save(update_fields=["quote_reference", "updated_at"])

    split = create_quote_financial_split(
        quote=quote,
        production_cost=production_cost,
        broker_client_price=broker_client_price,
        production_option=production_option,
        policy=split_payload["policy"],
    )
    if split.shop_payout + split.broker_payout != split.client_total - split.printy_fee:
        raise ValidationError("Unexpected direct-shop financial split.")

    payment = create_payment_for_quote(quote=quote, payer=client)
    draft.status = CalculatorDraftStatus.SENT
    draft.save(update_fields=["status", "updated_at"])

    return DirectShopSubmissionResult(
        quote_request=quote_request,
        production_option=production_option,
        quote=quote,
        payment=payment,
    )


def submit_direct_shop_draft(*, draft: CalculatorDraft, client: User) -> DirectShopSubmissionResult:
    if not is_client(client):
        raise ValidationError("Only client accounts can submit direct-shop drafts.")
    if draft.user_id != client.id:
        raise ValidationError("You do not own this draft.")
    if draft.status != CalculatorDraftStatus.DRAFT:
        raise ValidationError("Only draft quote drafts can be submitted.")
    if draft.intake_mode != CalculatorDraft.INTAKE_MODE_DIRECT_SHOP or not draft.direct_intake_shop_id:
        raise ValidationError("This draft is not scoped to a direct shop.")

    broker_id = resolve_effective_broker(client)
    if broker_id is not None:
        raise ExistingBrokerRequired(_existing_broker_payload(broker_id=broker_id))

    return _create_direct_shop_submission(draft=draft, client=client)
