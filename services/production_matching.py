"""Manager-facing production shop matching."""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils.text import slugify

from inventory.models import Machine, Paper
from pricing.models import FinishingRate, PrintingRate
from pricing.services.platform_fee_policy import calculate_financial_split
from services.pricing.engine import calculate_sheet_pricing
from services.pricing.finishing_normalization import (
    is_empty_finishing,
    normalize_finishing_slug,
    resolve_finishing_rate_for_slug,
)
from shops.models import Shop


MAX_MANAGER_MATCHES = 25
SHEET_PRODUCT_TYPES = {
    "business_card",
    "flyer",
    "poster",
    "letterhead",
    "certificate",
    "invitation_card",
    "brochure",
    "sticker",
}
PRODUCT_REQUIRED_FINISHINGS = {
    "business_card": ["cutting"],
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        if value in (None, ""):
            return default
        return Decimal(str(value))
    except Exception:
        return default


def _money(value: Any) -> str | None:
    amount = _decimal(value)
    if amount <= 0:
        return None
    return str(amount.quantize(Decimal("0.01")))


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _normal(value: Any) -> str:
    return str(value or "").strip()


def _normalized_key(value: Any) -> str:
    return slugify(_normal(value)).replace("-", "_")


def _parse_finished_size(payload: dict[str, Any]) -> tuple[int | None, int | None]:
    width = _int(payload.get("width_mm"))
    height = _int(payload.get("height_mm"))
    if width and height:
        return width, height
    raw = _normal(payload.get("finished_size") or payload.get("size"))
    match = re.search(r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)", raw)
    if not match:
        return None, None
    return int(Decimal(match.group(1))), int(Decimal(match.group(2)))


def _requested_gsm(payload: dict[str, Any]) -> int | None:
    explicit = _int(payload.get("requested_gsm") or payload.get("paper_gsm"))
    if explicit:
        return explicit
    for key in ("paper_stock", "cover_stock", "insert_stock"):
        match = re.search(r"(\d{2,4})\s*gsm", _normal(payload.get(key)), flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _requested_paper_category(payload: dict[str, Any]) -> str:
    explicit = _normal(payload.get("requested_paper_category"))
    if explicit:
        return explicit
    stock = _normal(payload.get("paper_stock")).lower()
    if "artcard" in stock or "art card" in stock:
        return "artcard"
    if "gloss" in stock:
        return "gloss"
    if "matt" in stock or "matte" in stock:
        return "matt"
    if "bond" in stock:
        return "bond"
    if "conqueror" in stock:
        return "conqueror"
    if "tictac" in stock or "sticker" in stock:
        return "tictac"
    return ""


def _required_finishing_keys(payload: dict[str, Any]) -> list[str]:
    keys = list(PRODUCT_REQUIRED_FINISHINGS.get(_normalized_key(payload.get("product_type")), []))
    lamination = normalize_finishing_slug(payload.get("lamination") or payload.get("cover_lamination"))
    if lamination and not is_empty_finishing(lamination):
        keys.append(lamination)
    finishings = payload.get("finishings")
    if isinstance(finishings, list):
        for item in finishings:
            if isinstance(item, dict):
                value = item.get("slug") or item.get("key") or item.get("name") or item.get("label")
            else:
                value = item
            key = normalize_finishing_slug(value)
            if key and key not in keys:
                keys.append(key)
    return keys


def _global_missing_fields(payload: dict[str, Any]) -> list[str]:
    missing = []
    if not _normal(payload.get("product_type")):
        missing.append("product_type")
    if not _int(payload.get("quantity")):
        missing.append("quantity")
    width, height = _parse_finished_size(payload)
    if not width or not height:
        missing.append("finished_size")
    if not (_normal(payload.get("paper_stock")) or _requested_gsm(payload) or _requested_paper_category(payload)):
        missing.append("paper_stock")
    return missing


def _shop_queryset():
    return Shop.objects.filter(is_active=True).select_related("owner").order_by("id")[:MAX_MANAGER_MATCHES]


def _machine_fits(machine: Machine, paper: Paper) -> bool:
    width = paper.width_mm or 0
    height = paper.height_mm or 0
    max_width = machine.max_width_mm or 0
    max_height = machine.max_height_mm or 0
    if not width or not height or not max_width or not max_height:
        return True
    return (width <= max_width and height <= max_height) or (height <= max_width and width <= max_height)


def _paper_score(paper: Paper, *, category: str, gsm: int | None) -> tuple[int, int, int, Decimal]:
    category_penalty = 0
    if category and paper.category != category and paper.paper_type.lower() != category.lower():
        category_penalty = 1000
    gsm_penalty = abs(int(paper.gsm or 0) - int(gsm or paper.gsm or 0))
    default_penalty = 0 if paper.is_default else 1
    return category_penalty, gsm_penalty, default_penalty, _decimal(paper.selling_price)


def _candidate_papers(shop: Shop, payload: dict[str, Any]) -> list[Paper]:
    qs = Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0)
    category = _requested_paper_category(payload)
    gsm = _requested_gsm(payload)
    if category:
        exact_qs = qs.filter(Q(category=category) | Q(paper_type__iexact=category))
        if exact_qs.exists():
            qs = exact_qs
    if gsm:
        close_qs = qs.filter(gsm__gte=max(1, gsm - 40), gsm__lte=gsm + 40)
        if close_qs.exists():
            qs = close_qs
    papers = list(qs.order_by("-is_default", "gsm", "selling_price", "id"))
    return sorted(papers, key=lambda paper: _paper_score(paper, category=category, gsm=gsm))


def _resolve_machine(shop: Shop, paper: Paper, payload: dict[str, Any]) -> Machine | None:
    color_mode = _normal(payload.get("color_mode") or payload.get("colour_mode") or "COLOR").upper()
    sides = _normal(payload.get("print_sides") or payload.get("sides") or "SIMPLEX").upper()
    machines = list(
        Machine.objects.filter(shop=shop, is_active=True)
        .filter(Q(min_gsm__isnull=True) | Q(min_gsm__lte=paper.gsm))
        .filter(Q(max_gsm__isnull=True) | Q(max_gsm__gte=paper.gsm))
        .order_by("id")
    )
    fitting = [machine for machine in machines if _machine_fits(machine, paper)]
    rated = [
        machine for machine in fitting
        if PrintingRate.resolve(machine, paper.sheet_size, color_mode, sides, paper=paper)[1] is not None
    ]
    return (rated or fitting or machines or [None])[0]


def _find_finishing(shop: Shop, key: str) -> FinishingRate | None:
    if _normalized_key(key) == "cutting":
        lookup_key = "cutting"
    else:
        lookup_key = normalize_finishing_slug(key)
    key = _normalized_key(lookup_key)
    if not key:
        return None
    rows = FinishingRate.objects.filter(shop=shop, is_active=True).order_by("id")
    for row in rows:
        if key != "cutting":
            if resolve_finishing_rate_for_slug(shop, lookup_key) == row:
                return row
            continue
        candidates = {_normalized_key(row.slug), _normalized_key(row.name)}
        if key in candidates or any(candidate.startswith(key) or key in candidate for candidate in candidates):
            return row
    return None


def _finishing_selections(shop: Shop, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    selections = []
    missing = []
    for key in _required_finishing_keys(payload):
        rule = _find_finishing(shop, key)
        if rule is None:
            missing.extend(["finishing", key])
            continue
        selections.append({"rule": rule, "selected_side": "both"})
    return selections, sorted(set(missing), key=missing.index)


def _location_summary(shop: Shop) -> str:
    parts = [
        _normal(getattr(shop, "service_area", "")),
        _normal(getattr(shop, "city", "")),
    ]
    return ", ".join(part for part in parts if part)


def _diagnostic_row(*, shop: Shop, payload: dict[str, Any], missing: list[str], available: list[str] | None = None, reason: str = "") -> dict[str, Any]:
    missing = sorted(set(missing), key=missing.index)
    return {
        "shop_id": shop.id,
        "shop_name": shop.name,
        "shop_display_name": shop.name,
        "shop_slug": shop.slug or "",
        "shop_location": _location_summary(shop),
        "shop_location_area": getattr(shop, "service_area", "") or getattr(shop, "city", ""),
        "location_summary": _location_summary(shop),
        "location_area": getattr(shop, "service_area", "") or getattr(shop, "city", ""),
        "can_produce": False,
        "production_cost": None,
        "estimated_production_cost": None,
        "estimated_shop_payout": None,
        "currency": getattr(shop, "currency", "KES") or "KES",
        "price_available": False,
        "price_status": "missing_pricing" if "pricing" in missing else "insufficient_data",
        "pricing_source": "insufficient_data",
        "missing_requirements": missing,
        "missing_spec_warnings": missing,
        "available_reasons": available or [],
        "capability_notes": available or [],
        "estimated_turnaround": getattr(shop, "turnaround_statement", "") or None,
        "turnaround_hours": None,
        "turnaround_days": None,
        "turnaround_label": getattr(shop, "turnaround_statement", "") or "",
        "match_type": "diagnostic",
        "match_score": 0,
        "score": 0,
        "is_recommended": False,
        "recommendation_rank": None,
        "recommendation_label": "",
        "explanation": reason or "Shop setup is missing required pricing data.",
        "reason": reason or "Shop setup is missing required pricing data.",
        "product_type": _normal(payload.get("product_type")),
        "preview_snapshot": None,
        "selection": None,
    }


def _priced_row(*, shop: Shop, payload: dict[str, Any], paper: Paper, machine: Machine, preview: dict[str, Any], rank: int | None = None) -> dict[str, Any]:
    totals = _as_dict(preview.get("totals"))
    subtotal = _money(totals.get("subtotal") or totals.get("grand_total"))
    breakdown = _as_dict(preview.get("breakdown"))
    imposition = _as_dict(breakdown.get("imposition"))
    score = float(_decimal(subtotal, Decimal("0"))) if subtotal else 0.0
    selection = {
        "paper_id": paper.id,
        "paper_label": _as_dict(breakdown.get("paper")).get("label") or f"{paper.sheet_size} {paper.gsm}gsm",
        "machine_id": machine.id,
        "machine_name": machine.name,
        "sheet_size": paper.sheet_size,
        "good_sheets": imposition.get("good_sheets") or imposition.get("parent_sheets_required"),
    }
    return {
        "shop_id": shop.id,
        "shop_name": shop.name,
        "shop_display_name": shop.name,
        "shop_slug": shop.slug or "",
        "shop_location": _location_summary(shop),
        "shop_location_area": getattr(shop, "service_area", "") or getattr(shop, "city", ""),
        "location_summary": _location_summary(shop),
        "location_area": getattr(shop, "service_area", "") or getattr(shop, "city", ""),
        "can_produce": True,
        "production_cost": subtotal,
        "estimated_production_cost": subtotal,
        "estimated_shop_payout": subtotal,
        "currency": preview.get("currency") or getattr(shop, "currency", "KES") or "KES",
        "price_available": bool(subtotal),
        "price_status": "priced",
        "pricing_source": "canonical_rate",
        "missing_requirements": [],
        "missing_spec_warnings": [],
        "available_reasons": ["Pricing path available through canonical shop paper, machine, and printing rate."],
        "capability_notes": ["Pricing path available through canonical shop paper, machine, and printing rate."],
        "estimated_turnaround": getattr(shop, "turnaround_statement", "") or None,
        "turnaround_hours": None,
        "turnaround_days": None,
        "turnaround_label": getattr(shop, "turnaround_statement", "") or "",
        "match_type": "canonical_rate",
        "match_score": score,
        "score": score,
        "is_recommended": rank == 1,
        "recommendation_rank": rank,
        "recommendation_label": "Recommended" if rank == 1 else "",
        "explanation": "Lowest production cost among currently priced eligible shops." if rank == 1 else "Eligible production shop with canonical pricing.",
        "reason": "Eligible production shop with canonical pricing.",
        "product_type": _normal(payload.get("product_type")),
        "preview_snapshot": preview,
        "selection": selection,
    }


def _match_shop(shop: Shop, payload: dict[str, Any]) -> dict[str, Any]:
    available = []
    papers = _candidate_papers(shop, payload)
    if not papers:
        return _diagnostic_row(shop=shop, payload=payload, missing=["paper"], reason="No active matching paper stock is configured for this shop.")
    paper = papers[0]
    available.append(f"Matched paper {paper.sheet_size} {paper.gsm}gsm.")

    machine = _resolve_machine(shop, paper, payload)
    if machine is None:
        return _diagnostic_row(shop=shop, payload=payload, missing=["machine"], available=available, reason="No active machine can handle the selected paper.")
    available.append(f"Matched machine {machine.name}.")

    finishing_selections, missing_finishings = _finishing_selections(shop, payload)
    if missing_finishings:
        return _diagnostic_row(shop=shop, payload=payload, missing=missing_finishings, available=available, reason="Required finishing is not configured for this shop.")

    width, height = _parse_finished_size(payload)
    result = calculate_sheet_pricing(
        shop=shop,
        product=None,
        quantity=int(payload.get("quantity") or 0),
        paper=paper,
        machine=machine,
        color_mode=_normal(payload.get("color_mode") or payload.get("colour_mode") or "COLOR").upper(),
        sides=_normal(payload.get("print_sides") or payload.get("sides") or "SIMPLEX").upper(),
        finishing_selections=finishing_selections,
        width_mm=width,
        height_mm=height,
    )
    preview = result.to_dict()
    if not result.can_calculate:
        return _diagnostic_row(
            shop=shop,
            payload=payload,
            missing=["pricing"],
            available=available,
            reason=result.reason or "No active canonical printing rate matches this shop configuration.",
        )
    row = _priced_row(shop=shop, payload=payload, paper=paper, machine=machine, preview=preview)
    row["available_reasons"] = available + row["available_reasons"]
    row["capability_notes"] = row["available_reasons"]
    return row


def _pricing_snapshot(rows: list[dict[str, Any]], *, currency: str) -> dict[str, Any]:
    selected_shops = []
    for row in rows:
        if not row.get("price_available"):
            continue
        selected_shops.append(
            {
                "id": row["shop_id"],
                "slug": row.get("shop_slug") or "",
                "shop_id": row["shop_id"],
                "shop_display_name": row.get("shop_display_name") or row.get("shop_name"),
                "can_produce": row.get("can_produce"),
                "price_available": row.get("price_available"),
                "price_status": row.get("price_status"),
                "production_cost": row.get("production_cost"),
                "preview": row.get("preview_snapshot"),
                "selection": row.get("selection"),
            }
        )
    return {
        "currency": currency,
        "selected_shops": selected_shops,
        "pricing_source": "manager_production_matching",
    }


def build_partner_production_matches(payload):
    payload = _as_dict(payload)
    product_type = _normal(payload.get("product_type"))
    missing_fields = _global_missing_fields(payload)
    if missing_fields:
        return {
            "product_type": product_type,
            "summary": "Complete required specs before production shops can be priced.",
            "missing_fields": missing_fields,
            "results": [],
            "matched_count": 0,
            "results_count": 0,
            "pricing_snapshot": {"currency": "KES", "selected_shops": [], "pricing_source": "insufficient_data"},
            "spec_snapshot": payload,
            "visibility": {
                "audience": "manager",
                "exposes_shop_identity": True,
                "exposes_internal_economics": True,
                "status": "missing_specs",
            },
        }

    if _normalized_key(product_type) not in SHEET_PRODUCT_TYPES:
        return {
            "product_type": product_type,
            "summary": "Production matching currently supports sheet-fed flat products only.",
            "missing_fields": ["product_type"],
            "results": [],
            "matched_count": 0,
            "results_count": 0,
            "pricing_snapshot": {"currency": "KES", "selected_shops": [], "pricing_source": "unsupported_product"},
            "spec_snapshot": payload,
            "visibility": {
                "audience": "manager",
                "exposes_shop_identity": True,
                "exposes_internal_economics": True,
                "status": "unsupported_product",
            },
        }

    rows = [_match_shop(shop, payload) for shop in _shop_queryset()]
    priced_rows = sorted(
        [row for row in rows if row.get("price_available")],
        key=lambda row: _decimal(row.get("production_cost")),
    )
    diagnostic_rows = [row for row in rows if not row.get("price_available")]
    ranked_rows = []
    for index, row in enumerate(priced_rows, start=1):
        row["recommendation_rank"] = index
        row["is_recommended"] = index == 1
        row["recommendation_label"] = "Recommended" if index == 1 else ""
        ranked_rows.append(row)
    ranked_rows.extend(diagnostic_rows)
    currency = (ranked_rows[0].get("currency") if ranked_rows else "KES") or "KES"
    summary = (
        f"{len(priced_rows)} production shop option(s) can price this request."
        if priced_rows
        else "No production shop can price this request yet."
    )
    return {
        "product_type": product_type,
        "summary": summary,
        "missing_fields": [],
        "results": ranked_rows,
        "matched_count": len(priced_rows),
        "results_count": len(ranked_rows),
        "pricing_snapshot": _pricing_snapshot(ranked_rows, currency=currency),
        "spec_snapshot": payload,
        "visibility": {
            "audience": "manager",
            "exposes_shop_identity": True,
            "exposes_internal_economics": True,
            "status": "matched" if priced_rows else "diagnostic_only",
        },
    }


def price_single_shop_for_submission(*, shop: Shop, payload: dict[str, Any]) -> dict[str, Any]:
    return _price_single_shop_row(shop=shop, payload=payload)


def _normalized_single_shop_payload(payload: dict[str, Any]) -> dict[str, Any]:
    payload = _as_dict(payload)
    normalized_payload = {
        **payload,
        "product_type": _normal(payload.get("product_type") or payload.get("job_type")),
        "paper_stock": payload.get("paper_stock")
        or payload.get("paper_preference")
        or " ".join(
            str(part)
            for part in (payload.get("paper_gsm"), payload.get("paper_type"))
            if part not in (None, "")
        ),
        "color_mode": payload.get("color_mode") or payload.get("colour_mode") or "COLOR",
        "print_sides": payload.get("print_sides") or payload.get("sides") or "SIMPLEX",
    }
    if not normalized_payload.get("finished_size") and normalized_payload.get("width_mm") and normalized_payload.get("height_mm"):
        normalized_payload["finished_size"] = f"{normalized_payload['width_mm']}x{normalized_payload['height_mm']}mm"
    return normalized_payload


def _price_single_shop_row(*, shop: Shop, payload: dict[str, Any]) -> dict[str, Any]:
    return _match_shop(shop, _normalized_single_shop_payload(payload))


def get_direct_shop_standard_markup_rate() -> Decimal:
    return Decimal(str(getattr(settings, "DIRECT_SHOP_STANDARD_MARKUP_RATE", Decimal("0.20")))).quantize(Decimal("0.0001"))


def build_direct_shop_pricing(*, shop: Shop, payload: dict[str, Any]) -> dict[str, Any]:
    row = _price_single_shop_row(shop=shop, payload=payload)
    if not row.get("price_available"):
        return {"row": row, "split": None}

    production_cost = _quantize_money(Decimal(str(row["production_cost"])))
    markup_rate = get_direct_shop_standard_markup_rate()
    broker_client_price = _quantize_money(production_cost * (Decimal("1.00") + markup_rate))
    split = calculate_financial_split(
        production_cost=production_cost,
        broker_client_price=broker_client_price,
    )
    row = {
        **row,
        "production_cost": str(split["production_cost"]),
        "estimated_production_cost": str(split["production_cost"]),
        "estimated_shop_payout": str(split["shop_payout"]),
        "direct_shop_markup_rate": str(markup_rate),
        "direct_shop_broker_client_price": str(split["broker_client_price"]),
        "direct_shop_client_total": str(split["client_total"]),
        "direct_shop_printy_fee": str(split["printy_fee"]),
        "direct_shop_broker_payout": str(split["broker_payout"]),
    }
    return {"row": row, "split": split}


def build_public_single_shop_quote_preview(*, shop: Shop, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_payload = _normalized_single_shop_payload(payload)
    product_type = normalized_payload.get("product_type")

    missing_fields = _global_missing_fields(normalized_payload)
    if missing_fields:
        return {
            "status": "missing_specs",
            "can_price": False,
            "missing_fields": missing_fields,
            "product_type": product_type,
            "shop": {
                "name": shop.name,
                "slug": shop.slug or "",
            },
        }

    if _normalized_key(product_type) not in SHEET_PRODUCT_TYPES:
        return {
            "status": "unsupported_product",
            "can_price": False,
            "missing_fields": ["product_type"],
            "product_type": product_type,
            "shop": {
                "name": shop.name,
                "slug": shop.slug or "",
            },
        }

    pricing = build_direct_shop_pricing(shop=shop, payload=normalized_payload)
    row = pricing["row"]
    split = pricing["split"]
    price = split["client_total"] if split else None
    return {
        "status": "priced" if price else row.get("price_status") or "unpriced",
        "can_price": bool(price),
        "missing_fields": row.get("missing_requirements") or [],
        "product_type": product_type,
        "shop": {
            "name": shop.name,
            "slug": shop.slug or "",
        },
        "price": {
            "currency": row.get("currency") or getattr(shop, "currency", "KES") or "KES",
            "total": str(price),
        } if price else None,
        "turnaround": {
            "label": row.get("turnaround_label") or row.get("estimated_turnaround") or "",
            "hours": row.get("turnaround_hours"),
            "days": row.get("turnaround_days"),
        },
        "summary": "Shop rate-card price is available." if price else row.get("reason") or "This shop cannot price the request yet.",
    }
