"""Visibility projection helpers for additive role-aware pricing exposure."""

from __future__ import annotations

from typing import Any

from accounts.services.roles import (
    CANONICAL_PARTNER_ROLE,
    CANONICAL_PRODUCTION_ROLE,
    CANONICAL_SUPER_ADMIN_ROLE,
    resolve_user_roles,
)


PUBLIC_ACTOR = "public"
CLIENT_ACTOR = "client"
PARTNER_ACTOR = "partner"
SHOP_ACTOR = "shop"
OPS_ACTOR = "ops"

TOPOLOGY_MARKETPLACE_LEGACY = "marketplace_legacy"
TOPOLOGY_MANAGED = "managed"
TOPOLOGY_TYPES = (
    "client_partner",
    "client_printy_support",
    "partner_shop",
    "shop_ops",
    "ops_internal",
)

SHOP_IDENTITY_KEYS = {
    "assigned_shop",
    "assigned_shop_id",
    "assigned_shop_name",
    "selected_shop",
    "selected_shop_id",
    "selected_shop_ids",
    "selected_shops",
    "other_shop",
    "other_shop_id",
    "other_shop_name",
    "other_shop_slug",
    "shop",
    "shop_id",
    "shop_ids",
    "shop_name",
    "shop_slug",
}

INTERNAL_FINANCIAL_KEYS = {
    "broker_margin",
    "broker_margin_amount",
    "broker_margin_fee",
    "broker_margin_percent",
    "broker_payout",
    "client_total_internal",
    "gross_margin",
    "platform_service_amount",
    "platform_service_percent",
    "printer_side_fee",
    "printy_fee",
    "production_base_price",
    "production_cost",
    "shop_payout",
}

RAW_SNAPSHOT_KEYS = {
    "internal_formula",
    "internal_pricing",
    "internal_pricing_snapshot",
    "internal_sourcing_snapshot",
    "pricing_formula",
    "pricing_snapshot",
    "raw_callback",
    "raw_response",
    "request_snapshot",
    "response_snapshot",
    "revised_pricing_snapshot",
}

FORBIDDEN_CLIENT_PUBLIC_KEYS = SHOP_IDENTITY_KEYS | INTERNAL_FINANCIAL_KEYS | RAW_SNAPSHOT_KEYS

FORBIDDEN_CLIENT_KEYS = FORBIDDEN_CLIENT_PUBLIC_KEYS | {
    "production_cost",
    "production_base_price",
    "shop_payout",
    "broker_payout",
    "broker_margin",
    "broker_margin_amount",
    "broker_margin_percent",
    "gross_margin",
    "printy_fee",
    "platform_service_amount",
    "platform_service_percent",
}

FORBIDDEN_SHOP_KEYS = RAW_SNAPSHOT_KEYS | {
    "client_total",
    "broker_payout",
    "broker_margin",
    "broker_margin_amount",
    "broker_margin_fee",
    "broker_margin_percent",
    "gross_margin",
    "printer_side_fee",
    "printy_fee",
    "platform_service_amount",
    "platform_service_percent",
    "competing_options",
    "production_options",
    "other_shop",
    "other_shop_id",
    "other_shop_name",
    "other_shop_slug",
    "other_shops",
}


def resolve_actor(user: Any) -> str:
    """Resolve actor string from user object."""
    if not user or not getattr(user, "is_authenticated", False):
        return PUBLIC_ACTOR
    roles = set(resolve_user_roles(user))
    if CANONICAL_SUPER_ADMIN_ROLE in roles or getattr(user, "is_staff", False):
        return OPS_ACTOR
    if CANONICAL_PRODUCTION_ROLE in roles:
        return SHOP_ACTOR
    if CANONICAL_PARTNER_ROLE in roles:
        return PARTNER_ACTOR
    return CLIENT_ACTOR


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _copy_keys(payload: dict[str, Any], allowed_keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: payload.get(key) for key in allowed_keys if key in payload}


