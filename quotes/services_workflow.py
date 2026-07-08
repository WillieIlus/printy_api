"""Canonical draft/request/response workflow services."""

import logging
import secrets

from django.conf import settings
from accounts.models import User
from accounts.services.roles import is_broker, user_can_manage_clients, user_can_source_jobs
from accounts.services.system_accounts import ensure_printy_manager_user, get_printy_manager_user, is_system_account
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from inventory.models import Machine, Paper
from notifications.models import Notification
from notifications.services import notify_quote_event
from api.visibility import (
    CLIENT_ACTOR,
    TOPOLOGY_MANAGED,
    project_identity,
    project_match_summary,
    project_pricing_breakdown,
    project_production_intelligence,
    resolve_topology_mode_for_quote_request,
)
from pricing.models import FinishingRate

logger = logging.getLogger(__name__)
from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent, CalculatorDraftStatus, QuoteStatus, QuoteOfferStatus
from quotes.messaging import create_quote_message
from quotes.models import (
    CalculatorDraft,
    QuoteItem,
    QuoteItemFinishing,
    QuoteRequest,
    QuoteRequestMessage,
    QuoteShareLink,
    Quote,
    ProductionOption,
)
from quotes.pending_artwork import claim_pending_artwork_to_quote_request
from quotes.turnaround import estimate_turnaround, legacy_days_from_hours
from shops.models import Shop


def _build_reference(prefix: str, instance_id: int) -> str:
    return f"{prefix}-{timezone.now():%Y%m%d}-{int(instance_id):04d}"


def _generate_share_token() -> str:
    return secrets.token_urlsafe(32)


def _ensure_share_link(response: Quote, *, user=None) -> QuoteShareLink:
    share_link = response.share_links.order_by("-id").first()
    if share_link:
        if not share_link.token:
            share_link.token = _generate_share_token()
            share_link.save(update_fields=["token", "updated_at"])
        return share_link
    return QuoteShareLink.objects.create(
        quote=response,
        token=_generate_share_token(),
        expires_at=timezone.now() + timezone.timedelta(days=30),
        created_by=user if user and getattr(user, "is_authenticated", False) else None,
    )


def _coerce_positive_int(value):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _resolve_shop_resource(model, shop: Shop, candidate, *, active_only: bool = False):
    resource_id = _coerce_positive_int(candidate)
    if not resource_id:
        return None
    queryset = model.objects.filter(pk=resource_id, shop=shop)
    if active_only and hasattr(model, "is_active"):
        queryset = queryset.filter(is_active=True)
    return queryset.first()


def resolve_assigned_manager(candidate):
    manager_id = _coerce_positive_int(candidate)
    if not manager_id:
        return None

    manager = User.objects.filter(pk=manager_id, is_active=True).first()
    if manager is None:
        raise ValueError("Selected manager is invalid or inactive.")

    if not (
        user_can_manage_clients(manager)
        or user_can_source_jobs(manager)
        or bool(getattr(manager, "partner_profile_enabled", False))
    ):
        raise ValueError("Selected manager is not eligible to manage client requests.")

    return manager


def _resolved_manager_short_title(manager: User | None) -> str:
    if manager is not None and is_system_account(manager):
        return "Managed by Printy"
    return "Print Manager"


def _build_assignment_snapshot(*, assigned_manager: User | None, merged_request_details: dict) -> dict:
    snapshot = {
        "selected_manager_id": getattr(assigned_manager, "id", None),
        "manager_selection_mode": str(merged_request_details.get("manager_selection_mode") or "").strip(),
        "escalation_status": str(merged_request_details.get("escalation_status") or "").strip(),
        "failed_manager_attempts": int(merged_request_details.get("failed_manager_attempts") or 0),
    }
    if assigned_manager is not None and is_system_account(assigned_manager):
        snapshot.update(
            {
                "is_printy_fallback": True,
                "default_markup_rate": "0.75",
                "escalation_status": snapshot["escalation_status"] or "printy_handled",
                "support_email": "support@printy.ke",
            }
        )
    return snapshot


def _should_assign_printy_fallback(*, merged_request_details: dict, assigned_manager: User | None) -> bool:
    if assigned_manager is not None:
        return False
    if merged_request_details.get("manager_selection_mode") == QuoteRequest.MANAGER_SELECTION_PRINTY_AUTO:
        return True
    failed_attempts = int(merged_request_details.get("failed_manager_attempts") or 0)
    if failed_attempts >= 3:
        return True
    return bool(merged_request_details.get("force_printy_fallback"))


def _resolve_product_for_shop(draft: CalculatorDraft, shop: Shop):
    return draft.selected_product


def _draft_pricing_snapshot(draft: CalculatorDraft) -> dict:
    return {}


def _extract_shop_preview(pricing_snapshot, shop: Shop):
    if not isinstance(pricing_snapshot, dict):
        return None
    selected_shops = pricing_snapshot.get("selected_shops")
    if isinstance(selected_shops, list):
        for entry in selected_shops:
            if not isinstance(entry, dict):
                continue
            if entry.get("id") == shop.id or entry.get("slug") == shop.slug:
                return entry
    return pricing_snapshot


def _build_buyer_snapshot(*, draft: CalculatorDraft, merged_request_details: dict):
    user = draft.user
    return {
        "user_id": getattr(user, "id", None),
        "is_authenticated": True,
        "name": (
            merged_request_details.get("customer_name")
            or getattr(user, "name", "")
            or getattr(user, "get_full_name", lambda: "")()
            or getattr(user, "email", "")
        ),
        "email": merged_request_details.get("customer_email") or getattr(user, "email", ""),
        "phone": merged_request_details.get("customer_phone", ""),
    }


