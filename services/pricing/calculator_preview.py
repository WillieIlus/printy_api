from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from pricing.services.platform_fee_policy import calculate_financial_split
from pricing.services.production_cost_calculator import calculate_client_price_with_waste_setup_and_quantity_tier
from quotes.models import Quote
from services.public_matching import get_booklet_marketplace_matches, get_marketplace_matches

from .calculator_config import get_product_definition, resolve_finished_size, resolve_stock_option
from .finishing_normalization import is_empty_finishing, normalize_finishing_slug
from .urgency import apply_priority_pricing

MONEY_QUANTIZER = Decimal("0.01")
DISPLAY_ROUNDING = Decimal("50")
FALLBACK_MIN_MULTIPLIER = Decimal("1.45")
FALLBACK_MAX_MULTIPLIER = Decimal("1.80")
HISTORY_QUANTITY_TOLERANCE = Decimal("0.20")
MIN_EQUAL_SPREAD_RATE = Decimal("0.10")
PUBLIC_MANAGER_MARKUP_MULTIPLIER = Decimal("1.75")
TURNAROUND_TIER_MULTIPLIERS = {
    "standard": Decimal("1.00"),
    "express": Decimal("1.20"),
    "same_day": Decimal("1.50"),
}
TURNAROUND_TIER_LABELS = {
    "standard": "Standard (5-7 days)",
    "express": "Express (3 days)",
    "same_day": "Same day",
}


def _parse_tier_gsm(raw: str | None) -> int | None:
    if not raw or not raw.endswith("gsm"):
        return None
    try:
        return int(raw[:-3])
    except ValueError:
        return None


PRODUCT_FAMILY_BY_TYPE = {
    "business_card": "flat",
    "flyer": "flat",
    "label_sticker": "flat",
    "letterhead": "flat",
    "booklet": "booklet",
    "large_format": "large_format",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _money(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value)).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    except (ArithmeticError, InvalidOperation, TypeError, ValueError):
        return default


def _stringify_money(value: Decimal | None) -> str | None:
    return str(value.quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)) if value is not None else None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_size_label(value: Any) -> str:
    raw = _normalize_text(value)
    return raw.replace(" ", "")


def _rounded_display_money(value: Decimal) -> Decimal:
    rounded = (value / DISPLAY_ROUNDING).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * DISPLAY_ROUNDING
    return rounded.quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def _format_display_amount(value: Decimal, currency: str) -> str:
    rounded = _rounded_display_money(value)
    integer_value = int(rounded)
    return f"{currency} {integer_value:,}"


def _format_whole_display_amount(value: Decimal, currency: str) -> str:
    rounded = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return f"{currency} {int(rounded):,}"


def _format_range_with_median_text(*, minimum: Decimal, maximum: Decimal, median: Decimal, currency: str) -> str:
    if minimum == maximum:
        return _format_whole_display_amount(median, currency)
    return f"From {_format_whole_display_amount(minimum, currency)} to {_format_whole_display_amount(maximum, currency)}"


def _extract_request_spec(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_type": _normalize_text(payload.get("product_type")),
        "quantity": int(payload.get("quantity") or 0),
        "finished_size": _normalize_size_label(payload.get("finished_size") or payload.get("size_label")),
        "print_sides": _normalize_text(payload.get("print_sides") or payload.get("sides")),
        "paper_gsm": int(payload.get("requested_gsm") or payload.get("paper_gsm") or 0) or None,
        "width_mm": _money(payload.get("width_mm")),
        "height_mm": _money(payload.get("height_mm")),
    }