def strip_forbidden_keys(payload: Any, actor: str) -> Any:
    if actor in {OPS_ACTOR, PARTNER_ACTOR}:
        return payload
    forbidden = FORBIDDEN_SHOP_KEYS if actor == SHOP_ACTOR else FORBIDDEN_CLIENT_KEYS
    if isinstance(payload, dict):
        return {
            key: strip_forbidden_keys(value, actor)
            for key, value in payload.items()
            if key not in forbidden
        }
    if isinstance(payload, list):
        return [strip_forbidden_keys(item, actor) for item in payload]
    return payload


def normalize_topology_mode(value: Any) -> str:
    if value == TOPOLOGY_MARKETPLACE_LEGACY:
        return TOPOLOGY_MARKETPLACE_LEGACY
    return TOPOLOGY_MANAGED


def _explicit_topology_mode(value: Any) -> str | None:
    if value == TOPOLOGY_MARKETPLACE_LEGACY:
        return TOPOLOGY_MARKETPLACE_LEGACY
    if value == TOPOLOGY_MANAGED:
        return TOPOLOGY_MANAGED
    return None


def resolve_topology_mode_from_snapshot(snapshot: dict[str, Any] | None) -> str:
    payload = _as_dict(snapshot)
    visibility = _as_dict(payload.get("visibility"))
    candidate = visibility.get("topology_mode") or payload.get("topology_mode")
    explicit = _explicit_topology_mode(candidate)
    return explicit or TOPOLOGY_MANAGED


def resolve_topology_mode_for_quote_request(quote_request: Any) -> str:
    request_snapshot = _as_dict(getattr(quote_request, "request_snapshot", None))
    visibility = _as_dict(request_snapshot.get("visibility"))
    explicit_mode = _explicit_topology_mode(
        visibility.get("topology_mode") or request_snapshot.get("topology_mode")
    )
    if explicit_mode:
        return explicit_mode

    if request_snapshot:
        source = request_snapshot.get("source")
        if source in {"guest_calculator_send", "calculator_draft_send"}:
            return TOPOLOGY_MANAGED

    if getattr(quote_request, "source_draft_id", None):
        return TOPOLOGY_MANAGED

    return TOPOLOGY_MARKETPLACE_LEGACY


def can_actor_view_shop_name(*, actor: str, topology_mode: str = TOPOLOGY_MANAGED) -> bool:
    mode = normalize_topology_mode(topology_mode)
    if actor in {OPS_ACTOR, SHOP_ACTOR, PARTNER_ACTOR}:
        return True
    return mode == TOPOLOGY_MARKETPLACE_LEGACY


def can_actor_view_client_name(*, actor: str, topology_mode: str = TOPOLOGY_MANAGED) -> bool:
    mode = normalize_topology_mode(topology_mode)
    if actor == OPS_ACTOR:
        return True
    if actor == CLIENT_ACTOR:
        return True
    return mode == TOPOLOGY_MARKETPLACE_LEGACY


def can_actor_view_partner_identity(*, actor: str, topology_mode: str = TOPOLOGY_MANAGED) -> bool:
    mode = normalize_topology_mode(topology_mode)
    if actor in {OPS_ACTOR, PARTNER_ACTOR, SHOP_ACTOR}:
        return True
    return mode == TOPOLOGY_MARKETPLACE_LEGACY


def can_actor_view_email(*, actor: str, topology_mode: str = TOPOLOGY_MANAGED) -> bool:
    mode = normalize_topology_mode(topology_mode)
    if actor == OPS_ACTOR:
        return True
    return mode == TOPOLOGY_MARKETPLACE_LEGACY and actor == SHOP_ACTOR


def can_actor_view_phone(*, actor: str, topology_mode: str = TOPOLOGY_MANAGED) -> bool:
    return can_actor_view_email(actor=actor, topology_mode=topology_mode)


def project_shop_identity(
    name: str | None,
    *,
    actor: str,
    topology_mode: str = TOPOLOGY_MANAGED,
    fallback: str = "Verified Print Partner",
) -> str:
    if can_actor_view_shop_name(actor=actor, topology_mode=topology_mode):
        return name or "Participant"
    return fallback