def _build_request_snapshot(*, draft: CalculatorDraft, shop: Shop, merged_request_details: dict):
    pricing_snapshot = _draft_pricing_snapshot(draft)
    selected_shop_preview = _extract_shop_preview(pricing_snapshot, shop) or {}
    return {
        "source": "calculator_draft_send",
        "draft_reference": draft.draft_reference,
        "calculator_inputs": draft.calculator_inputs_snapshot or {},
        "production_preview_snapshot": pricing_snapshot.get("production_preview"),
        "pricing_preview_snapshot": pricing_snapshot.get("pricing_preview"),
        "selected_shop_preview": selected_shop_preview,
        "matched_specs": selected_shop_preview.get("matched_specs") or [],
        "needs_confirmation": selected_shop_preview.get("needs_confirmation") or [],
        "request_details": merged_request_details,
        "custom_product_snapshot": draft.custom_product_snapshot,
        "selected_shop_ids": merged_request_details.get("selected_shop_ids") or [],
        "selected_shop": {"id": shop.id, "slug": shop.slug, "name": shop.name},
        "buyer": _build_buyer_snapshot(draft=draft, merged_request_details=merged_request_details),
        "customer_pricing": {
            "currency": pricing_snapshot.get("currency") or "KES",
            "min_price": pricing_snapshot.get("min_price"),
            "max_price": pricing_snapshot.get("max_price"),
            "production_preview": project_production_intelligence(pricing_snapshot.get("production_preview")),
            "pricing_summary": project_pricing_breakdown(pricing_snapshot.get("pricing_preview"), actor=CLIENT_ACTOR),
            "selected_shop_preview": project_match_summary(
                selected_shop_preview,
                actor=CLIENT_ACTOR,
                topology_mode=TOPOLOGY_MANAGED,
            ),
        },
        "visibility": {
            "actor": CLIENT_ACTOR,
            "topology_mode": TOPOLOGY_MANAGED,
            "exposes_internal_economics": False,
        },
    }


def _build_manager_intake_snapshot(*, draft: CalculatorDraft, merged_request_details: dict, assigned_manager=None):
    pricing_snapshot = _draft_pricing_snapshot(draft)
    snapshot = {
        "source": "manager_led_intake",
        "draft_reference": draft.draft_reference,
        "calculator_inputs": draft.calculator_inputs_snapshot or {},
        "production_preview_snapshot": pricing_snapshot.get("production_preview"),
        "pricing_preview_snapshot": pricing_snapshot.get("pricing_preview"),
        "matched_specs": [],
        "needs_confirmation": [],
        "request_details": merged_request_details,
        "custom_product_snapshot": draft.custom_product_snapshot,
        "selected_shop_ids": [],
        "buyer": _build_buyer_snapshot(draft=draft, merged_request_details=merged_request_details),
        "customer_pricing": {
            "currency": pricing_snapshot.get("currency") or "KES",
            "min_price": pricing_snapshot.get("min_price"),
            "max_price": pricing_snapshot.get("max_price"),
            "production_preview": project_production_intelligence(pricing_snapshot.get("production_preview")),
            "pricing_summary": project_pricing_breakdown(pricing_snapshot.get("pricing_preview"), actor=CLIENT_ACTOR),
            "selected_shop_preview": None,
        },
        "visibility": {
            "actor": CLIENT_ACTOR,
            "topology_mode": TOPOLOGY_MANAGED,
            "exposes_internal_economics": False,
        },
        "assignment": _build_assignment_snapshot(
            assigned_manager=assigned_manager,
            merged_request_details=merged_request_details,
        ),
    }
    if assigned_manager is not None:
        snapshot["relationship_owner_type"] = "user"
        snapshot["relationship_owner_user_id"] = assigned_manager.id
        snapshot["assigned_manager"] = {
            "id": assigned_manager.id,
            "display_name": getattr(assigned_manager, "name", "") or getattr(assigned_manager, "email", "") or "Print Manager",
            "short_title": _resolved_manager_short_title(assigned_manager),
        }
    return snapshot


def _build_item_spec_snapshot(*, draft: CalculatorDraft, merged_request_details: dict, shop: Shop):
    pricing_snapshot = _draft_pricing_snapshot(draft)
    selected_shop_preview = _extract_shop_preview(pricing_snapshot, shop)
    return {
        "source": "calculator_draft_send",
        "draft_reference": draft.draft_reference,
        "calculator_inputs": draft.calculator_inputs_snapshot or {},
        "custom_product_snapshot": draft.custom_product_snapshot or {},
        "request_details": merged_request_details,
        "production_preview_snapshot": pricing_snapshot.get("production_preview"),
        "pricing_preview_snapshot": pricing_snapshot.get("pricing_preview"),
        "selected_shop_preview": selected_shop_preview or {},
        "matched_specs": (selected_shop_preview or {}).get("matched_specs") or [],
        "needs_confirmation": (selected_shop_preview or {}).get("needs_confirmation") or [],
        "selected_shop_ids": merged_request_details.get("selected_shop_ids") or [],
        "selected_shop": {
            "id": shop.id,
            "slug": shop.slug,
            "name": shop.name,
        },
        "customer_pricing": {
            "currency": pricing_snapshot.get("currency") or "KES",
            "production_preview": project_production_intelligence(pricing_snapshot.get("production_preview")),
            "pricing_summary": project_pricing_breakdown(pricing_snapshot.get("pricing_preview"), actor=CLIENT_ACTOR),
        },
        "visibility": {
            "actor": CLIENT_ACTOR,
            "topology_mode": TOPOLOGY_MANAGED,
            "exposes_internal_economics": False,
        },
    }


