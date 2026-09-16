"""Public, anonymized matching against configured production shops."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import Q

from inventory.models import Machine, Paper
from pricing.models import PrintingRate
from services.pricing.engine import calculate_sheet_pricing
from services.pricing.finishing_normalization import normalize_finishing_slug, resolve_finishing_rate_for_slug
from services.pricing.marketplace_pricing import apply_marketplace_pricing_to_preview
from shops.models import Shop


MAX_PUBLIC_MATCHES = 12


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _positive_money(value: Any) -> Decimal | None:
    amount = _decimal(value)
    return amount if amount > 0 else None


def _shop_queryset():
    return Shop.objects.filter(is_active=True, is_public=True).order_by("id")


def _machine_fits(machine: Machine, paper: Paper) -> bool:
    width = paper.width_mm or 0
    height = paper.height_mm or 0
    max_width = machine.max_width_mm or 0
    max_height = machine.max_height_mm or 0
    if not width or not height or not max_width or not max_height:
        return True
    return (width <= max_width and height <= max_height) or (height <= max_width and width <= max_height)


def _paper_score(paper: Paper, *, paper_type: str | None, paper_gsm: int | None) -> tuple[int, int, int]:
    category_penalty = 0 if not paper_type or paper.category == paper_type or paper.paper_type == paper_type else 1000
    gsm_penalty = abs(int(paper.gsm or 0) - int(paper_gsm or paper.gsm or 0))
    default_penalty = 0 if paper.is_default else 1
    return category_penalty, gsm_penalty, default_penalty


def _candidate_papers(shop: Shop, payload: dict[str, Any]) -> list[Paper]:
    qs = Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0)
    paper_type = (payload.get("paper_type") or "").strip()
    paper_gsm = int(payload.get("paper_gsm") or 0) or None

    if paper_type:
        exact_qs = qs.filter(Q(category=paper_type) | Q(paper_type=paper_type))
        if exact_qs.exists():
            qs = exact_qs
    if paper_gsm:
        close_qs = qs.filter(gsm__gte=max(1, paper_gsm - 40), gsm__lte=paper_gsm + 40)
        if close_qs.exists():
            qs = close_qs

    papers = list(qs.order_by("-is_default", "gsm", "selling_price", "id"))
    return sorted(papers, key=lambda paper: _paper_score(paper, paper_type=paper_type or None, paper_gsm=paper_gsm))


def _resolve_machine(shop: Shop, paper: Paper, payload: dict[str, Any]) -> Machine | None:
    color_mode = payload.get("color_mode") or "COLOR"
    sides = payload.get("sides") or "SIMPLEX"
    machines = (
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
    return (rated or fitting or list(machines))[:1][0] if (rated or fitting or list(machines)) else None


def _finishing_selections(shop: Shop, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    selections = []
    missing = []
    for slug in payload.get("finishing_slugs") or []:
        canonical_slug = normalize_finishing_slug(slug)
        rule = resolve_finishing_rate_for_slug(shop, canonical_slug)
        if rule:
            selections.append({"rule": rule, "selected_side": "both"})
        else:
            missing.append(canonical_slug)
    return selections, missing


def _public_match(index: int, shop: Shop, preview: dict[str, Any]) -> dict[str, Any]:
    totals = preview.get("totals") or {}
    total = _positive_money(totals.get("grand_total"))
    return {
        "id": index,
        "shop_id": shop.id,
        "name": "Verified Print Partner",
        "shop_name": "Verified Print Partner",
        "slug": "partner",
        "shop_slug": "partner",
        "currency": preview.get("currency") or getattr(shop, "currency", "KES") or "KES",
        "can_calculate": bool(total),
        "can_price_now": bool(total),
        "can_send_quote_request": bool(total),
        "reason": "" if total else "Configured shop needs a matching paper, machine, and printing rate.",
        "summary": "Verified public production capacity matched this specification." if total else "",
        "missing_fields": [] if total else ["pricing_rate"],
        "missing_specs": [] if total else ["pricing_rate"],
        "exact_or_estimated": bool(total),
        "preview": preview,
        "production_preview": {
            "pieces_per_sheet": preview.get("copies_per_sheet"),
            "sheets_required": preview.get("good_sheets"),
            "parent_sheet": preview.get("parent_sheet_name"),
        },
        "price_range": str(total) if total else None,
    }


def build_public_match_payload(payload):
    matches: list[dict[str, Any]] = []
    for shop in _shop_queryset():
        for paper in _candidate_papers(shop, payload)[:6]:
            machine = _resolve_machine(shop, paper, payload)
            if not machine:
                continue
            finishing_selections, missing_finishings = _finishing_selections(shop, payload)
            if missing_finishings:
                continue
            result = calculate_sheet_pricing(
                shop=shop,
                product=None,
                quantity=int(payload.get("quantity") or 0),
                paper=paper,
                machine=machine,
                color_mode=payload.get("color_mode") or "COLOR",
                sides=payload.get("sides") or "SIMPLEX",
                finishing_selections=finishing_selections,
                width_mm=int(payload.get("width_mm") or 0),
                height_mm=int(payload.get("height_mm") or 0),
            )
            if not result.can_calculate:
                continue
            preview = apply_marketplace_pricing_to_preview(result.to_dict(), shop=shop)
            total = _positive_money((preview.get("totals") or {}).get("grand_total"))
            if not total:
                continue
            matches.append(_public_match(len(matches) + 1, shop, preview))
            break
        if len(matches) >= MAX_PUBLIC_MATCHES:
            break

    totals = [
        _positive_money(((match.get("preview") or {}).get("totals") or {}).get("grand_total"))
        for match in matches
    ]
    totals = [amount for amount in totals if amount is not None]
    return {
        "matches": matches,
        "matches_count": len(matches),
        "request": payload,
        "status": "matched" if matches else "no_matches",
        "min_price": str(min(totals)) if totals else None,
        "max_price": str(max(totals)) if totals else None,
        "exact_or_estimated": bool(matches),
        "currency": matches[0]["currency"] if matches else "KES",
    }


def build_public_booklet_match_payload(payload):
    return {"matches": [], "matches_count": 0, "request": payload, "status": "booklet_matching_pending"}


def get_marketplace_matches(payload):
    return build_public_match_payload(payload)


def get_booklet_marketplace_matches(payload):
    return build_public_booklet_match_payload(payload)


def get_shop_specific_preview(shop, payload):
    response = build_public_match_payload(payload)
    response["fixed_shop_preview"] = {
        "option_label": "Production option 1",
        "can_produce": bool(response["matches_count"]),
        "summary": "Single-shop public previews are anonymized.",
    }
    return response


def recompute_shop_match_readiness(shop):
    return None