def project_client_identity(
    name: str | None,
    *,
    actor: str,
    topology_mode: str = TOPOLOGY_MANAGED,
    fallback: str = "Client",
) -> str:
    if can_actor_view_client_name(actor=actor, topology_mode=topology_mode):
        return name or fallback
    return fallback


def project_production_intelligence(preview: dict[str, Any] | None) -> dict[str, Any] | None:
    payload = _as_dict(preview)
    if not payload:
        return None
    return _copy_keys(
        payload,
        (
            "pieces_per_sheet",
            "sheets_required",
            "parent_sheet",
            "imposition_label",
            "size_label",
            "quantity",
            "cutting_required",
            "selected_finishings",
            "suggested_finishings",
            "warnings",
            "roll_width_m",
            "roll_width_mm",
            "items_per_row",
            "rows",
            "used_length_m",
            "orientation",
            "input_size_m",
            "charged_area_m2",
            "printed_area_m2",
            "waste_area_m2",
            "overlap_area_m2",
            "tiling",
            "booklet_input_pages",
            "booklet_normalized_pages",
            "booklet_blank_pages_added",
            "booklet_cover_pages",
            "booklet_insert_pages",
            "booklet_cover_sheets",
            "booklet_insert_sheets",
            "booklet_binding_label",
            "booklet_cover_paper_label",
            "booklet_insert_paper_label",
        ),
    )