def _build_manager_intake_item_spec_snapshot(*, draft: CalculatorDraft, merged_request_details: dict, assigned_manager=None):
    pricing_snapshot = _draft_pricing_snapshot(draft)
    snapshot = {
        "source": "manager_led_intake",
        "draft_reference": draft.draft_reference,
        "calculator_inputs": draft.calculator_inputs_snapshot or {},
        "custom_product_snapshot": draft.custom_product_snapshot or {},
        "request_details": merged_request_details,
        "production_preview_snapshot": pricing_snapshot.get("production_preview"),
        "pricing_preview_snapshot": pricing_snapshot.get("pricing_preview"),
        "selected_shop_preview": {},
        "matched_specs": [],
        "needs_confirmation": [],
        "selected_shop_ids": [],
        "customer_pricing": {
            "currency": pricing_snapshot.get("currency") or "KES",
            "production_preview": project_production_intelligence(pricing_snapshot.get("production_preview")),
            "pricing_summary": project_pricing_breakdown(pricing_snapshot.get("pricing_preview"), actor=CLIENT_ACTOR),
        },
        "visibility": {
            "actor": CLIENT_ACTOR,
            "topology_mode": TOPOLOGY_MANAGED,
            "exposes_internal_economics": False,
        },
        "assignment": _build_assignment_snapshot(
            assigned_manager=assigned_manager,
            merged_request_details=merged_request_details,
        ),
    }
    if assigned_manager is not None:
        snapshot["assigned_manager_id"] = assigned_manager.id
    return snapshot


def _build_quote_item(*, quote_request: QuoteRequest, draft: CalculatorDraft, shop: Shop, merged_request_details: dict) -> QuoteItem:
    calculator_inputs = draft.calculator_inputs_snapshot or {}
    custom_snapshot = draft.custom_product_snapshot or {}
    product = _resolve_product_for_shop(draft, shop)
    pricing_snapshot = _draft_pricing_snapshot(draft)
    shop_preview = _extract_shop_preview(pricing_snapshot, shop) or {}
    shop_selection = shop_preview.get("selection") if isinstance(shop_preview, dict) else {}
    if not isinstance(shop_selection, dict):
        shop_selection = {}

    pricing_mode = (
        calculator_inputs.get("product_pricing_mode")
        or calculator_inputs.get("pricing_mode")
        or getattr(product, "pricing_mode", "")
        or "SHEET"
    )
    paper = _resolve_shop_resource(
        Paper,
        shop,
        calculator_inputs.get("paper_id") or shop_selection.get("paper_id"),
        active_only=True,
    )
    machine = _resolve_shop_resource(
        Machine,
        shop,
        calculator_inputs.get("machine_id") or shop_selection.get("machine_id"),
        active_only=True,
    )
    width_mm = _coerce_positive_int(
        calculator_inputs.get("width_mm")
        or custom_snapshot.get("width_mm")
        or getattr(product, "default_finished_width_mm", None)
    )
    height_mm = _coerce_positive_int(
        calculator_inputs.get("height_mm")
        or custom_snapshot.get("height_mm")
        or getattr(product, "default_finished_height_mm", None)
    )

    item = QuoteItem.objects.create(
        quote_request=quote_request,
        item_type="PRODUCT" if product else "CUSTOM",
        product=product,
        title=(product.name if product else custom_snapshot.get("custom_title") or calculator_inputs.get("custom_title") or draft.title or "Custom print job")[:120],
        spec_text=(custom_snapshot.get("custom_brief") or calculator_inputs.get("custom_brief") or merged_request_details.get("notes") or "")[:5000],
        has_artwork=True,
        quantity=_coerce_positive_int(calculator_inputs.get("quantity")) or 1,
        pricing_mode=pricing_mode if pricing_mode in {"SHEET", "LARGE_FORMAT"} else "SHEET",
        paper=paper,
        chosen_width_mm=width_mm,
        chosen_height_mm=height_mm,
        sides=calculator_inputs.get("print_sides") or calculator_inputs.get("sides") or getattr(product, "default_sides", "") or "SIMPLEX",
        color_mode=calculator_inputs.get("colour_mode") or calculator_inputs.get("color_mode") or "COLOR",
        machine=machine,
        special_instructions=(merged_request_details.get("notes") or custom_snapshot.get("custom_brief") or "")[:5000],
        pricing_snapshot=_extract_shop_preview(pricing_snapshot, shop),
        item_spec_snapshot=_build_item_spec_snapshot(
            draft=draft,
            merged_request_details=merged_request_details,
            shop=shop,
        ),
        needs_review=(
            not product
            and not (custom_snapshot.get("custom_title") or calculator_inputs.get("custom_title") or draft.title)
        ) or (
            pricing_mode == "SHEET" and not paper
        ),
    )

    finishing_selections = calculator_inputs.get("finishings")
    if not isinstance(finishing_selections, list):
        finishing_selections = []
    for selection in finishing_selections:
        if not isinstance(selection, dict):
            continue
        finishing = _resolve_shop_resource(
            FinishingRate,
            shop,
            selection.get("finishing_rate_id") or selection.get("finishing_rate"),
            active_only=True,
        )
        if not finishing:
            continue
        selected_side = selection.get("selected_side")
        QuoteItemFinishing.objects.get_or_create(
            quote_item=item,
            finishing_rate=finishing,
            defaults={
                "selected_side": selected_side if selected_side in {"front", "back", "both"} else "both",
                "apply_to_sides": "DOUBLE" if selected_side == "both" else "SINGLE",
            },
        )

    return item