def _extract_snapshot_spec(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    root = _as_dict(snapshot)
    calculator = _as_dict(root.get("calculator_inputs"))
    details = _as_dict(root.get("request_details"))
    nested = calculator or details or root
    return {
        "product_type": _normalize_text(
            nested.get("product_type")
            or root.get("product_type")
            or _as_dict(root.get("custom_product_snapshot")).get("product_type")
        ),
        "quantity": int(nested.get("quantity") or root.get("quantity") or 0),
        "finished_size": _normalize_size_label(
            nested.get("finished_size")
            or nested.get("size_label")
            or root.get("finished_size")
            or root.get("size_label")
        ),
        "print_sides": _normalize_text(
            nested.get("print_sides")
            or nested.get("sides")
            or root.get("print_sides")
            or root.get("sides")
        ),
        "paper_gsm": int(nested.get("requested_gsm") or nested.get("paper_gsm") or root.get("paper_gsm") or 0) or None,
        "width_mm": _money(nested.get("width_mm") or root.get("width_mm")),
        "height_mm": _money(nested.get("height_mm") or root.get("height_mm")),
    }


def _is_similar_history_spec(request_spec: dict[str, Any], history_spec: dict[str, Any]) -> bool:
    if not history_spec.get("product_type") or history_spec["product_type"] != request_spec["product_type"]:
        return False

    requested_quantity = request_spec.get("quantity") or 0
    history_quantity = history_spec.get("quantity") or 0
    if requested_quantity <= 0 or history_quantity <= 0:
        return False

    minimum = Decimal(str(requested_quantity)) * (Decimal("1.00") - HISTORY_QUANTITY_TOLERANCE)
    maximum = Decimal(str(requested_quantity)) * (Decimal("1.00") + HISTORY_QUANTITY_TOLERANCE)
    if Decimal(str(history_quantity)) < minimum or Decimal(str(history_quantity)) > maximum:
        return False

    requested_size = request_spec.get("finished_size")
    history_size = history_spec.get("finished_size")
    if requested_size and history_size and requested_size != history_size:
        return False

    requested_sides = request_spec.get("print_sides")
    history_sides = history_spec.get("print_sides")
    if requested_sides and history_sides and requested_sides != history_sides:
        return False

    requested_gsm = request_spec.get("paper_gsm")
    history_gsm = history_spec.get("paper_gsm")
    if requested_gsm and history_gsm and abs(int(history_gsm) - int(requested_gsm)) > 20:
        return False

    requested_width = request_spec.get("width_mm")
    requested_height = request_spec.get("height_mm")
    history_width = history_spec.get("width_mm")
    history_height = history_spec.get("height_mm")
    if requested_width and requested_height and history_width and history_height:
        width_ratio = abs(history_width - requested_width) / requested_width if requested_width else Decimal("0")
        height_ratio = abs(history_height - requested_height) / requested_height if requested_height else Decimal("0")
        if width_ratio > Decimal("0.10") or height_ratio > Decimal("0.10"):
            return False

    return True


def _ensure_estimate_spread(minimum: Decimal, maximum: Decimal) -> tuple[Decimal, Decimal, bool]:
    collapsed = minimum == maximum
    if maximum < minimum:
        minimum, maximum = maximum, minimum
        collapsed = minimum == maximum
    if collapsed:
        maximum = (minimum * (Decimal("1.00") + MIN_EQUAL_SPREAD_RATE)).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    return minimum, maximum, collapsed


def _build_history_estimate(payload: dict[str, Any]) -> dict[str, Any] | None:
    request_spec = _extract_request_spec(payload)
    if not request_spec["product_type"] or request_spec["quantity"] <= 0:
        return None

    matching_totals: list[Decimal] = []
    history_rows = (
        Quote.objects.filter(status=Quote.ACCEPTED, financial_split__client_total__isnull=False)
        .select_related("quote_request", "financial_split")
        .order_by("-accepted_at", "-created_at", "-id")[:200]
    )
    for quote in history_rows:
        snapshot = getattr(getattr(quote, "quote_request", None), "request_snapshot", None)
        if not _is_similar_history_spec(request_spec, _extract_snapshot_spec(snapshot)):
            continue
        client_total = _money(quote.financial_split.client_total)
        if client_total is not None:
            matching_totals.append(client_total)

    if not matching_totals:
        return None

    minimum = min(matching_totals)
    maximum = max(matching_totals)
    minimum, maximum, collapsed = _ensure_estimate_spread(minimum, maximum)
    count = len(matching_totals)
    confidence = "high" if count >= 2 else "medium"
    source_label = "Based on recent managed jobs" if count >= 2 else "Estimated market range"
    return {
        "estimate_min": minimum,
        "estimate_max": maximum,
        "confidence_label": confidence,
        "source_label": source_label,
        "display_mode": "from_price" if collapsed else "range",
        "history_count": count,
    }


def _build_shop_band_estimate(response: dict[str, Any]) -> dict[str, Any] | None:
    client_totals: list[Decimal] = []
    for match in _as_list(response.get("matches")):
        preview = _as_dict(match.get("preview"))
        marketplace = _as_dict(preview.get("marketplace_pricing"))
        breakdown = _as_dict(preview.get("breakdown"))
        marketplace = marketplace or _as_dict(breakdown.get("marketplace_pricing"))
        totals = _as_dict(preview.get("totals"))
        client_total = (
            _money(marketplace.get("client_price"))
            or _money(totals.get("grand_total"))
            or _money(match.get("total"))
        )
        if client_total is not None and client_total > 0:
            client_totals.append(client_total)

    if not client_totals:
        response_min = _money(response.get("min_price"))
        response_max = _money(response.get("max_price"))
        if response_min is not None and response_max is not None and response_min > 0 and response_max > 0:
            client_totals.extend([response_min, response_max])

    if not client_totals:
        return None

    minimum, maximum, collapsed = _ensure_estimate_spread(min(client_totals), max(client_totals))
    return {
        "estimate_min": minimum,
        "estimate_max": maximum,
        "confidence_label": "medium",
        "source_label": "Estimated market range",
        "display_mode": "from_price" if collapsed else "range",
    }


def _build_fallback_estimate(response: dict[str, Any]) -> dict[str, Any] | None:
    pricing_breakdown = _as_dict(response.get("pricing_breakdown"))
    base_price = _money(pricing_breakdown.get("base_price"))
    if base_price is None:
        base_price = _money(response.get("total")) or _money(response.get("min_price")) or _money(response.get("max_price"))
    if base_price is None or base_price <= 0:
        return None

    minimum = (base_price * FALLBACK_MIN_MULTIPLIER).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    maximum = (base_price * FALLBACK_MAX_MULTIPLIER).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    minimum, maximum, collapsed = _ensure_estimate_spread(minimum, maximum)
    return {
        "estimate_min": minimum,
        "estimate_max": maximum,
        "confidence_label": "low",
        "source_label": "Estimated Printy price range",
        "display_mode": "from_price" if collapsed else "range",
    }


def compute_eligible_shop_median(matches: list) -> Decimal | None:
    production_totals: list[Decimal] = []
    for match in _as_list(matches):
        row = _as_dict(match)
        preview = _as_dict(row.get("preview"))
        totals = _as_dict(preview.get("totals"))
        production_total = (
            _money(row.get("shop_total"))
            or _money(totals.get("shop_total"))
            or _money(totals.get("production_cost"))
        )
        if production_total is not None and production_total > 0:
            production_totals.append(production_total)

    if not production_totals:
        return None

    ordered = sorted(production_totals)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle].quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    return ((ordered[middle - 1] + ordered[middle]) / Decimal("2")).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def _format_display_price_text(*, minimum: Decimal, maximum: Decimal, currency: str, display_mode: str) -> str:
    if display_mode == "exact_estimate":
        return _format_display_amount(minimum, currency)
    minimum_text = _format_display_amount(minimum, currency)
    maximum_text = _format_display_amount(maximum, currency)
    return f"From {minimum_text} to {maximum_text}"