def project_public_preview(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    preview = _as_dict(payload)
    if not preview:
        return None
    projected = _copy_keys(
        preview,
        (
            "quote_type",
            "product_type",
            "size_label",
            "quantity",
            "normalized_pages",
            "blank_pages_added",
            "blanks_added",
            "input_pages",
            "cover_pages",
            "insert_pages",
            "cover_sheets",
            "insert_sheets",
            "matched_stock",
            "warnings",
            "explanations",
        ),
    )
    production_preview = project_production_intelligence(preview)
    if production_preview:
        projected["production_preview"] = production_preview
    pricing_breakdown = _copy_keys(
        _as_dict(preview.get("pricing")),
        (
            "method",
            "charged_area_m2",
            "charged_length_m",
            "minimum_charge",
            "minimum_charge_applied",
            "rate",
        ),
    )
    if pricing_breakdown:
        projected["pricing_breakdown"] = pricing_breakdown
    return projected or None


def project_pricing_breakdown(payload: dict[str, Any] | None, *, actor: str) -> dict[str, Any] | None:
    breakdown = _as_dict(payload)
    if not breakdown:
        return None

    if actor == SHOP_ACTOR or actor == OPS_ACTOR:
        return breakdown

    if actor == PARTNER_ACTOR:
        projected = _copy_keys(
            breakdown,
            (
                "currency",
                "estimated_total",
                "price_range",
                "lines",
            ),
        )
        projected["lines"] = [
            {"label": line.get("label"), "amount": line.get("amount")}
            for line in _as_list(breakdown.get("lines"))
            if isinstance(line, dict)
        ]
        return projected

    if actor == PUBLIC_ACTOR:
        return _copy_keys(
            breakdown,
            (
                "currency",
                "method",
                "charged_area_m2",
                "charged_length_m",
                "minimum_charge",
                "minimum_charge_applied",
                "lines",
            ),
        )

    return None


def project_match_summary(
    payload: dict[str, Any] | None,
    *,
    actor: str,
    include_identity: bool = True,
    topology_mode: str = TOPOLOGY_MANAGED,
) -> dict[str, Any]:
    match = _as_dict(payload)

    should_show_identity = (
        include_identity
        and actor != PUBLIC_ACTOR
        and can_actor_view_shop_name(actor=actor, topology_mode=topology_mode)
    )
    
    projected = {
        "id": match.get("id"),
        "shop_id": match.get("shop_id") if should_show_identity else None,
        "name": match.get("name") if should_show_identity else "Verified Print Partner",
        "shop_name": match.get("shop_name") if should_show_identity else "Verified Print Partner",
        "slug": match.get("slug") if should_show_identity else "partner",
        "shop_slug": match.get("shop_slug") if should_show_identity else "partner",
        "can_calculate": match.get("can_calculate"),
        "can_price_now": match.get("can_price_now"),
        "can_send_quote_request": match.get("can_send_quote_request"),
        "currency": match.get("currency"),
        "reason": match.get("reason"),
        "summary": match.get("summary"),
        "missing_fields": _as_list(match.get("missing_fields")),
        "missing_specs": _as_list(match.get("missing_specs") or match.get("missing_fields")),
        "similarity_score": match.get("similarity_score"),
        "match_score": match.get("match_score"),
        "confidence_score": match.get("confidence_score"),
        "match_type": match.get("match_type"),
        "price_confidence": match.get("price_confidence"),
        "quote_basis": match.get("quote_basis"),
        "distance_km": match.get("distance_km") if should_show_identity else None,
        "total": match.get("total") if actor != PUBLIC_ACTOR else None,
        "turnaround_hours": match.get("turnaround_hours"),
        "estimated_working_hours": match.get("estimated_working_hours"),
        "estimated_ready_at": match.get("estimated_ready_at"),
        "human_ready_text": match.get("human_ready_text"),
        "turnaround_label": match.get("turnaround_label"),
        "exact_or_estimated": match.get("exact_or_estimated"),
        "product_match": match.get("product_match"),
        "matched_specs": _as_list(match.get("matched_specs")),
        "needs_confirmation": _as_list(match.get("needs_confirmation")),
        "closest_alternatives": _as_list(match.get("closest_alternatives")),
        "alternative_suggestions": _as_list(match.get("alternative_suggestions") or match.get("closest_alternatives")),
        "estimated_price": match.get("estimated_price") if actor != PUBLIC_ACTOR else None,
        "price_range": match.get("price_range"),
        "distance_label": match.get("distance_label") if should_show_identity else None,
        "production_preview": project_production_intelligence(match.get("production_preview")),
        "pricing_breakdown": project_pricing_breakdown(match.get("pricing_breakdown"), actor=actor),
    }
    if actor == SHOP_ACTOR or actor == OPS_ACTOR:
        projected["preview"] = match.get("preview")
        projected["selection"] = match.get("selection") or {}
    return projected


def project_public_marketplace_response(payload: dict[str, Any] | None) -> dict[str, Any]:
    response = _as_dict(payload)

    min_price = response.get("estimate_min") or response.get("min_price")
    max_price = response.get("estimate_max") or response.get("max_price")
    currency = response.get("currency") or "KES"
    display_price_text = response.get("display_price_text")
    display_mode = response.get("display_mode")
    confidence_label = response.get("confidence_label")
    source_label = response.get("source_label")
    raw_matches = _as_list(response.get("matches"))
    projected_matches = []
    for index, match in enumerate(raw_matches, start=1):
        if not isinstance(match, dict):
            continue
        projected_preview = project_public_preview(match.get("preview")) or {}
        selection = _as_dict(match.get("selection"))
        if selection.get("paper_label"):
            projected_preview["selected_paper_label"] = selection.get("paper_label")
        if selection.get("cover_paper_label"):
            projected_preview["selected_cover_paper_label"] = selection.get("cover_paper_label")
        if selection.get("insert_paper_label"):
            projected_preview["selected_insert_paper_label"] = selection.get("insert_paper_label")
        projected = project_match_summary(
            match,
            actor=PUBLIC_ACTOR,
            include_identity=False,
            topology_mode=TOPOLOGY_MANAGED,
        )
        projected_matches.append(
            {
                "option_label": f"Production option {index}",
                "can_produce": bool(
                    match.get("can_produce")
                    or match.get("can_calculate")
                    or match.get("can_price_now")
                ),
                "can_calculate": match.get("can_calculate"),
                "can_price_now": match.get("can_price_now"),
                "can_send_quote_request": match.get("can_send_quote_request"),
                "currency": match.get("currency"),
                "reason": match.get("reason"),
                "summary": match.get("summary"),
                "missing_fields": _as_list(match.get("missing_fields")),
                "missing_specs": _as_list(match.get("missing_specs") or match.get("missing_fields")),
                "match_type": match.get("match_type"),
                "price_confidence": match.get("price_confidence"),
                "quote_basis": match.get("quote_basis"),
                "turnaround_hours": match.get("turnaround_hours"),
                "estimated_working_hours": match.get("estimated_working_hours"),
                "estimated_ready_at": match.get("estimated_ready_at"),
                "human_ready_text": match.get("human_ready_text"),
                "turnaround_label": match.get("turnaround_label"),
                "exact_or_estimated": match.get("exact_or_estimated"),
                "product_match": match.get("product_match"),
                "matched_specs": _as_list(match.get("matched_specs")),
                "needs_confirmation": _as_list(match.get("needs_confirmation")),
                "closest_alternatives": _as_list(match.get("closest_alternatives")),
                "alternative_suggestions": _as_list(match.get("alternative_suggestions") or match.get("closest_alternatives")),
                "price_range": match.get("price_range"),
                "preview": projected_preview or None,
                "production_preview": projected.get("production_preview"),
                "pricing_breakdown": None,
            }
        )

    # Public shape strictly returns market range and confidence
    market_range = {
        "min": min_price,
        "max": max_price,
        "currency": currency,
        "label": display_price_text or (
            f"{currency} {min_price} - {max_price}" if min_price and max_price and min_price != max_price else f"{currency} {min_price or max_price or '0.00'}"
        ),
        "confidence": confidence_label or ("verified" if response.get("exact_or_estimated") else "estimated"),
        "display_mode": display_mode,
        "source_label": source_label,
    }

    # Production summary (safe)
    production_preview = project_production_intelligence(response.get("production_preview"))

    return {
        "mode": response.get("mode"),
        "can_calculate": response.get("can_calculate"),
        "product_type": response.get("product_type"),
        "price_mode": response.get("price_mode"),
        "total": response.get("total"),
        "matches_count": response.get("matches_count") or len(projected_matches),
        "matches": projected_matches,
        "shops": projected_matches,
        "selected_shops": projected_matches,
        "shop_matches": projected_matches,
        "min_price": min_price,
        "max_price": max_price,
        "estimate_min": min_price,
        "estimate_max": max_price,
        "display_price_text": display_price_text or market_range["label"],
        "display_mode": display_mode,
        "confidence_label": confidence_label,
        "source_label": source_label,
        "market_range": market_range,
        "currency": currency,
        "production_preview": production_preview,
        "production_summary": production_preview,
        "pricing_breakdown": None,
        "missing_requirements": _as_list(response.get("missing_requirements")),
        "missing_fields": _as_list(response.get("missing_requirements")),
        "unsupported_reasons": _as_list(response.get("unsupported_reasons")),
        "summary": response.get("summary"),
        "suggestions": _as_list(response.get("suggestions")),
        "exact_or_estimated": bool(response.get("exact_or_estimated")),
        "warnings": _as_list(response.get("warnings")),
        "visibility": {
            "actor": PUBLIC_ACTOR,
            "exposes_shop_identity": False,
            "exposes_internal_economics": False,
            "topology_mode": "managed",
        },
    }


def project_request_snapshot_for_client(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    snapshot = strip_forbidden_keys(_as_dict(payload), CLIENT_ACTOR)
    if not snapshot:
        return None
    topology_mode = resolve_topology_mode_from_snapshot(snapshot)
    selected_shop_preview = snapshot.get("selected_shop_preview")
    projected_selected_shop_preview = None
    if isinstance(selected_shop_preview, dict):
        projected_preview = project_match_summary(selected_shop_preview, actor=CLIENT_ACTOR, topology_mode=topology_mode)
        projected_selected_shop_preview = {
            "option_label": "Production source selected by your Print Manager",
            "can_produce": bool(
                selected_shop_preview.get("can_produce")
                or selected_shop_preview.get("can_calculate")
                or selected_shop_preview.get("can_price_now")
            ),
            "can_calculate": selected_shop_preview.get("can_calculate"),
            "can_price_now": selected_shop_preview.get("can_price_now"),
            "can_send_quote_request": selected_shop_preview.get("can_send_quote_request"),
            "currency": selected_shop_preview.get("currency"),
            "reason": selected_shop_preview.get("reason"),
            "summary": selected_shop_preview.get("summary"),
            "missing_fields": _as_list(selected_shop_preview.get("missing_fields")),
            "missing_specs": _as_list(selected_shop_preview.get("missing_specs") or selected_shop_preview.get("missing_fields")),
            "match_type": selected_shop_preview.get("match_type"),
            "price_confidence": selected_shop_preview.get("price_confidence"),
            "quote_basis": selected_shop_preview.get("quote_basis"),
            "turnaround_hours": selected_shop_preview.get("turnaround_hours"),
            "estimated_working_hours": selected_shop_preview.get("estimated_working_hours"),
            "estimated_ready_at": selected_shop_preview.get("estimated_ready_at"),
            "human_ready_text": selected_shop_preview.get("human_ready_text"),
            "turnaround_label": selected_shop_preview.get("turnaround_label"),
            "exact_or_estimated": selected_shop_preview.get("exact_or_estimated"),
            "product_match": selected_shop_preview.get("product_match"),
            "matched_specs": _as_list(selected_shop_preview.get("matched_specs")),
            "needs_confirmation": _as_list(selected_shop_preview.get("needs_confirmation")),
            "closest_alternatives": _as_list(selected_shop_preview.get("closest_alternatives")),
            "alternative_suggestions": _as_list(selected_shop_preview.get("alternative_suggestions") or selected_shop_preview.get("closest_alternatives")),
            "price_range": selected_shop_preview.get("price_range"),
            "production_preview": projected_preview.get("production_preview"),
            "pricing_breakdown": None,
        }
    return {
        "draft_reference": snapshot.get("draft_reference"),
        "partner_brand_name": snapshot.get("partner_brand_name"),
        "white_label_mode": bool(snapshot.get("white_label_mode")),
        "calculator_inputs": strip_forbidden_keys(_as_dict(snapshot.get("calculator_inputs")), CLIENT_ACTOR),
        "request_details": strip_forbidden_keys(_as_dict(snapshot.get("request_details")), CLIENT_ACTOR),
        "custom_product_snapshot": strip_forbidden_keys(_as_dict(snapshot.get("custom_product_snapshot")), CLIENT_ACTOR),
        "production_source_label": "Production source selected by your Print Manager",
        "matched_specs": _as_list(snapshot.get("matched_specs")),
        "needs_confirmation": _as_list(snapshot.get("needs_confirmation")),
        "production_preview_snapshot": project_production_intelligence(snapshot.get("production_preview_snapshot")),
        "pricing_preview_snapshot": project_pricing_breakdown(snapshot.get("pricing_preview_snapshot"), actor=CLIENT_ACTOR),
        "selected_shop_preview": projected_selected_shop_preview,
        "customer_pricing": strip_forbidden_keys(_as_dict(snapshot.get("customer_pricing")), CLIENT_ACTOR),
        "visibility": {
            "actor": CLIENT_ACTOR,
            "exposes_internal_economics": False,
            "topology_mode": topology_mode,
        },
    }


def _extract_customer_pricing(payload: dict[str, Any]) -> dict[str, Any]:
    payload = strip_forbidden_keys(payload, CLIENT_ACTOR)
    pricing = _as_dict(payload.get("pricing"))
    totals = _as_dict(payload.get("totals"))
    candidate_total = (
        pricing.get("grand_total")
        or totals.get("grand_total")
        or pricing.get("estimated_total")
        or payload.get("total")
    )
    customer_pricing = {
        "currency": payload.get("currency") or pricing.get("currency") or "KES",
        "estimated_total": candidate_total,
        "terms": payload.get("terms") or payload.get("payment_terms"),
        "payment_terms": payload.get("payment_terms") or payload.get("terms"),
    }
    if "shop_note" in payload:
        customer_pricing["shop_note"] = payload.get("shop_note")
    elif "note" in payload:
        customer_pricing["shop_note"] = payload.get("note")
    if payload.get("partner_brand_name"):
        customer_pricing["partner_brand_name"] = payload.get("partner_brand_name")
    if payload.get("white_label_mode") is not None:
        customer_pricing["white_label_mode"] = bool(payload.get("white_label_mode"))
    return customer_pricing


def project_quote_response_snapshot_for_client(
    response_snapshot: dict[str, Any] | None,
    *,
    revised_pricing_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    snapshot = strip_forbidden_keys(_as_dict(response_snapshot), CLIENT_ACTOR)
    revised = strip_forbidden_keys(_as_dict(revised_pricing_snapshot), CLIENT_ACTOR)
    if not snapshot and not revised:
        return None

    production_preview = (
        project_production_intelligence(snapshot.get("production_preview"))
        or project_production_intelligence(snapshot.get("production_preview_snapshot"))
        or project_production_intelligence(revised.get("production_preview"))
    )
    pricing_summary = (
        project_pricing_breakdown(snapshot.get("pricing_breakdown"), actor=CLIENT_ACTOR)
        or project_pricing_breakdown(snapshot.get("pricing_preview"), actor=CLIENT_ACTOR)
        or project_pricing_breakdown(snapshot.get("pricing_preview_snapshot"), actor=CLIENT_ACTOR)
        or project_pricing_breakdown(revised, actor=CLIENT_ACTOR)
    )
    white_label_mode = bool(snapshot.get("white_label_mode"))

    return {
        **_extract_customer_pricing(snapshot),
        "production_preview": None if white_label_mode else production_preview,
        "pricing_summary": None if white_label_mode else pricing_summary,
        "visibility": {
            "actor": CLIENT_ACTOR,
            "exposes_internal_economics": False,
        },
    }


def project_client_counterparty_name(
    *,
    fallback_name: str | None,
    topology_mode: str = TOPOLOGY_MANAGED,
    request_snapshot: dict[str, Any] | None = None,
    response_snapshot: dict[str, Any] | None = None,
) -> str:
    request_payload = _as_dict(request_snapshot)
    response_payload = _as_dict(response_snapshot)
    if topology_mode == TOPOLOGY_MANAGED:
        partner_brand_name = (
            response_payload.get("partner_brand_name")
            or request_payload.get("partner_brand_name")
        )
        if partner_brand_name:
            return str(partner_brand_name)
    return project_shop_identity(fallback_name, actor=CLIENT_ACTOR, topology_mode=topology_mode, fallback="Verified Print Partner")


def project_identity(name: str | None, *, actor: str, topology_mode: str = "managed") -> str:
    """Project participant identity based on observer role and system topology."""
    return project_shop_identity(name, actor=actor, topology_mode=topology_mode, fallback="Verified Print Partner")


def project_participant_name(user_name: str | None, role: str, *, actor: str, topology_mode: str = "managed") -> str:
    """Project a user's name based on their role and the observer's context."""
    if role == SHOP_ACTOR or "shop" in role.lower():
        return project_shop_identity(user_name, actor=actor, topology_mode=topology_mode)
    if role == CLIENT_ACTOR or "client" in role.lower():
        return project_client_identity(user_name, actor=actor, topology_mode=topology_mode)
    return user_name or "Participant"


def project_revised_pricing_snapshot_for_client(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    revised = strip_forbidden_keys(_as_dict(payload), CLIENT_ACTOR)
    if not revised:
        return None
    production_preview = project_production_intelligence(revised.get("production_preview"))
    pricing_summary = project_pricing_breakdown(revised, actor=CLIENT_ACTOR)
    return {
        "production_preview": production_preview,
        "pricing_summary": pricing_summary,
        "visibility": {
            "actor": CLIENT_ACTOR,
            "exposes_internal_economics": False,
        },
    }