def _build_manager_intake_quote_item(*, quote_request: QuoteRequest, draft: CalculatorDraft, merged_request_details: dict, assigned_manager=None) -> QuoteItem:
    calculator_inputs = draft.calculator_inputs_snapshot or {}
    custom_snapshot = draft.custom_product_snapshot or {}
    pricing_mode = (
        calculator_inputs.get("product_pricing_mode")
        or calculator_inputs.get("pricing_mode")
        or "SHEET"
    )
    width_mm = _coerce_positive_int(
        calculator_inputs.get("width_mm")
        or custom_snapshot.get("width_mm")
    )
    height_mm = _coerce_positive_int(
        calculator_inputs.get("height_mm")
        or custom_snapshot.get("height_mm")
    )

    return QuoteItem.objects.create(
        quote_request=quote_request,
        item_type="CUSTOM",
        title=(custom_snapshot.get("custom_title") or calculator_inputs.get("custom_title") or draft.title or "Print job")[:120],
        spec_text=(custom_snapshot.get("custom_brief") or calculator_inputs.get("custom_brief") or merged_request_details.get("notes") or "")[:5000],
        has_artwork=True,
        quantity=_coerce_positive_int(calculator_inputs.get("quantity")) or 1,
        pricing_mode=pricing_mode if pricing_mode in {"SHEET", "LARGE_FORMAT"} else "SHEET",
        chosen_width_mm=width_mm,
        chosen_height_mm=height_mm,
        sides=calculator_inputs.get("print_sides") or calculator_inputs.get("sides") or "SIMPLEX",
        color_mode=calculator_inputs.get("colour_mode") or calculator_inputs.get("color_mode") or "COLOR",
        special_instructions=(merged_request_details.get("notes") or custom_snapshot.get("custom_brief") or "")[:5000],
        pricing_snapshot={},
        item_spec_snapshot=_build_manager_intake_item_spec_snapshot(
            draft=draft,
            merged_request_details=merged_request_details,
            assigned_manager=assigned_manager,
        ),
        needs_review=True,
    )


def _create_request_message(*, quote_request: QuoteRequest, sender, metadata: dict | None = None):
    return create_quote_message(
        quote_request=quote_request,
        sender=sender,
        recipient=quote_request.shop.owner,
        sender_role=QuoteRequestMessage.SenderRole.CLIENT,
        recipient_role=QuoteRequestMessage.RecipientRole.SHOP_OWNER,
        message_kind=QuoteRequestMessage.MessageKind.STATUS,
        message_type=QuoteRequestMessage.MessageType.QUOTE_REQUEST_CREATED,
        direction=QuoteRequestMessage.Direction.INBOUND,
        subject=f"New quote request from {quote_request.customer_name or 'client'}",
        body=quote_request.notes or "Request submitted to the shop.",
        metadata=metadata or {"status": QuoteStatus.SUBMITTED, "source": "calculator_draft_send"},
        send_email_copy=bool(getattr(quote_request.shop.owner, "email", "")),
        create_failure_notice=True,
    )


def _intake_has_artwork(request_details: dict | None) -> bool:
    details = request_details if isinstance(request_details, dict) else {}
    return bool(
        str(details.get("artwork_reference") or "").strip()
        or str(details.get("artwork_token") or "").strip()
    )


def _require_calculator_context_intent(*, calculator_context: str | None, intent: str | None):
    if not calculator_context or not intent:
        raise ValueError("calculator_context and intent are required.")
    valid_contexts = {choice.value for choice in CalculatorDraftContext}
    valid_intents = {choice.value for choice in CalculatorDraftIntent}
    if calculator_context not in valid_contexts:
        raise ValueError("calculator_context is invalid.")
    if intent not in valid_intents:
        raise ValueError("intent is invalid.")


def _assert_calculator_routing_allowed(*, calculator_context: str | None, intent: str | None, shops: list[Shop] | None = None):
    _require_calculator_context_intent(calculator_context=calculator_context, intent=intent)
    shops = shops or []
    if calculator_context == CalculatorDraftContext.PUBLIC_GUEST:
        if intent not in {CalculatorDraftIntent.PUBLIC_PREVIEW, CalculatorDraftIntent.SAVE_DRAFT}:
            raise ValueError("Public calculator requests cannot route to shops.")
        if shops:
            raise ValueError("Public calculator requests cannot route to shops.")
    if calculator_context == CalculatorDraftContext.CLIENT_DASHBOARD:
        if intent not in {CalculatorDraftIntent.SAVE_DRAFT, CalculatorDraftIntent.CLIENT_QUOTE_REQUEST}:
            raise ValueError("Client calculator requests cannot source production.")
        if shops:
            raise ValueError("Client calculator requests cannot route directly to shops.")
    if calculator_context == CalculatorDraftContext.SHOP_DASHBOARD:
        if intent not in {CalculatorDraftIntent.INTERNAL_ESTIMATE, CalculatorDraftIntent.RESPOND_TO_REQUEST}:
            raise ValueError("Shop calculator requests cannot source production.")
        if shops:
            raise ValueError("Shop calculator requests cannot source other shops.")
    if shops and calculator_context not in {
        CalculatorDraftContext.MANAGER_DASHBOARD,
        CalculatorDraftContext.BROKER_DASHBOARD,
        CalculatorDraftContext.ADMIN_DASHBOARD,
    }:
        raise ValueError("Direct shop sourcing is only available to manager, broker, or admin calculators.")
    if shops and intent != CalculatorDraftIntent.SOURCE_PRODUCTION:
        raise ValueError("Direct shop sourcing requires source_production intent.")