def _attach_public_estimate(response: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(response)
    median_production_cost = compute_eligible_shop_median(_as_list(updated.get("matches")))

    if median_production_cost is None:
        estimate = _build_fallback_estimate(updated)
        if estimate is None:
            updated["estimate_min"] = None
            updated["estimate_max"] = None
            updated["display_price_text"] = None
            updated["display_mode"] = None
            updated["confidence_label"] = "low"
            updated["source_label"] = "Estimated from market median (no live shop data)"
            return updated

        estimate_min = estimate["estimate_min"]
        estimate_max = estimate["estimate_max"]
        currency = updated.get("currency") or "KES"
        updated["estimate_min"] = _stringify_money(estimate_min)
        updated["estimate_max"] = _stringify_money(estimate_max)
        updated["min_price"] = updated["estimate_min"]
        updated["max_price"] = updated["estimate_max"]
        updated["display_mode"] = estimate["display_mode"]
        updated["display_price_text"] = _format_display_price_text(
            minimum=estimate_min,
            maximum=estimate_max,
            currency=currency,
            display_mode=estimate["display_mode"],
        )
        updated["confidence_label"] = estimate["confidence_label"]
        updated["source_label"] = "Estimated from market median (no live shop data)"
        return updated

    # Do not use accepted quote history for public estimates. Mixing stale
    # historical quotes with live shop rates creates non-monotonic price curves.
    broker_client_price = (median_production_cost * PUBLIC_MANAGER_MARKUP_MULTIPLIER).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    split = calculate_financial_split(
        production_cost=median_production_cost,
        broker_client_price=broker_client_price,
    )
    standard_total = split["client_total"].quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)
    tiers = {
        key: {
            "multiplier": str(multiplier.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "total": _stringify_money((standard_total * multiplier).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)),
            "label": TURNAROUND_TIER_LABELS[key],
        }
        for key, multiplier in TURNAROUND_TIER_MULTIPLIERS.items()
    }
    requested_tier = _normalize_text(payload.get("urgency_type") or "standard").replace("-", "_")
    if requested_tier not in tiers:
        requested_tier = "standard"
    selected_total = _money(tiers[requested_tier]["total"])
    if selected_total is None:
        updated["estimate_min"] = None
        updated["estimate_max"] = None
        updated["display_price_text"] = None
        updated["display_mode"] = None
        updated["confidence_label"] = "low"
        updated["source_label"] = "Estimated Printy price range"
        return updated

    currency = updated.get("currency") or "KES"
    selected_total_text = _stringify_money(selected_total)
    band_estimate = _build_shop_band_estimate(updated)
    range_min = band_estimate["estimate_min"] if band_estimate else selected_total
    range_max = band_estimate["estimate_max"] if band_estimate else selected_total
    if range_max < range_min:
        range_min, range_max = range_max, range_min
    updated["total"] = selected_total_text
    updated["median_price"] = selected_total_text
    updated["estimate_median"] = selected_total_text
    updated["estimate_min"] = _stringify_money(range_min)
    updated["estimate_max"] = _stringify_money(range_max)
    updated["min_price"] = updated["estimate_min"]
    updated["max_price"] = updated["estimate_max"]
    updated["display_mode"] = "range_with_median"
    updated["display_price_text"] = _format_range_with_median_text(
        minimum=range_min,
        maximum=range_max,
        median=selected_total,
        currency=currency,
    )
    updated["confidence_label"] = "configured"
    updated["source_label"] = "Estimated from market median"
    updated["exact_or_estimated"] = True
    updated["turnaround_tiers"] = tiers
    updated["public_pricing"] = {
        "production_cost": _stringify_money(median_production_cost),
        "manager_markup_percent": "75.00",
        "broker_client_price": _stringify_money(broker_client_price),
        "printy_fee": _stringify_money(split["printy_fee"]),
        "standard_total": _stringify_money(standard_total),
        "selected_turnaround": requested_tier,
    }
    return updated


def _sanitize_public_preview(preview: dict[str, Any] | None) -> dict[str, Any] | None:
    source = _as_dict(preview)
    if not source:
        return None
    allowed_keys = (
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
        "production_preview",
        "turnaround_label",
        "human_ready_text",
    )
    return {key: source.get(key) for key in allowed_keys if source.get(key) not in (None, "", [], {})}


