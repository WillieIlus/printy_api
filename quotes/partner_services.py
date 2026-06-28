"""Partner quote builder helpers on top of the existing quote workflow."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from api.visibility import TOPOLOGY_MANAGED
from pricing.services.platform_fee_policy import calculate_financial_split, create_quote_financial_split
from pricing.services.production_cost_calculator import calculate_client_price_with_waste_setup_and_quantity_tier
from quotes.choices import QuoteStatus, QuoteOfferStatus
from quotes.guardrails import build_partner_markup_warning, calculate_quote_expiry, validate_partner_markup_amount
from quotes.models import QuoteRequest, Quote
from quotes.services_workflow import _build_reference, create_quote_response
from services.pricing.projections import project_broker_projection
from shops.models import Shop


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _money(value: Any, default: str = "0") -> Decimal:
    try:
        if value in (None, ""):
            return Decimal(default)
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _extract_shop_preview(pricing_snapshot: dict[str, Any], shop: Shop) -> dict[str, Any]:
    for entry in pricing_snapshot.get("selected_shops") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("id") == shop.id or entry.get("slug") == shop.slug:
            return entry
    return pricing_snapshot


def _extract_production_cost_spec(*, pricing_snapshot: dict[str, Any], shop_preview: dict[str, Any], calculator_inputs_snapshot: dict[str, Any] | None = None) -> dict[str, Any] | None:
    calculator_inputs_snapshot = calculator_inputs_snapshot or {}
    candidates = [
        shop_preview.get("production_cost_inputs"),
        shop_preview.get("production_cost_spec"),
        _as_dict(shop_preview.get("preview")).get("production_cost_inputs"),
        _as_dict(shop_preview.get("preview")).get("production_cost_spec"),
        pricing_snapshot.get("production_cost_inputs"),
        pricing_snapshot.get("production_cost_spec"),
        calculator_inputs_snapshot.get("production_cost_inputs"),
        calculator_inputs_snapshot.get("production_cost_spec"),
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        required = {"quantity", "yield_per_sheet", "paper_cost_per_sheet", "click_charge_per_sheet"}
        if required.issubset(candidate.keys()):
            return candidate
    return None


def _canonical_pricing_payload(*, pricing_snapshot: dict[str, Any], shop: Shop, calculator_inputs_snapshot: dict[str, Any] | None = None) -> dict[str, Any] | None:
    shop_preview = _extract_shop_preview(pricing_snapshot, shop)
    spec = _extract_production_cost_spec(
        pricing_snapshot=pricing_snapshot,
        shop_preview=shop_preview,
        calculator_inputs_snapshot=calculator_inputs_snapshot,
    )
    if not spec:
        return None
    return calculate_client_price_with_waste_setup_and_quantity_tier(spec)


def build_partner_quote_preview(*, pricing_snapshot: dict[str, Any], shop: Shop, partner_markup: Decimal) -> dict[str, Any]:
    shop_preview = _extract_shop_preview(pricing_snapshot, shop)
    raw_payload = _as_dict(shop_preview.get("preview")) or shop_preview
    broker_projection = project_broker_projection(raw_payload)
    canonical_pricing = _canonical_pricing_payload(pricing_snapshot=pricing_snapshot, shop=shop)
    production_estimate = (
        _money(canonical_pricing["production_cost"])
        if canonical_pricing
        else _money(broker_projection.get("production_estimate"))
    )
    broker_client_price = (
        _money(canonical_pricing["final_client_price"])
        if canonical_pricing
        else production_estimate + partner_markup
    )
    minimum_price = broker_client_price if canonical_pricing else production_estimate
    # TODO(batch-6): fallback preview-only range guidance, not authoritative split math.
    suggested_max = broker_client_price if canonical_pricing else production_estimate + max(partner_markup, production_estimate * Decimal("0.35"))
    split = _financial_split_payload(production_base_price=production_estimate, broker_client_price=broker_client_price)
    broker_projection.update(
        {
            "production_estimate": str(production_estimate.quantize(Decimal("0.01"))),
            "suggested_selling_range": {
                "min": str(minimum_price.quantize(Decimal("0.01"))),
                "max": str(suggested_max.quantize(Decimal("0.01"))),
            },
            "gross_margin": str(split["gross_margin"]),
            "printy_fee": str(split["printy_fee"]),
            "broker_client_price": str(split["broker_client_price"]),
            "client_price": str(split["client_total"]),
            "broker_payout": str(split["broker_payout"]),
            "quantity_pricing": _quantity_pricing_snapshot(canonical_pricing) if canonical_pricing else None,
            "markup_warning": build_partner_markup_warning(
                base_price=production_estimate,
                markup_amount=split["gross_margin"],
            ),
        }
    )
    return broker_projection


def validate_partner_markup(*, pricing_snapshot: dict[str, Any], shop: Shop, partner_markup: Decimal) -> None:
    preview = build_partner_quote_preview(pricing_snapshot=pricing_snapshot, shop=shop, partner_markup=partner_markup)
    production_estimate = _money(preview.get("production_estimate"))
    validate_partner_markup_amount(base_price=production_estimate, markup_amount=partner_markup)


def _financial_split_payload(*, production_base_price: Decimal, broker_client_price: Decimal | None = None, partner_markup: Decimal | None = None) -> dict[str, Decimal]:
    if broker_client_price is None:
        broker_client_price = production_base_price + (partner_markup or Decimal("0.00"))
    split = calculate_financial_split(
        production_cost=production_base_price,
        broker_client_price=broker_client_price,
    )
    return {
        **split,
        "gross_margin_percent": (
            (split["gross_margin"] / production_base_price) * Decimal("100")
        ).quantize(Decimal("0.01")) if production_base_price > 0 else Decimal("0.00"),
    }


def _quantity_pricing_snapshot(payload: dict[str, Any] | None) -> dict[str, str | int | None] | None:
    if not payload:
        return None
    tier = payload.get("quantity_pricing_tier")
    return {
        "raw_sheets": payload.get("raw_sheets"),
        "fixed_waste_sheets": payload.get("fixed_waste_sheets"),
        "variable_waste_sheets": payload.get("variable_waste_sheets"),
        "waste_sheets_added": payload.get("waste_sheets_added"),
        "total_sheets_needed": payload.get("total_sheets_needed"),
        "billable_sheets": payload.get("billable_sheets"),
        "material_cost": str(payload.get("material_cost")),
        "click_cost": str(payload.get("click_cost")),
        "setup_cost": str(payload.get("setup_cost")),
        "finishing_cost": str(payload.get("finishing_cost")),
        "production_cost": str(payload.get("production_cost")),
        "volume_multiplier": str(payload.get("volume_multiplier")),
        "calculated_client_price": str(payload.get("calculated_client_price")),
        "minimum_order_floor": str(payload.get("minimum_order_floor")),
        "final_client_price": str(payload.get("final_client_price")),
        "waste_policy_id": getattr(payload.get("waste_policy"), "id", None),
        "setup_policy_id": getattr(payload.get("setup_policy"), "id", None),
        "quantity_pricing_tier_id": getattr(tier, "id", None),
    }


def _public_response_snapshot(*, pricing_snapshot: dict[str, Any], partner_brand_name: str, split: dict[str, Decimal], note: str, white_label_mode: bool = False) -> dict[str, Any]:
    client_total = str(split["client_total"])
    return {
        "currency": pricing_snapshot.get("currency") or "KES",
        "partner_brand_name": partner_brand_name,
        "white_label_mode": white_label_mode,
        "customer_pricing": {
            "currency": pricing_snapshot.get("currency") or "KES",
            "final_client_price": client_total,
            "estimated_total": client_total,
        },
        "pricing": {
            "grand_total": client_total,
        },
        "totals": {
            "grand_total": client_total,
        },
        "payment_terms": "Pay through Printy before production starts.",
        "note": note,
    }


def _internal_pricing_snapshot(*, split: dict[str, Decimal]) -> dict[str, str]:
    return {
        "production_cost": str(split["production_cost"]),
        "gross_margin": str(split["gross_margin"]),
        "printy_fee": str(split["printy_fee"]),
        "printer_side_fee": str(split["printer_side_fee"]),
        "broker_margin_fee": str(split["broker_margin_fee"]),
        "broker_payout": str(split["broker_payout"]),
        "shop_payout": str(split["shop_payout"]),
        "client_total": str(split["client_total"]),
        "gross_margin_percent": str(split["gross_margin_percent"]),
        "policy_used_id": str(split["policy"].id),
    }


def _persist_quote_split(*, quote, split: dict[str, Decimal]):
    if getattr(quote, "financial_split", None):
        return quote.financial_split
    return create_quote_financial_split(
        quote=quote,
        production_cost=split["production_cost"],
        broker_client_price=split["broker_client_price"],
        policy=split["policy"],
    )


def get_or_create_partner_customer(
    *,
    shop: Shop,
    partner_user,
    client_name: str,
    client_email: str = "",
    client_phone: str = "",
) -> None:
    # TODO(batch-3): replace with canonical customer/account attribution model in later batch.
    return None


def _build_partner_request_snapshot(
    *,
    shop: Shop,
    calculator_inputs_snapshot: dict[str, Any],
    pricing_snapshot: dict[str, Any],
    partner_user,
    partner_brand_name: str,
    partner_markup: Decimal,
    client_name: str,
    client_email: str,
    client_phone: str,
    client_company: str,
    client_user=None,
) -> dict[str, Any]:
    return {
        "source": "partner_quote_builder",
        "quote_source": "partner_quote_builder",
        "calculator_inputs": calculator_inputs_snapshot,
        "pricing_snapshot": pricing_snapshot,
        "request_details": {
            "customer_name": client_name,
            "customer_email": client_email,
            "customer_phone": client_phone,
            "client_company": client_company,
        },
        "selected_shop_ids": [shop.id],
        "selected_shop_preview": {
            "id": shop.id,
            "slug": shop.slug,
            "name": shop.name,
        },
        "partner_brand_name": partner_brand_name,
        "white_label_mode": True,
        "partner_markup": str(partner_markup.quantize(Decimal("0.01"))),
        "relationship_owner_type": "user",
        "relationship_owner_user_id": partner_user.id,
        "topology_mode": TOPOLOGY_MANAGED,
        "visibility": {
            "actor": "client",
            "topology_mode": TOPOLOGY_MANAGED,
            "exposes_internal_economics": False,
        },
        "pending_client": {
            "client_user_id": getattr(client_user, "id", None),
            "name": client_name,
            "email": client_email,
            "phone": client_phone,
            "company": client_company,
        },
    }


@transaction.atomic
def create_partner_quote(
    *,
    partner_user,
    shop: Shop,
    client_user=None,
    client_name: str,
    client_email: str = "",
    client_phone: str = "",
    client_company: str = "",
    calculator_inputs_snapshot: dict[str, Any],
    pricing_snapshot: dict[str, Any],
    partner_markup: Decimal,
    title: str = "",
    note: str = "",
    save_as_draft: bool = False,
) -> dict[str, Any]:
    validate_partner_markup(pricing_snapshot=pricing_snapshot, shop=shop, partner_markup=partner_markup)
    preview = build_partner_quote_preview(
        pricing_snapshot=pricing_snapshot,
        shop=shop,
        partner_markup=partner_markup,
    )
    production_base_price = _money(preview.get("production_estimate"))
    broker_client_price = _money(preview.get("broker_client_price"), default=str(production_base_price + partner_markup))
    split = _financial_split_payload(production_base_price=production_base_price, broker_client_price=broker_client_price)
    partner_brand_name = getattr(partner_user, "name", "") or getattr(partner_user, "email", "") or "Partner"
    if client_name or client_email or client_phone:
        get_or_create_partner_customer(
            shop=shop,
            partner_user=partner_user,
            client_name=client_name or client_email or client_phone,
            client_email=client_email,
            client_phone=client_phone,
        )
    quote_request = QuoteRequest.objects.create(
        shop=shop,
        created_by=partner_user,
        on_behalf_of=client_user if not save_as_draft else None,
        customer_name=client_name or client_email or client_phone or "Client",
        customer_email=client_email,
        customer_phone=client_phone,
        notes=note or "Partner quote prepared in Printy.",
        status=QuoteStatus.DRAFT if save_as_draft else QuoteStatus.QUOTED,
        request_snapshot=_build_partner_request_snapshot(
            shop=shop,
            calculator_inputs_snapshot=calculator_inputs_snapshot,
            pricing_snapshot=pricing_snapshot,
            partner_user=partner_user,
            partner_brand_name=partner_brand_name,
            partner_markup=partner_markup,
            client_name=client_name,
            client_email=client_email,
            client_phone=client_phone,
            client_company=client_company,
            client_user=client_user,
        ),
    )
    quote_request.request_reference = _build_reference("QR", quote_request.id)
    quote_request.save(update_fields=["request_reference", "updated_at"])

    response_snapshot = _public_response_snapshot(
        pricing_snapshot=pricing_snapshot,
        partner_brand_name=partner_brand_name,
        split=split,
        note=note or "Partner quote prepared in Printy.",
        white_label_mode=True,
    )
    response_snapshot["internal_pricing_snapshot"] = _internal_pricing_snapshot(split=split)
    if preview.get("quantity_pricing"):
        response_snapshot["quantity_pricing_snapshot"] = preview["quantity_pricing"]
    if save_as_draft:
        response = Quote.objects.create(
            quote_request=quote_request,
            shop=shop,
            created_by=partner_user,
            status=QuoteOfferStatus.PENDING,
            total=split["client_total"],
            note=note or "Partner quote draft prepared in Printy.",
            response_snapshot=response_snapshot,
            revised_pricing_snapshot=None,
        )
        response.quote_reference = _build_reference("QS", response.id)
        response.client_quote_status = "draft"
        response.save(
            update_fields=[
                "quote_reference",
                "client_quote_status",
                "updated_at",
            ]
        )
    else:
        response = create_quote_response(
            quote_request=quote_request,
            shop=shop,
            user=partner_user,
            status="sent",
            response_snapshot=response_snapshot,
            revised_pricing_snapshot=None,
            total=split["client_total"],
            note=note or "Partner quote prepared in Printy.",
        )
    if not save_as_draft:
        sent_at = response.sent_at or timezone.now()
        response.sent_at = sent_at
        response.expires_at = calculate_quote_expiry(sent_at=sent_at)
    response.sent_to_client_at = response.sent_at if not save_as_draft else None
    response.sent_to_client_by = partner_user if not save_as_draft else None
    response.client_quote_status = "draft" if save_as_draft else "sent"
    response.save(
        update_fields=[
            "sent_at",
            "expires_at",
            "sent_to_client_at",
            "sent_to_client_by",
            "client_quote_status",
            "updated_at",
        ]
    )
    _persist_quote_split(quote=response, split=split)
    return {
        "draft": None,
        "quote_request": quote_request,
        "quote": response,
        "preview": preview,
    }


@transaction.atomic
def respond_to_assigned_quote_request(
    *,
    partner_user,
    quote_request,
    shop: Shop,
    pricing_snapshot: dict[str, Any],
    partner_markup: Decimal,
    note: str = "",
) -> dict[str, Any]:
    validate_partner_markup(pricing_snapshot=pricing_snapshot, shop=shop, partner_markup=partner_markup)
    preview = build_partner_quote_preview(
        pricing_snapshot=pricing_snapshot,
        shop=shop,
        partner_markup=partner_markup,
    )
    production_base_price = _money(preview.get("production_estimate"))
    broker_client_price = _money(preview.get("broker_client_price"), default=str(production_base_price + partner_markup))
    split = _financial_split_payload(production_base_price=production_base_price, broker_client_price=broker_client_price)
    partner_brand_name = getattr(partner_user, "name", "") or getattr(partner_user, "email", "") or "Print Manager"

    request_snapshot = _as_dict(quote_request.request_snapshot)
    request_snapshot.update(
        {
            "partner_brand_name": partner_brand_name,
            "relationship_owner_type": "user",
            "relationship_owner_user_id": partner_user.id,
            "selected_shop_ids": [shop.id],
            "selected_shop_preview": {
                "id": shop.id,
                "slug": shop.slug,
                "name": shop.name,
            },
            "topology_mode": TOPOLOGY_MANAGED,
        }
    )
    request_snapshot["visibility"] = {
        "actor": "client",
        "topology_mode": TOPOLOGY_MANAGED,
        "exposes_internal_economics": False,
    }
    quote_request.request_snapshot = request_snapshot
    quote_request.save(update_fields=["request_snapshot", "updated_at"])

    response_snapshot = _public_response_snapshot(
        pricing_snapshot=pricing_snapshot,
        partner_brand_name=partner_brand_name,
        split=split,
        note=note or "Your Print Manager prepared an exact quote in Printy.",
    )
    response_snapshot["internal_pricing_snapshot"] = _internal_pricing_snapshot(split=split)
    if preview.get("quantity_pricing"):
        response_snapshot["quantity_pricing_snapshot"] = preview["quantity_pricing"]
    response = create_quote_response(
        quote_request=quote_request,
        shop=shop,
        user=partner_user,
        status="sent",
        response_snapshot=response_snapshot,
        revised_pricing_snapshot=None,
        total=split["client_total"],
        note=note or "Your Print Manager prepared an exact quote in Printy.",
    )
    sent_at = response.sent_at or timezone.now()
    response.sent_at = sent_at
    response.expires_at = calculate_quote_expiry(sent_at=sent_at)
    response.sent_to_client_at = response.sent_at
    response.sent_to_client_by = partner_user
    response.client_quote_status = "sent"
    response.save(
        update_fields=[
            "sent_at",
            "expires_at",
            "sent_to_client_at",
            "sent_to_client_by",
            "client_quote_status",
            "updated_at",
        ]
    )
    _persist_quote_split(quote=response, split=split)
    return {
        "quote_request": quote_request,
        "quote": response,
        "preview": preview,
    }