def save_calculator_draft(*, user=None, guest_session_key: str = "", selected_product=None, shop=None, source_job=None, title: str = "", calculator_inputs_snapshot: dict, pricing_snapshot: dict | None = None, custom_product_snapshot: dict | None = None, request_details_snapshot: dict | None = None, artwork_token: str = "", artwork_filename: str = "", calculator_context: str | None = None, intent: str | None = None) -> CalculatorDraft:
    _assert_calculator_routing_allowed(
        calculator_context=calculator_context,
        intent=intent,
        shops=[shop] if shop else [],
    )
    draft = CalculatorDraft.objects.create(
        user=user,
        guest_session_key=guest_session_key,
        selected_product=selected_product,
        source_job=source_job,
        title=title,
        calculator_context=calculator_context,
        intent=intent,
        calculator_inputs_snapshot=calculator_inputs_snapshot,
        custom_product_snapshot=custom_product_snapshot,
        request_details_snapshot=request_details_snapshot,
        artwork_token=artwork_token,
        artwork_filename=artwork_filename,
    )
    draft.draft_reference = _build_reference("QD", draft.id)
    draft.save(update_fields=["draft_reference", "updated_at"])
    return draft


def update_calculator_draft(
    *,
    draft: CalculatorDraft,
    title: str | None = None,
    shop=None,
    selected_product=None,
    calculator_inputs_snapshot: dict | None = None,
    pricing_snapshot: dict | None = None,
    custom_product_snapshot: dict | None = None,
    request_details_snapshot: dict | None = None,
    artwork_token: str | None = None,
    artwork_filename: str | None = None,
    guest_session_key: str | None = None,
    calculator_context: str | None = None,
    intent: str | None = None,
) -> CalculatorDraft:
    if draft.status != CalculatorDraftStatus.DRAFT:
        raise ValueError("Only draft quote drafts can be updated.")
    next_context = calculator_context if calculator_context is not None else draft.calculator_context
    next_intent = intent if intent is not None else draft.intent
    next_shop = shop
    if next_context in {CalculatorDraftContext.PUBLIC_GUEST, CalculatorDraftContext.CLIENT_DASHBOARD}:
        next_shop = None
    _assert_calculator_routing_allowed(
        calculator_context=next_context,
        intent=next_intent,
        shops=[next_shop] if next_shop else [],
    )

    if title is not None:
        draft.title = title
    if selected_product is not None:
        draft.selected_product = selected_product
    if calculator_inputs_snapshot is not None:
        draft.calculator_inputs_snapshot = calculator_inputs_snapshot
    if custom_product_snapshot is not None:
        draft.custom_product_snapshot = custom_product_snapshot
    if request_details_snapshot is not None:
        draft.request_details_snapshot = request_details_snapshot
    if artwork_token is not None:
        draft.artwork_token = artwork_token
    if artwork_filename is not None:
        draft.artwork_filename = artwork_filename
    if guest_session_key is not None:
        draft.guest_session_key = guest_session_key
    if calculator_context is not None:
        draft.calculator_context = calculator_context
    if intent is not None:
        draft.intent = intent
    draft.save()
    return draft