def _sanitize_public_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for match in matches:
        row = _as_dict(match)
        sanitized.append(
            {
                "id": int(row.get("id") or 0),
                "shop_id": 0,
                "name": "Verified Print Partner",
                "shop_name": "Verified Print Partner",
                "slug": "partner",
                "shop_slug": "partner",
                "can_calculate": bool(row.get("can_calculate")),
                "can_price_now": bool(row.get("can_price_now")),
                "can_send_quote_request": bool(row.get("can_send_quote_request")),
                "currency": row.get("currency") or "KES",
                "reason": row.get("reason") or "",
                "summary": row.get("summary") or "",
                "missing_fields": row.get("missing_fields") or [],
                "missing_specs": row.get("missing_specs") or row.get("missing_fields") or [],
                "turnaround_hours": row.get("turnaround_hours"),
                "estimated_working_hours": row.get("estimated_working_hours"),
                "estimated_ready_at": row.get("estimated_ready_at"),
                "human_ready_text": row.get("human_ready_text"),
                "turnaround_label": row.get("turnaround_label"),
                "exact_or_estimated": row.get("exact_or_estimated"),
                "matched_specs": row.get("matched_specs") or [],
                "needs_confirmation": row.get("needs_confirmation") or [],
                "closest_alternatives": row.get("closest_alternatives") or [],
                "alternative_suggestions": row.get("alternative_suggestions") or row.get("closest_alternatives") or [],
                "price_range": row.get("price_range"),
                "preview": _sanitize_public_preview(row.get("preview")),
                "production_preview": row.get("production_preview"),
                "pricing_breakdown": None,
            }
        )
    return sanitized