def send_calculator_draft_to_shops(*, draft: CalculatorDraft, shops: list[Shop], request_details_snapshot: dict | None = None, caller=None) -> list[QuoteRequest]:
    if draft.status != CalculatorDraftStatus.DRAFT:
        raise ValueError("Only draft quote drafts can be sent.")
    caller = caller or draft.user
    if shops and not user_can_source_jobs(caller):
        raise PermissionDenied("Only manager, broker, or admin users can route calculator drafts to shops.")

    merged_request_details = {
        **(draft.request_details_snapshot or {}),
        **(request_details_snapshot or {}),
    }
    if draft.intake_mode == CalculatorDraft.INTAKE_MODE_DIRECT_SHOP and draft.direct_intake_shop_id:
        merged_request_details.setdefault("direct_shop_intake", True)
        merged_request_details.setdefault("shop_id", draft.direct_intake_shop_id)
        merged_request_details.setdefault("shop_slug", getattr(draft.direct_intake_shop, "slug", ""))
        merged_request_details.setdefault("shop_name", getattr(draft.direct_intake_shop, "name", ""))
    calculator_context = merged_request_details.get("calculator_context") or draft.calculator_context
    intent = merged_request_details.get("intent") or draft.intent
    _assert_calculator_routing_allowed(
        calculator_context=calculator_context,
        intent=intent,
        shops=shops,
    )
    if draft.artwork_token and not merged_request_details.get("artwork_token"):
        merged_request_details["artwork_token"] = draft.artwork_token
    if draft.artwork_filename and not merged_request_details.get("artwork_filename"):
        merged_request_details["artwork_filename"] = draft.artwork_filename
    merged_request_details["selected_shop_ids"] = [shop.id for shop in shops]
    assigned_manager = resolve_assigned_manager(
        merged_request_details.get("selected_manager_id")
        or merged_request_details.get("assigned_manager_id")
        or merged_request_details.get("assigned_manager")
    )
    if _should_assign_printy_fallback(
        merged_request_details=merged_request_details,
        assigned_manager=assigned_manager,
    ):
        assigned_manager = get_printy_manager_user()
        if assigned_manager is None:
            assigned_manager, _profile, _created = ensure_printy_manager_user()
    if assigned_manager is not None and is_system_account(assigned_manager):
        merged_request_details["selected_manager_id"] = assigned_manager.id
        merged_request_details["manager_selection_mode"] = QuoteRequest.MANAGER_SELECTION_PRINTY_AUTO
        merged_request_details["force_printy_fallback"] = True
        merged_request_details["escalation_status"] = "printy_handled"
    on_behalf_of = None
    client_id = merged_request_details.get("client_id") or merged_request_details.get("on_behalf_of")
    if client_id:
        on_behalf_of = User.objects.filter(pk=client_id).first()
    if is_broker(draft.user) and on_behalf_of is None:
        raise ValueError("client_id is required for partner quote requests.")
    created_requests = []

    with transaction.atomic():
        if not shops:
            quote_request = QuoteRequest.objects.create(
                shop=None,
                created_by=draft.user,
                assigned_manager=assigned_manager,
                on_behalf_of=on_behalf_of,
                customer_name=merged_request_details.get("customer_name") or getattr(draft.user, "name", "") or draft.user.email,
                customer_email=merged_request_details.get("customer_email") or getattr(draft.user, "email", ""),
                customer_phone=merged_request_details.get("customer_phone", ""),
                notes=merged_request_details.get("notes", ""),
                status=QuoteStatus.SUBMITTED,
                delivery_preference=merged_request_details.get("delivery_preference", ""),
                delivery_address=merged_request_details.get("delivery_address", ""),
                source_draft=draft,
                request_snapshot=_build_manager_intake_snapshot(
                    draft=draft,
                    merged_request_details=merged_request_details,
                    assigned_manager=assigned_manager,
                ),
            )
            quote_request.request_reference = _build_reference("QR", quote_request.id)
            quote_request.save(update_fields=["request_reference", "updated_at"])
            _build_manager_intake_quote_item(
                quote_request=quote_request,
                draft=draft,
                merged_request_details=merged_request_details,
                assigned_manager=assigned_manager,
            )
            if merged_request_details.get("artwork_token"):
                claim_pending_artwork_to_quote_request(
                    token=str(merged_request_details["artwork_token"]),
                    quote_request=quote_request,
                    claimed_by=draft.user,
                )
            if assigned_manager and assigned_manager.id != draft.user.id:
                notify_quote_event(
                    recipient=assigned_manager,
                    notification_type=Notification.QUOTE_REQUEST_SUBMITTED,
                    message=f"New client intake request #{quote_request.id} has been assigned to you.",
                    object_type="quote_request",
                    object_id=quote_request.id,
                    actor=draft.user,
                )
            if draft.user_id:
                notify_quote_event(
                    recipient=draft.user,
                    notification_type=Notification.QUOTE_REQUEST_SENT,
                    message=f"Your quote request #{quote_request.id} was received by Printy.",
                    object_type="quote_request",
                    object_id=quote_request.id,
                    actor=draft.user,
                )
                if not _intake_has_artwork(merged_request_details):
                    Notification.objects.create(
                        user=draft.user,
                        actor=draft.user,
                        notification_type=Notification.QUOTE_REQUEST_SENT,
                        message=(
                            "Your quote request was sent. Don't forget to upload your artwork so production can begin "
                            "as soon as you accept a quote."
                        ),
                        object_type="quote_request",
                        object_id=quote_request.id,
                    )
                    from django.conf import settings
                    from django.core.mail import send_mail

                    try:
                        send_mail(
                            subject="Upload your artwork to keep your Printy request moving",
                            message=(
                                "Your quote request was sent. Don't forget to upload your artwork so production can begin "
                                "as soon as you accept a quote."
                            ),
                            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                            recipient_list=[draft.user.email],
                            fail_silently=False,
                        )
                    except Exception:
                        logger.warning(
                            "Failed to send missing-artwork reminder for calculator_draft=%s user=%s",
                            draft.id,
                            getattr(draft.user, "email", ""),
                            exc_info=True,
                        )
            created_requests.append(quote_request)
            draft.status = CalculatorDraftStatus.SENT
            draft.save(update_fields=["status", "updated_at"])
            return created_requests

        for index, shop in enumerate(shops):
            quote_request = QuoteRequest.objects.create(
                shop=shop,
                created_by=draft.user,
                assigned_manager=assigned_manager,
                on_behalf_of=on_behalf_of,
                customer_name=merged_request_details.get("customer_name") or getattr(draft.user, "name", "") or draft.user.email,
                customer_email=merged_request_details.get("customer_email") or draft.user.email,
                customer_phone=merged_request_details.get("customer_phone", ""),
                notes=merged_request_details.get("notes", ""),
                status=QuoteStatus.SUBMITTED,
                delivery_preference=merged_request_details.get("delivery_preference", ""),
                delivery_address=merged_request_details.get("delivery_address", ""),
                source_draft=draft,
                request_snapshot=_build_request_snapshot(
                    draft=draft,
                    shop=shop,
                    merged_request_details=merged_request_details,
                ),
            )
            quote_request.request_reference = _build_reference("QR", quote_request.id)
            quote_request.save(update_fields=["request_reference", "updated_at"])
            _build_quote_item(
                quote_request=quote_request,
                draft=draft,
                shop=shop,
                merged_request_details=merged_request_details,
            )
            if merged_request_details.get("artwork_token"):
                claim_pending_artwork_to_quote_request(
                    token=str(merged_request_details["artwork_token"]),
                    quote_request=quote_request,
                    claimed_by=draft.user,
                    delete_after_claim=index == len(shops) - 1,
                )
            _create_request_message(quote_request=quote_request, sender=draft.user)
            create_quote_message(
                quote_request=quote_request,
                sender=draft.user,
                recipient=draft.user,
                sender_role=QuoteRequestMessage.SenderRole.CLIENT,
                recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
                message_kind=QuoteRequestMessage.MessageKind.STATUS,
                message_type=QuoteRequestMessage.MessageType.QUOTE_REQUEST_CREATED,
                direction=QuoteRequestMessage.Direction.OUTBOUND,
                subject=f"Quote request sent to {project_identity(shop.name, actor=CLIENT_ACTOR)}",
                body=quote_request.notes or f"Your quote request was sent to {project_identity(shop.name, actor=CLIENT_ACTOR)}.",
                metadata={"status": QuoteStatus.SUBMITTED, "source": "calculator_draft_send"},
            )
            if shop.owner_id and shop.owner_id != draft.user.id:
                notify_quote_event(
                    recipient=shop.owner,
                    notification_type=Notification.QUOTE_REQUEST_SUBMITTED,
                    message=f"New quote request #{quote_request.id} from {quote_request.customer_name or 'customer'}.",
                    object_type="quote_request",
                    object_id=quote_request.id,
                    actor=draft.user,
                )
            if draft.user_id:
                notify_quote_event(
                    recipient=draft.user,
                    notification_type=Notification.QUOTE_REQUEST_SENT,
                    message=f"Your quote request #{quote_request.id} was sent to {project_identity(shop.name, actor=CLIENT_ACTOR)}.",
                    object_type="quote_request",
                    object_id=quote_request.id,
                    actor=draft.user,
                )
            created_requests.append(quote_request)
        draft.status = CalculatorDraftStatus.SENT
        draft.save(update_fields=["status", "updated_at"])
    return created_requests