def _sanitize_public_response(response: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(response)
    updated["matches"] = _sanitize_public_matches(_as_list(updated.get("matches")))
    updated["shops"] = updated["matches"]
    updated["selected_shops"] = updated["matches"]
    updated["shop_matches"] = updated["matches"]
    updated["pricing_breakdown"] = None
    return updated


def _has_requested_paper(payload: dict[str, Any], *, booklet: bool = False, prefix: str = "") -> bool:
    if booklet:
        return bool(payload.get(f"{prefix}_stock")) or bool(payload.get(f"requested_{prefix}_paper_category")) or bool(payload.get(f"requested_{prefix}_gsm"))
    return bool(payload.get("paper_stock")) or bool(payload.get("requested_paper_category")) or bool(payload.get("requested_gsm"))


def _required_missing(payload: dict[str, Any], definition: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in definition["required_fields"]:
        if field == "paper_stock":
            if not _has_requested_paper(payload):
                missing.append(field)
            continue
        if field == "cover_stock":
            if not _has_requested_paper(payload, booklet=True, prefix="cover"):
                missing.append(field)
            continue
        if field == "insert_stock":
            if not _has_requested_paper(payload, booklet=True, prefix="insert"):
                missing.append(field)
            continue
        value = payload.get(field)
        if value in (None, "", []):
            missing.append(field)
    return missing


def _build_missing_response(product_type: str, missing_fields: list[str]) -> dict[str, Any]:
    field_labels = {
        "paper_stock": "paper stock or requested paper",
        "cover_stock": "cover stock or requested cover paper",
        "insert_stock": "insert stock or requested insert paper",
        "finished_size": "finished size",
        "print_sides": "print sides",
        "color_mode": "color mode",
        "total_pages": "total pages",
        "material_type": "material",
        "width_mm": "width",
        "height_mm": "height",
    }
    readable = [field_labels.get(field, field.replace("_", " ")) for field in missing_fields]
    message = f"Choose {', '.join(readable)} to price this {product_type.replace('_', ' ')}."
    return {
        "mode": "calculator_public_preview",
        "can_calculate": False,
        "product_type": product_type,
        "price_mode": None,
        "total": None,
        "breakdown": None,
        "currency": "KES",
        "missing_fields": missing_fields,
        "missing_requirements": missing_fields,
        "warnings": [],
        "assumptions": [],
        "message": message,
        "summary": message,
        "matches": [],
        "shops": [],
        "selected_shops": [],
        "matches_count": 0,
        "min_price": None,
        "max_price": None,
        "production_preview": None,
        "pricing_breakdown": None,
        "unsupported_reasons": [],
        "suggestions": [],
        "exact_or_estimated": False,
        "estimate_min": None,
        "estimate_max": None,
        "display_price_text": None,
        "display_mode": None,
        "confidence_label": "low",
        "source_label": "Estimated Printy price range",
    }


def _build_match_note(*, requested_category: str | None, requested_gsm: int | None, matched_label: str | None) -> dict[str, Any] | None:
    if not matched_label:
        return None
    requested_bits = []
    if requested_category:
        requested_bits.append(requested_category.replace("_", " ").title())
    if requested_gsm:
        requested_bits.append(f"{requested_gsm}gsm")
    requested_paper = " ".join(requested_bits).strip() or None
    if not requested_paper:
        return None
    if requested_paper.lower() in matched_label.lower():
        return {
            "requested_paper": requested_paper,
            "matched_paper": matched_label,
            "match_note": "Exact available stock",
            "fit_indicator": "exact",
        }
    return {
        "requested_paper": requested_paper,
        "matched_paper": matched_label,
        "match_note": "Closest available stock",
        "fit_indicator": "closest",
    }


def _attach_flat_match_metadata(response: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    requested_category = payload.get("paper_type")
    requested_gsm = payload.get("paper_gsm")
    updated = deepcopy(response)
    for row in updated.get("matches", []):
        selection = row.get("selection") or {}
        preview = row.get("preview") or {}
        match = _build_match_note(
            requested_category=requested_category,
            requested_gsm=requested_gsm,
            matched_label=selection.get("paper_label") or preview.get("selected_paper_label"),
        )
        if match:
            preview["matched_stock"] = match
            row["preview"] = preview
    return updated


def _attach_booklet_match_metadata(response: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(response)
    for row in updated.get("matches", []):
        selection = row.get("selection") or {}
        preview = row.get("preview") or {}
        matches = []
        cover_match = _build_match_note(
            requested_category=payload.get("cover_paper_type"),
            requested_gsm=payload.get("cover_paper_gsm"),
            matched_label=selection.get("cover_paper_label") or preview.get("selected_cover_paper_label"),
        )
        insert_match = _build_match_note(
            requested_category=payload.get("insert_paper_type"),
            requested_gsm=payload.get("insert_paper_gsm"),
            matched_label=selection.get("insert_paper_label") or preview.get("selected_insert_paper_label"),
        )
        if cover_match:
            matches.append({"slot": "cover", **cover_match})
        if insert_match:
            matches.append({"slot": "insert", **insert_match})
        if matches:
            preview["matched_stock"] = matches
            row["preview"] = preview
    return updated


def _extract_production_preview(matches: list[dict[str, Any]], product_type: str) -> dict[str, Any] | None:
    if not matches:
        return None

    top_match = matches[0]
    existing_projection = top_match.get("production_preview")
    if isinstance(existing_projection, dict) and existing_projection:
        return existing_projection
    preview_data = top_match.get("preview") or {}
    preview_projection = preview_data.get("production_preview") or {}
    if isinstance(preview_projection, dict) and preview_projection:
        return preview_projection
    breakdown = preview_data.get("breakdown") or {}
    imposition = preview_data.get("imposition") or breakdown.get("imposition") or {}
    paper = breakdown.get("paper") or {}
    finishing_rows = breakdown.get("finishings") or preview_data.get("finishings") or []
    warnings = preview_data.get("explanations") or preview_data.get("warnings") or []

    result: dict[str, Any] = {
        "pieces_per_sheet": imposition.get("copies_per_sheet") or preview_data.get("copies_per_sheet"),
        "sheets_required": imposition.get("good_sheets") or preview_data.get("good_sheets"),
        "parent_sheet": imposition.get("sheet_size") or imposition.get("sheet_name") or paper.get("sheet_size") or preview_data.get("parent_sheet_name"),
        "imposition_label": imposition.get("explanation") or preview_data.get("reason"),
        "size_label": paper.get("label") or paper.get("sheet_size"),
        "quantity": preview_data.get("quantity"),
        "cutting_required": True if product_type in ["business_card", "flyer", "label_sticker"] else None,
        "selected_finishings": [f.get("name") for f in finishing_rows if f.get("name")],
        "suggested_finishings": [],
        "warnings": warnings,
    }

    if product_type == "large_format":
        roll_usage = breakdown.get("roll_usage") or {}
        dimensions = breakdown.get("dimensions") or {}
        pricing = breakdown.get("pricing") or {}
        result.update({
            "size_label": preview_data.get("size_label") or result.get("size_label"),
            "roll_width_m": (
                round(float(roll_usage.get("roll_width_mm")) / 1000, 3)
                if roll_usage.get("roll_width_mm") not in (None, "")
                else None
            ),
            "roll_width_mm": roll_usage.get("roll_width_mm"),
            "items_per_row": roll_usage.get("items_per_row") or preview_data.get("items_per_row"),
            "rows": roll_usage.get("rows") or preview_data.get("rows"),
            "used_length_m": preview_data.get("used_length_m"),
            "orientation": roll_usage.get("orientation") or preview_data.get("orientation"),
            "input_size_m": {
                "width": round(float(dimensions.get("width_mm")) / 1000, 3),
                "height": round(float(dimensions.get("height_mm")) / 1000, 3),
            } if dimensions.get("width_mm") and dimensions.get("height_mm") else None,
            "charged_area_m2": preview_data.get("charged_area_m2") or pricing.get("charged_area_m2"),
            "printed_area_m2": preview_data.get("printed_area_m2"),
            "waste_area_m2": preview_data.get("waste_area_m2"),
            "overlap_area_m2": preview_data.get("overlap_area_m2"),
            "tiling": preview_data.get("tiling") or breakdown.get("tiling"),
        })
        return result

    if product_type == "booklet":
        booklet_bd = breakdown.get("booklet") or {}
        cover_bd = breakdown.get("cover") or {}
        insert_bd = breakdown.get("inserts") or {}
        result.update({
            "booklet_input_pages": preview_data.get("input_pages") or booklet_bd.get("requested_pages"),
            "booklet_normalized_pages": preview_data.get("normalized_pages") or booklet_bd.get("normalized_pages"),
            "booklet_blank_pages_added": preview_data.get("blank_pages_added") or booklet_bd.get("blank_pages_added"),
            "booklet_cover_pages": preview_data.get("cover_pages") or booklet_bd.get("cover_pages"),
            "booklet_insert_pages": preview_data.get("insert_pages") or booklet_bd.get("insert_pages"),
            "booklet_cover_sheets": preview_data.get("cover_sheets") or booklet_bd.get("cover_sheets"),
            "booklet_insert_sheets": preview_data.get("insert_sheets") or booklet_bd.get("insert_sheets"),
            "booklet_binding_label": booklet_bd.get("binding_label"),
            "booklet_cover_paper_label": (cover_bd.get("paper") or {}).get("label"),
            "booklet_insert_paper_label": (insert_bd.get("paper") or {}).get("label"),
        })

    return result


def _extract_pricing_breakdown(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not matches:
        return None

    top_match = matches[0]
    existing_projection = top_match.get("pricing_breakdown")
    if isinstance(existing_projection, dict) and existing_projection:
        return existing_projection
    preview_data = top_match.get("preview") or {}
    preview_projection = preview_data.get("pricing_breakdown") or {}
    if isinstance(preview_projection, dict) and preview_projection:
        return {
            "currency": top_match.get("currency", "KES"),
            "base_price": None,
            "client_price": None,
            "paper_price": None,
            "print_price_front": None,
            "print_price_back": None,
            "total_per_sheet": None,
            "estimated_total": None,
            "price_range": None,
            "formula": None,
            "method": preview_projection.get("method"),
            "rate": preview_projection.get("rate"),
            "charged_area_m2": preview_projection.get("charged_area_m2"),
            "charged_length_m": preview_projection.get("charged_length_m"),
            "minimum_charge": preview_projection.get("minimum_charge"),
            "minimum_charge_applied": preview_projection.get("minimum_charge_applied"),
            "lines": [],
        }
    breakdown = preview_data.get("breakdown") or {}
    per_sheet = preview_data.get("per_sheet_pricing") or breakdown.get("per_sheet_pricing") or {}
    totals = preview_data.get("totals") or {}
    line_items = (preview_data.get("calculation_result") or {}).get("line_items") or []
    pricing = breakdown.get("pricing") or preview_data.get("pricing") or {}
    material = breakdown.get("material") or {}
    material_rate = pricing.get("rate") if pricing.get("rate") is not None else material.get("rate_per_unit")
    marketplace = breakdown.get("marketplace_pricing") or preview_data.get("marketplace_pricing") or {}
    pricing_lines = [
        {
            "label": item.get("label") or item.get("code") or "Line item",
            "amount": item.get("amount"),
            "formula": item.get("formula"),
        }
        for item in line_items
    ]
    pricing_lines.extend(
        {
            "label": line.get("label") or "Marketplace line",
            "amount": line.get("amount"),
            "formula": None,
        }
        for line in marketplace.get("lines") or []
    )

    return {
        "currency": top_match.get("currency", "KES"),
        "base_price": marketplace.get("base_price") or totals.get("shop_total"),
        "client_price": marketplace.get("client_price") or totals.get("grand_total"),
        "paper_price": per_sheet.get("paper_price"),
        "print_price_front": per_sheet.get("print_price_front"),
        "print_price_back": per_sheet.get("print_price_back"),
        "total_per_sheet": per_sheet.get("total_per_sheet"),
        "estimated_total": totals.get("grand_total"),
        "price_range": None,
        "formula": per_sheet.get("formula"),
        "method": pricing.get("method"),
        "rate": material_rate,
        "charged_area_m2": pricing.get("charged_area_m2"),
        "charged_length_m": pricing.get("charged_length_m"),
        "minimum_charge": pricing.get("minimum_charge"),
        "minimum_charge_applied": pricing.get("minimum_charge_applied"),
        "lines": pricing_lines,
    }


def _apply_urgency_to_response(response: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    urgency_type = payload.get("urgency_type")
    turnaround_hours = payload.get("turnaround_hours")
    requested_deadline = payload.get("requested_deadline")
    requested_delivery_time = payload.get("requested_delivery_time")

    if not urgency_type and not requested_deadline and not requested_delivery_time:
        return response

    updated = deepcopy(response)
    matches = []
    for row in updated.get("matches", []):
        match = dict(row)
        preview = match.get("preview")
        if isinstance(preview, dict):
            match["preview"] = apply_priority_pricing(
                preview,
                urgency_type=urgency_type,
                turnaround_hours=turnaround_hours,
                turnaround_label=preview.get("turnaround_label"),
                requested_deadline=requested_deadline,
                requested_delivery_time=requested_delivery_time,
            )
        matches.append(match)
    updated["matches"] = matches
    updated["shops"] = matches
    updated["selected_shops"] = matches
    updated["production_preview"] = _extract_production_preview(matches, updated.get("product_type") or payload.get("product_type") or "")
    updated["pricing_breakdown"] = _extract_pricing_breakdown(matches)

    if updated.get("pricing_breakdown"):
        breakdown = dict(updated["pricing_breakdown"])
        for row in matches[:1]:
            preview = row.get("preview") or {}
            breakdown["urgency_type"] = preview.get("urgency_type")
            breakdown["urgency_fee"] = preview.get("urgency_fee")
            breakdown["after_hours_fee"] = preview.get("after_hours_fee")
            breakdown["operational_priority_level"] = preview.get("operational_priority_level")
            break
        updated["pricing_breakdown"] = breakdown

    if matches:
        totals = _as_dict((matches[0].get("preview") or {}).get("totals"))
        updated["total"] = totals.get("grand_total") or updated.get("total")
        all_totals = [
            value for value in [
                _as_dict((match.get("preview") or {}).get("totals")).get("grand_total")
                for match in matches
            ] if value is not None
        ]
        if all_totals:
            try:
                numeric_totals = [float(value) for value in all_totals]
                updated["min_price"] = f"{min(numeric_totals):.2f}"
                updated["max_price"] = f"{max(numeric_totals):.2f}"
            except Exception:
                pass
    return updated


def _production_cost_spec_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("production_cost_inputs", "production_cost_spec"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            required = {"quantity", "yield_per_sheet", "paper_cost_per_sheet", "click_charge_per_sheet"}
            if required.issubset(candidate.keys()):
                return candidate
    return None


def _attach_canonical_public_price(response: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    spec = _production_cost_spec_from_payload(payload)
    if not spec:
        return response
    pricing = calculate_client_price_with_waste_setup_and_quantity_tier(spec)
    final_price = _stringify_money(pricing["final_client_price"])
    updated = dict(response)
    updated["total"] = final_price
    updated["min_price"] = final_price
    updated["max_price"] = final_price
    updated["estimate_min"] = final_price
    updated["estimate_max"] = final_price
    updated["display_price_text"] = _format_display_amount(pricing["final_client_price"], updated.get("currency") or "KES")
    updated["display_mode"] = "fixed"
    updated["confidence_label"] = "configured"
    updated["source_label"] = "Configured Printy pricing policy"
    updated["exact_or_estimated"] = True
    updated["pricing_breakdown"] = None
    return updated


def build_public_calculator_preview(payload: dict[str, Any]) -> dict[str, Any]:
    product_type = (payload.get("product_type") or "").strip()
    definition = get_product_definition(product_type)
    if not definition:
        msg = "Select a supported product type."
        return {
            "mode": "calculator_public_preview",
            "can_calculate": False,
            "product_type": product_type or None,
            "price_mode": None,
            "missing_fields": ["product_type"],
            "missing_requirements": ["product_type"],
            "warnings": [],
            "assumptions": [],
            "message": msg,
            "summary": msg,
            "matches": [],
            "shops": [],
            "selected_shops": [],
            "matches_count": 0,
            "min_price": None,
            "max_price": None,
            "production_preview": None,
            "pricing_breakdown": None,
            "unsupported_reasons": [],
            "suggestions": [],
            "exact_or_estimated": False,
            "estimate_min": None,
            "estimate_max": None,
            "display_price_text": None,
            "display_mode": None,
            "confidence_label": "low",
            "source_label": "Estimated Printy price range",
        }

    missing_fields = _required_missing(payload, definition)
    if missing_fields:
        return _build_missing_response(product_type, missing_fields)

    if product_type == "large_format":
        width_mm = payload.get("width_mm")
        height_mm = payload.get("height_mm")
        extra_missing: list[str] = []
        if not width_mm:
            extra_missing.append("width_mm")
        if not height_mm:
            extra_missing.append("height_mm")
        if extra_missing:
            return _build_missing_response(product_type, extra_missing)

        request_payload = {
            "calculator_mode": "marketplace",
            "product_family": "large_format",
            "pricing_mode": "custom",
            "product_pricing_mode": "LARGE_FORMAT",
            "product_subtype": payload.get("product_subtype") or definition["defaults"].get("product_subtype") or "banner",
            "quantity": payload.get("quantity"),
            "width_mm": width_mm,
            "height_mm": height_mm,
            "material_type": payload.get("material_type"),
            "finishing_slugs": [
                value
                for value in [
                    normalize_finishing_slug(payload.get("lamination")) if not is_empty_finishing(payload.get("lamination")) else None,
                    payload.get("cut_type"),
                ]
                if value
            ],
            "turnaround_hours": payload.get("turnaround_hours"),
        }
        response = get_marketplace_matches(request_payload)
        response["product_type"] = product_type
        response["can_calculate"] = bool(response.get("matches_count"))
        response["price_mode"] = "exact" if response.get("exact_or_estimated") else "estimate"
        response["missing_fields"] = response.get("missing_requirements", [])
        matches = response.get("matches", [])
        response["production_preview"] = _extract_production_preview(matches, product_type)
        response["pricing_breakdown"] = _extract_pricing_breakdown(matches)
        response = _apply_urgency_to_response(response, request_payload)
        response = _attach_canonical_public_price(_attach_public_estimate(response, payload), payload)
        return _sanitize_public_response(response)

    finished_size_raw = payload.get("finished_size") or ""
    custom_warnings: list[str] = []

    paper_selection_mode = payload.get("paper_selection_mode", "configured")
    if paper_selection_mode == "custom_request":
        custom_warnings.append("Requested paper needs shop confirmation.")

    if finished_size_raw == "custom":
        custom_width = payload.get("custom_width_mm") or payload.get("width_mm")
        custom_height = payload.get("custom_height_mm") or payload.get("height_mm")
        if not custom_width or not custom_height:
            return _build_missing_response(product_type, ["custom_width_mm", "custom_height_mm"])
        size = {"width_mm": float(custom_width), "height_mm": float(custom_height)}
        custom_warnings.append("Custom size will be priced from actual dimensions.")
    else:
        size = resolve_finished_size(product_type, finished_size_raw)
        if not size:
            return _build_missing_response(product_type, ["finished_size"])

    if product_type == "booklet":
        cover_stock_raw = payload.get("cover_stock") or ""
        insert_stock_raw = payload.get("insert_stock") or ""
        cover_stock = resolve_stock_option(cover_stock_raw, usage="cover")
        insert_stock = resolve_stock_option(insert_stock_raw, usage="insert")
        cover_tier_gsm = _parse_tier_gsm(cover_stock_raw) if not cover_stock else None
        insert_tier_gsm = _parse_tier_gsm(insert_stock_raw) if not insert_stock else None
        color_mode = payload.get("color_mode") or "COLOR"
        request_payload = {
            "product_family": "booklet",
            "quantity": payload.get("quantity"),
            "total_pages": payload.get("total_pages"),
            "binding_type": payload.get("binding_type") or definition["defaults"].get("binding_type"),
            "cover_paper_type": payload.get("requested_cover_paper_category") or (cover_stock or {}).get("category"),
            "cover_paper_gsm": payload.get("requested_cover_gsm") or (cover_stock or {}).get("gsm") or cover_tier_gsm,
            "insert_paper_type": payload.get("requested_insert_paper_category") or (insert_stock or {}).get("category"),
            "insert_paper_gsm": payload.get("requested_insert_gsm") or (insert_stock or {}).get("gsm") or insert_tier_gsm,
            "cover_lamination_mode": payload.get("cover_lamination") or definition["defaults"].get("cover_lamination"),
            "color_mode": color_mode,
            "width_mm": size["width_mm"],
            "height_mm": size["height_mm"],
            "turnaround_hours": payload.get("turnaround_hours"),
        }
        response = get_booklet_marketplace_matches(request_payload)
        response["product_type"] = product_type
        response["can_calculate"] = bool(response.get("matches_count"))
        response["price_mode"] = "exact" if response.get("exact_or_estimated") else "estimate"
        response["missing_fields"] = response.get("missing_requirements", [])
        if custom_warnings:
            response["warnings"] = list(response.get("warnings") or []) + custom_warnings

        # Attach normalized previews
        matches = response.get("matches", [])
        response["production_preview"] = _extract_production_preview(matches, product_type)
        response["pricing_breakdown"] = _extract_pricing_breakdown(matches)
        response = _apply_urgency_to_response(_attach_booklet_match_metadata(response, request_payload), request_payload)
        response = _attach_canonical_public_price(_attach_public_estimate(response, payload), payload)
        return _sanitize_public_response(response)

    paper_stock_raw = payload.get("paper_stock") or ""
    stock = resolve_stock_option(paper_stock_raw, usage="sticker" if product_type == "label_sticker" else "")
    tier_gsm: int | None = None
    if stock is None and paper_stock_raw.endswith("gsm"):
        try:
            tier_gsm = int(paper_stock_raw[:-3])
        except ValueError:
            pass
    is_custom_size = finished_size_raw == "custom"
    request_payload = {
        "calculator_mode": "marketplace",
        "product_family": PRODUCT_FAMILY_BY_TYPE[product_type],
        "pricing_mode": "custom",
        "product_pricing_mode": "SHEET",
        "quantity": payload.get("quantity"),
        "size_mode": "custom" if is_custom_size else "standard",
        "size_label": None if is_custom_size else finished_size_raw,
        "width_mm": size["width_mm"],
        "height_mm": size["height_mm"],
        "sides": payload.get("print_sides") or definition["defaults"].get("print_sides"),
        "color_mode": payload.get("color_mode") or definition["defaults"].get("color_mode"),
        "paper_type": payload.get("requested_paper_category") or (stock or {}).get("category"),
        "paper_gsm": payload.get("requested_gsm") or (stock or {}).get("gsm") or tier_gsm,
        "finishing_slugs": [
            value
            for value in [
                normalize_finishing_slug(payload.get("lamination")) if not is_empty_finishing(payload.get("lamination")) else None,
                payload.get("folding") if payload.get("folding") not in {None, "", "none"} else None,
                "corner-rounding" if payload.get("corner_rounding") else None,
                payload.get("cut_type"),
            ]
            if value
        ],
        "turnaround_hours": payload.get("turnaround_hours"),
    }
    response = get_marketplace_matches(request_payload)
    response["product_type"] = product_type
    response["can_calculate"] = bool(response.get("matches_count"))
    response["price_mode"] = "exact" if response.get("exact_or_estimated") else "estimate"
    response["missing_fields"] = response.get("missing_requirements", [])
    if custom_warnings:
        response["warnings"] = list(response.get("warnings") or []) + custom_warnings

    # Attach normalized previews
    matches = response.get("matches", [])
    response["production_preview"] = _extract_production_preview(matches, product_type)
    response["pricing_breakdown"] = _extract_pricing_breakdown(matches)
    response = _apply_urgency_to_response(_attach_flat_match_metadata(response, request_payload), request_payload)
    response = _attach_canonical_public_price(_attach_public_estimate(response, payload), payload)
    return _sanitize_public_response(response)