def create_production_option_from_calculator(
    *,
    quote_request: QuoteRequest,
    shop: Shop,
    created_by,
    production_cost,
    calculator_context: str,
    intent: str,
    estimated_turnaround_hours=None,
    capacity_status: str = "",
    score=None,
    pricing_snapshot: dict | None = None,
    notes: str = "",
) -> ProductionOption:
    _assert_calculator_routing_allowed(
        calculator_context=calculator_context,
        intent=intent,
        shops=[shop],
    )
    option = ProductionOption(
        quote_request=quote_request,
        shop=shop,
        production_cost=production_cost,
        estimated_turnaround_hours=estimated_turnaround_hours,
        capacity_status=capacity_status,
        score=score,
        pricing_snapshot=pricing_snapshot or {},
        notes=notes,
        created_by=created_by,
    )
    option.full_clean()
    option.save()
    return option


def _request_status_for_response_status(response_status: str) -> str:
    if response_status == QuoteOfferStatus.ACCEPTED:
        return QuoteStatus.QUOTED
    if response_status == QuoteOfferStatus.REJECTED:
        return QuoteStatus.REJECTED
    return QuoteStatus.QUOTED


def _assert_response_transition(current_status: str | None, next_status: str):
    allowed = {
        None: {
            QuoteOfferStatus.PENDING,
            QuoteOfferStatus.MODIFIED,
            QuoteOfferStatus.SENT,
            QuoteOfferStatus.REVISED,
            QuoteOfferStatus.ACCEPTED,
            QuoteOfferStatus.REJECTED,
        },
        QuoteOfferStatus.PENDING: {
            QuoteOfferStatus.PENDING,
            QuoteOfferStatus.MODIFIED,
            QuoteOfferStatus.SENT,
            QuoteOfferStatus.REVISED,
            QuoteOfferStatus.ACCEPTED,
            QuoteOfferStatus.REJECTED,
        },
        QuoteOfferStatus.MODIFIED: {
            QuoteOfferStatus.MODIFIED,
            QuoteOfferStatus.SENT,
            QuoteOfferStatus.REVISED,
            QuoteOfferStatus.ACCEPTED,
            QuoteOfferStatus.REJECTED,
        },
        QuoteOfferStatus.SENT: {
            QuoteOfferStatus.SENT,
            QuoteOfferStatus.REVISED,
            QuoteOfferStatus.ACCEPTED,
            QuoteOfferStatus.REJECTED,
        },
        QuoteOfferStatus.REVISED: {
            QuoteOfferStatus.REVISED,
            QuoteOfferStatus.ACCEPTED,
            QuoteOfferStatus.REJECTED,
        },
        QuoteOfferStatus.ACCEPTED: set(),
        QuoteOfferStatus.REJECTED: set(),
    }
    if next_status not in allowed.get(current_status, set()):
        raise ValueError(f"Cannot change quote response from {current_status or 'new'} to {next_status}.")


def create_quote_response(*, quote_request: QuoteRequest, shop, user, status: str, response_snapshot: dict, revised_pricing_snapshot: dict | None = None, total=None, note: str = "", turnaround_days=None, turnaround_hours=None) -> Quote:
    _assert_response_transition(None, status)
    if turnaround_hours is None and turnaround_days is not None:
        turnaround_hours = turnaround_days * 8
    turnaround_estimate = estimate_turnaround(shop=shop, working_hours=turnaround_hours)
    response = Quote.objects.create(
        quote_request=quote_request,
        shop=shop,
        created_by=user,
        status=status,
        total=total,
        sent_at=timezone.now() if status != QuoteOfferStatus.PENDING else None,
        note=note,
        turnaround_days=legacy_days_from_hours(turnaround_hours) if turnaround_hours else turnaround_days,
        turnaround_hours=turnaround_hours,
        estimated_ready_at=turnaround_estimate.ready_at if turnaround_estimate else None,
        human_ready_text=turnaround_estimate.human_ready_text if turnaround_estimate else "",
        turnaround_label=turnaround_estimate.label if turnaround_estimate else "",
        revision_number=quote_request.quotes.count() + 1,
        response_snapshot=response_snapshot,
        revised_pricing_snapshot=revised_pricing_snapshot,
    )
    response.quote_reference = _build_reference("Q", response.id)
    response.save(update_fields=["quote_reference", "updated_at"])
    quote_request.status = _request_status_for_response_status(status)
    quote_request.save(update_fields=["status", "updated_at"])

    share_link = None
    if status != QuoteOfferStatus.PENDING:
        sender_name = project_identity(
            getattr(shop, "name", None),
            actor=CLIENT_ACTOR,
            topology_mode=resolve_topology_mode_for_quote_request(quote_request),
        )
        # Create share link for client visibility
        share_link = _ensure_share_link(response, user=user)

        create_quote_message(
            quote_request=quote_request,
            quote=response,
            sender=user,
            recipient=quote_request.created_by,
            recipient_email=quote_request.customer_email,
            sender_role=QuoteRequestMessage.SenderRole.SHOP,
            recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
            message_kind=QuoteRequestMessage.MessageKind.QUOTE,
            message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
            direction=QuoteRequestMessage.Direction.INBOUND,
            subject=f"{sender_name} sent a quote",
            body=note or "A shop sent you a quote in Printy.",
            metadata={
                "status": quote_request.status, 
                "quote_status": status, 
                "total": str(total or ""),
                "share_token": share_link.token if share_link else None,
            },
            send_email_copy=bool(quote_request.customer_email),
            create_failure_notice=True,
        )
        create_quote_message(
            quote_request=quote_request,
            quote=response,
            sender=user,
            recipient=user,
            sender_role=QuoteRequestMessage.SenderRole.SHOP,
            recipient_role=QuoteRequestMessage.RecipientRole.SHOP_OWNER,
            message_kind=QuoteRequestMessage.MessageKind.QUOTE,
            message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
            direction=QuoteRequestMessage.Direction.OUTBOUND,
            subject=f"Quote sent to {quote_request.customer_name or 'client'}",
            body=note or "You sent a quote from Printy.",
            metadata={"status": quote_request.status, "quote_status": status, "total": str(total or "")},
        )
    return response


def update_quote_response(
    *,
    response: Quote,
    status: str,
    response_snapshot: dict | None = None,
    revised_pricing_snapshot: dict | None = None,
    total=None,
    note: str | None = None,
    turnaround_days=None,
    turnaround_hours=None,
) -> Quote:
    _assert_response_transition(response.status, status)

    response.status = status
    if response_snapshot is not None:
        response.response_snapshot = response_snapshot
    if revised_pricing_snapshot is not None:
        response.revised_pricing_snapshot = revised_pricing_snapshot
    if total is not None:
        response.total = total
    if note is not None:
        response.note = note
    if turnaround_hours is None and turnaround_days is not None:
        turnaround_hours = turnaround_days * 8
    if turnaround_days is not None:
        response.turnaround_days = turnaround_days
    if turnaround_hours is not None:
        turnaround_estimate = estimate_turnaround(shop=response.shop, working_hours=turnaround_hours)
        response.turnaround_hours = turnaround_hours
        response.turnaround_days = legacy_days_from_hours(turnaround_hours)
        response.estimated_ready_at = turnaround_estimate.ready_at if turnaround_estimate else None
        response.human_ready_text = turnaround_estimate.human_ready_text if turnaround_estimate else ""
        response.turnaround_label = turnaround_estimate.label if turnaround_estimate else ""
    if status != QuoteOfferStatus.PENDING and response.sent_at is None:
        response.sent_at = timezone.now()
    response.save()

    quote_request = response.quote_request
    quote_request.status = _request_status_for_response_status(status)
    quote_request.save(update_fields=["status", "updated_at"])

    share_link = None
    if status != QuoteOfferStatus.PENDING:
        sender_name = project_identity(
            getattr(response.shop, "name", None),
            actor=CLIENT_ACTOR,
            topology_mode=resolve_topology_mode_for_quote_request(quote_request),
        )
        # Create or update share link for client visibility
        share_link = _ensure_share_link(
            response,
            user=response.created_by if response.created_by and response.created_by.is_authenticated else None,
        )

        create_quote_message(
            quote_request=quote_request,
            quote=response,
            sender=response.created_by,
            recipient=quote_request.created_by,
            recipient_email=quote_request.customer_email,
            sender_role=QuoteRequestMessage.SenderRole.SHOP,
            recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
            message_kind=QuoteRequestMessage.MessageKind.QUOTE,
            message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
            direction=QuoteRequestMessage.Direction.INBOUND,
            subject=f"{sender_name} sent a quote",
            body=response.note or "A shop updated your quote in Printy.",
            metadata={
                "status": quote_request.status, 
                "quote_status": status, 
                "total": str(response.total or ""),
                "share_token": share_link.token if share_link else None,
            },
            send_email_copy=bool(quote_request.customer_email),
            create_failure_notice=True,
        )
        create_quote_message(
            quote_request=quote_request,
            quote=response,
            sender=response.created_by,
            recipient=response.created_by,
            sender_role=QuoteRequestMessage.SenderRole.SHOP,
            recipient_role=QuoteRequestMessage.RecipientRole.SHOP_OWNER,
            message_kind=QuoteRequestMessage.MessageKind.QUOTE,
            message_type=QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT,
            direction=QuoteRequestMessage.Direction.OUTBOUND,
            subject=f"Quote sent to {quote_request.customer_name or 'client'}",
            body=response.note or "You updated a quote in Printy.",
            metadata={"status": quote_request.status, "quote_status": status, "total": str(response.total or "")},
        )
    return response
