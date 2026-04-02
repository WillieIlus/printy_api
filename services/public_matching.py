from __future__ import annotations

from decimal import Decimal
from typing import Any

from catalog.models import Product
from common.geo import haversine_km
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, Material, PrintingRate
from services.pricing.engine import calculate_large_format_pricing, calculate_sheet_pricing
from shops.models import Shop


MAX_PUBLIC_MATCHES = 6


def recompute_shop_match_readiness(shop: Shop) -> bool:
    has_sheet_path = (
        Machine.objects.filter(shop=shop, is_active=True).exists()
        and Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0).exists()
        and PrintingRate.objects.filter(machine__shop=shop, is_active=True).exists()
    )
    has_large_format_path = Material.objects.filter(shop=shop, is_active=True, selling_price__gt=0).exists()
    has_catalog_products = Product.objects.filter(shop=shop, is_active=True, status="PUBLISHED").exists()

    pricing_ready = has_sheet_path or has_large_format_path
    supports_catalog_requests = bool(has_catalog_products and pricing_ready)
    supports_custom_requests = bool(pricing_ready)
    public_match_ready = bool(shop.is_public and shop.is_active and pricing_ready and (supports_catalog_requests or supports_custom_requests))

    Shop.objects.filter(pk=shop.pk).update(
        pricing_ready=pricing_ready,
        public_match_ready=public_match_ready,
        supports_catalog_requests=supports_catalog_requests,
        supports_custom_requests=supports_custom_requests,
    )

    shop.pricing_ready = pricing_ready
    shop.public_match_ready = public_match_ready
    shop.supports_catalog_requests = supports_catalog_requests
    shop.supports_custom_requests = supports_custom_requests
    return pricing_ready


def get_marketplace_matches(payload: dict[str, Any]) -> dict[str, Any]:
    candidate_shops = list(filter_candidate_shops(payload))
    rows = [try_preview_for_shop(shop, payload) for shop in candidate_shops]
    rows = [row for row in rows if row is not None]

    successful_rows = [row for row in rows if row["can_calculate"]]
    failed_rows = [row for row in rows if not row["can_calculate"]]

    successful_rows.sort(key=lambda row: (-row["similarity_score"], _as_decimal(row.get("total")), row["name"]))
    failed_rows.sort(key=lambda row: (-row["similarity_score"], len(row.get("missing_fields", [])), row["name"]))

    selected_rows = successful_rows[:MAX_PUBLIC_MATCHES]
    missing_requirements = _unique_strings(field for row in failed_rows for field in row.get("missing_fields", []))
    unsupported_reasons = _unique_strings(row.get("reason") for row in failed_rows if row.get("reason"))
    totals = [_as_decimal(row.get("total")) for row in successful_rows if row.get("total")]

    return {
        "mode": "marketplace",
        "matches_count": len(successful_rows),
        "shops": selected_rows,
        "selected_shops": selected_rows,
        "min_price": _format_decimal(min(totals)) if totals else None,
        "max_price": _format_decimal(max(totals)) if totals else None,
        "currency": selected_rows[0]["currency"] if selected_rows else "KES",
        "missing_requirements": missing_requirements,
        "unsupported_reasons": unsupported_reasons,
        "summary": _build_marketplace_summary(successful_rows, failed_rows),
        "exact_or_estimated": bool(selected_rows) and all(row.get("exact_or_estimated", False) for row in selected_rows),
    }


def get_shop_specific_preview(shop: Shop, payload: dict[str, Any]) -> dict[str, Any]:
    row = try_preview_for_shop(shop, payload)
    selected_rows = [row] if row else []
    return {
        "mode": "single-shop",
        "matches_count": 1 if row and row["can_calculate"] else 0,
        "shops": selected_rows,
        "selected_shops": selected_rows,
        "fixed_shop_preview": row,
        "min_price": row.get("total") if row and row["can_calculate"] else None,
        "max_price": row.get("total") if row and row["can_calculate"] else None,
        "currency": row.get("currency", getattr(shop, "currency", "KES")) if row else getattr(shop, "currency", "KES"),
        "missing_requirements": row.get("missing_fields", []) if row else [],
        "unsupported_reasons": [row["reason"]] if row and row.get("reason") and not row["can_calculate"] else [],
        "summary": row["reason"] if row and row.get("reason") else ("Preview ready." if row and row["can_calculate"] else "Preview unavailable."),
        "exact_or_estimated": bool(row and row.get("exact_or_estimated")),
    }


def filter_candidate_shops(payload: dict[str, Any]):
    queryset = Shop.objects.filter(public_match_ready=True, is_active=True, is_public=True)

    if payload.get("pricing_mode") == "catalog":
        queryset = queryset.filter(supports_catalog_requests=True)
    else:
        queryset = queryset.filter(supports_custom_requests=True)

    lat = payload.get("lat")
    lng = payload.get("lng")
    radius = payload.get("radius_km") or 50
    if lat is not None and lng is not None:
        shop_ids = []
        for shop in queryset:
            if shop.latitude is None or shop.longitude is None:
                continue
            distance = haversine_km(float(lat), float(lng), float(shop.latitude), float(shop.longitude))
            if distance <= radius:
                shop_ids.append(shop.id)
        queryset = queryset.filter(id__in=shop_ids)

    return queryset.distinct()


def try_preview_for_shop(shop: Shop, payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("pricing_mode") == "catalog":
        product_id = payload.get("product_id")
        if not product_id:
            return None
        product = Product.objects.filter(id=product_id, shop=shop, is_active=True, status="PUBLISHED").first()
        if not product:
            return None
        return _preview_catalog_for_shop(shop, product, payload)
    return _preview_custom_for_shop(shop, payload)


def _preview_catalog_for_shop(shop: Shop, product: Product, payload: dict[str, Any]) -> dict[str, Any]:
    finishing_selections, missing_finishings = _resolve_finishings(shop, payload)
    product_pricing_mode = product.pricing_mode

    if product_pricing_mode == "LARGE_FORMAT":
        material = _pick_material(shop, payload)
        if not material:
            return _build_shop_row(
                shop,
                can_calculate=False,
                reason="No compatible material is priced for this shop.",
                missing_fields=["material", *missing_finishings],
                similarity_score=_shop_similarity_score(shop, payload, False),
            )

        width_mm = int(payload.get("width_mm") or product.default_finished_width_mm or 0)
        height_mm = int(payload.get("height_mm") or product.default_finished_height_mm or 0)
        if not width_mm or not height_mm:
            return _build_shop_row(
                shop,
                can_calculate=False,
                reason="Finished size is required for large-format pricing.",
                missing_fields=["width_mm", "height_mm", *missing_finishings],
                similarity_score=_shop_similarity_score(shop, payload, False),
            )

        result = calculate_large_format_pricing(
            shop=shop,
            product=product,
            quantity=payload["quantity"],
            material=material,
            width_mm=width_mm,
            height_mm=height_mm,
            finishing_selections=finishing_selections,
        ).to_dict()
        return _build_shop_row(
            shop,
            can_calculate=True,
            total=result["totals"]["grand_total"],
            currency=result["currency"],
            reason="Exact preview from this shop.",
            preview=result,
            selection={"material_id": material.id, "material_label": f"{material.material_type} ({material.unit})"},
            similarity_score=_shop_similarity_score(shop, payload, True, material=material),
            exact_or_estimated=True,
        )

    paper = _pick_paper(shop, payload, product=product)
    if not paper:
        return _build_shop_row(
            shop,
            can_calculate=False,
            reason="No compatible paper is priced for this shop.",
            missing_fields=["paper", *missing_finishings],
            similarity_score=_shop_similarity_score(shop, payload, False),
        )

    machine = _pick_machine(shop, paper=paper, payload=payload, product=product)
    if not machine:
        return _build_shop_row(
            shop,
            can_calculate=False,
            reason="No compatible print-rate path is ready for this shop.",
            missing_fields=["machine", *missing_finishings],
            selection={"paper_id": paper.id, "paper_label": _paper_label(paper)},
            similarity_score=_shop_similarity_score(shop, payload, False, paper=paper),
        )

    result = calculate_sheet_pricing(
        shop=shop,
        product=product,
        quantity=payload["quantity"],
        paper=paper,
        machine=machine,
        color_mode=payload.get("colour_mode") or "COLOR",
        sides=payload.get("print_sides") or "SIMPLEX",
        apply_duplex_surcharge=payload.get("apply_duplex_surcharge"),
        finishing_selections=finishing_selections,
        width_mm=int(payload.get("width_mm") or product.default_finished_width_mm or 0),
        height_mm=int(payload.get("height_mm") or product.default_finished_height_mm or 0),
    ).to_dict()

    return _build_shop_row(
        shop,
        can_calculate=True,
        total=result["totals"]["grand_total"],
        currency=result["currency"],
        reason="Exact preview from this shop.",
        preview=result,
        selection={
            "paper_id": paper.id,
            "paper_label": _paper_label(paper),
            "machine_id": machine.id,
            "machine_label": getattr(machine, "name", ""),
        },
        similarity_score=_shop_similarity_score(shop, payload, True, paper=paper),
        exact_or_estimated=True,
    )


def _preview_custom_for_shop(shop: Shop, payload: dict[str, Any]) -> dict[str, Any]:
    finishing_selections, missing_finishings = _resolve_finishings(shop, payload)
    product_pricing_mode = payload.get("product_pricing_mode") or _infer_product_pricing_mode(payload)

    if product_pricing_mode == "LARGE_FORMAT":
        material = _pick_material(shop, payload)
        width_mm = int(payload.get("width_mm") or 0)
        height_mm = int(payload.get("height_mm") or 0)
        missing_fields = list(missing_finishings)
        if not material:
            missing_fields.append("material")
        if not width_mm:
            missing_fields.append("width_mm")
        if not height_mm:
            missing_fields.append("height_mm")
        if missing_fields:
            return _build_shop_row(
                shop,
                can_calculate=False,
                reason="Add material and finished size to price this custom job.",
                missing_fields=missing_fields,
                selection={"material_id": material.id, "material_label": f"{material.material_type} ({material.unit})"} if material else {},
                similarity_score=_shop_similarity_score(shop, payload, False, material=material),
            )

        result = calculate_large_format_pricing(
            shop=shop,
            product=None,
            quantity=payload["quantity"],
            material=material,
            width_mm=width_mm,
            height_mm=height_mm,
            finishing_selections=finishing_selections,
        ).to_dict()
        return _build_shop_row(
            shop,
            can_calculate=True,
            total=result["totals"]["grand_total"],
            currency=result["currency"],
            reason="Exact custom-spec preview from this shop.",
            preview=result,
            selection={"material_id": material.id, "material_label": f"{material.material_type} ({material.unit})"},
            similarity_score=_shop_similarity_score(shop, payload, True, material=material),
            exact_or_estimated=True,
        )

    paper = _pick_paper(shop, payload)
    machine = _pick_machine(shop, paper=paper, payload=payload) if paper else None
    width_mm = int(payload.get("width_mm") or 0)
    height_mm = int(payload.get("height_mm") or 0)
    missing_fields = list(missing_finishings)
    if not width_mm:
        missing_fields.append("width_mm")
    if not height_mm:
        missing_fields.append("height_mm")
    if not paper:
        missing_fields.append("paper")
    if paper and not machine:
        missing_fields.append("machine")

    if missing_fields:
        selection = {}
        if paper:
            selection["paper_id"] = paper.id
            selection["paper_label"] = _paper_label(paper)
        if machine:
            selection["machine_id"] = machine.id
            selection["machine_label"] = getattr(machine, "name", "")
        return _build_shop_row(
            shop,
            can_calculate=False,
            reason="Add paper and finished size to price this custom job exactly.",
            missing_fields=missing_fields,
            selection=selection,
            similarity_score=_shop_similarity_score(shop, payload, False, paper=paper),
        )

    result = calculate_sheet_pricing(
        shop=shop,
        product=None,
        quantity=payload["quantity"],
        paper=paper,
        machine=machine,
        color_mode=payload.get("colour_mode") or "COLOR",
        sides=payload.get("print_sides") or "SIMPLEX",
        apply_duplex_surcharge=payload.get("apply_duplex_surcharge"),
        finishing_selections=finishing_selections,
        width_mm=width_mm,
        height_mm=height_mm,
    ).to_dict()

    return _build_shop_row(
        shop,
        can_calculate=True,
        total=result["totals"]["grand_total"],
        currency=result["currency"],
        reason="Exact custom-spec preview from this shop.",
        preview=result,
        selection={
            "paper_id": paper.id,
            "paper_label": _paper_label(paper),
            "machine_id": machine.id,
            "machine_label": getattr(machine, "name", ""),
        },
        similarity_score=_shop_similarity_score(shop, payload, True, paper=paper),
        exact_or_estimated=True,
    )


def _resolve_finishings(shop: Shop, payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    finishing_selections = payload.get("finishing_selections") or []
    finishing_ids = payload.get("finishing_ids") or []
    finishing_slugs = payload.get("finishing_slugs") or []

    if not finishing_selections and not finishing_ids and not finishing_slugs:
        return [], []

    selections: list[dict[str, Any]] = []
    missing: list[str] = []

    if finishing_ids:
        rates = list(FinishingRate.objects.filter(shop=shop, pk__in=finishing_ids, is_active=True))
        by_id = {rate.id: rate for rate in rates}
        for finishing_id in finishing_ids:
            rate = by_id.get(finishing_id)
            if rate:
                side = _selected_side_for_finishing(finishing_id, None, finishing_selections)
                selections.append({"rule": rate, "selected_side": side})
            else:
                missing.append("finishings")

    if finishing_slugs:
        rates = list(FinishingRate.objects.filter(shop=shop, slug__in=finishing_slugs, is_active=True))
        by_slug = {rate.slug: rate for rate in rates}
        existing_ids = {selection["rule"].id for selection in selections}
        for slug in finishing_slugs:
            rate = by_slug.get(slug)
            if rate and rate.id not in existing_ids:
                side = _selected_side_for_finishing(None, slug, finishing_selections)
                selections.append({"rule": rate, "selected_side": side})
            elif not rate:
                missing.append("finishings")

    return selections, _unique_strings(missing)


def _selected_side_for_finishing(finishing_id: int | None, slug: str | None, finishing_selections: list[dict[str, Any]]) -> str:
    for selection in finishing_selections:
        if finishing_id and selection.get("finishing_id") == finishing_id:
            return selection.get("selected_side", "both")
        if slug and selection.get("slug") == slug:
            return selection.get("selected_side", "both")
    return "both"


def _pick_paper(shop: Shop, payload: dict[str, Any], product: Product | None = None) -> Paper | None:
    queryset = Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0)

    explicit_paper_id = payload.get("paper_id")
    if explicit_paper_id:
        return queryset.filter(id=explicit_paper_id).first()

    requested_gsm = payload.get("paper_gsm")
    requested_type = (payload.get("paper_type") or "").strip().lower()
    requested_sheet_size = (payload.get("sheet_size") or getattr(product, "default_sheet_size", "") or "").strip().upper()

    papers = list(queryset)
    if not papers:
        return None

    def score(paper: Paper) -> tuple[int, Decimal, int]:
        score_value = 0
        if requested_sheet_size and (paper.sheet_size or "").upper() == requested_sheet_size:
            score_value += 40
        if product and product.allowed_sheet_sizes and paper.sheet_size in product.allowed_sheet_sizes:
            score_value += 25
        if requested_gsm and paper.gsm == requested_gsm:
            score_value += 35
        elif requested_gsm:
            score_value += max(0, 20 - abs(paper.gsm - requested_gsm))
        if product and product.min_gsm and paper.gsm >= product.min_gsm:
            score_value += 5
        if product and product.max_gsm and paper.gsm <= product.max_gsm:
            score_value += 5
        paper_type = (paper.get_paper_type_display() or paper.paper_type or "").strip().lower()
        if requested_type and requested_type in paper_type:
            score_value += 25
        if product and product.default_sheet_size and (paper.sheet_size or "").upper() == product.default_sheet_size.upper():
            score_value += 10
        return (score_value, -Decimal(str(paper.selling_price)), -paper.id)

    return sorted(papers, key=score, reverse=True)[0]


def _pick_material(shop: Shop, payload: dict[str, Any]) -> Material | None:
    queryset = Material.objects.filter(shop=shop, is_active=True, selling_price__gt=0)
    material_id = payload.get("material_id")
    if material_id:
        return queryset.filter(id=material_id).first()
    return queryset.order_by("selling_price", "id").first()


def _pick_machine(shop: Shop, paper: Paper | None, payload: dict[str, Any], product: Product | None = None) -> Machine | None:
    if paper is None:
        return None
    colour_mode = payload.get("colour_mode") or "COLOR"
    print_sides = payload.get("print_sides") or getattr(product, "default_sides", "SIMPLEX") or "SIMPLEX"

    queryset = (
        Machine.objects.filter(shop=shop, is_active=True)
        .filter(
            printing_rates__sheet_size=paper.sheet_size,
            printing_rates__color_mode=colour_mode,
            printing_rates__is_active=True,
        )
        .distinct()
    )

    if product and getattr(product, "default_machine_id", None):
        preferred = queryset.filter(id=product.default_machine_id).first()
        if preferred:
            resolved_rate, resolved_price = PrintingRate.resolve(preferred, paper.sheet_size, colour_mode, print_sides, paper=paper)
            if resolved_rate and resolved_price is not None:
                return preferred

    for machine in queryset.order_by("id"):
        resolved_rate, resolved_price = PrintingRate.resolve(machine, paper.sheet_size, colour_mode, print_sides, paper=paper)
        if resolved_rate and resolved_price is not None:
            return machine
    return None


def _shop_similarity_score(
    shop: Shop,
    payload: dict[str, Any],
    can_calculate: bool,
    *,
    paper: Paper | None = None,
    material: Material | None = None,
) -> float:
    score = 0.0
    score += 45.0 if can_calculate else 5.0
    if payload.get("pricing_mode") == "catalog" and getattr(shop, "supports_catalog_requests", False):
        score += 10.0
    if payload.get("pricing_mode") == "custom" and getattr(shop, "supports_custom_requests", False):
        score += 10.0
    requested_gsm = payload.get("paper_gsm")
    if paper and requested_gsm:
        gsm_gap = abs(int(paper.gsm) - int(requested_gsm))
        score += max(0.0, 15.0 - float(gsm_gap) / 10.0)
    requested_sheet_size = (payload.get("sheet_size") or "").strip().upper()
    if paper and requested_sheet_size and (paper.sheet_size or "").upper() == requested_sheet_size:
        score += 10.0
    requested_type = (payload.get("paper_type") or "").strip().lower()
    if paper and requested_type:
        paper_type = (paper.get_paper_type_display() or paper.paper_type or "").strip().lower()
        if requested_type in paper_type:
            score += 10.0
    if material and payload.get("material_id") and material.id == payload.get("material_id"):
        score += 20.0
    if payload.get("finishing_ids") or payload.get("finishing_slugs"):
        score += 10.0
    return round(score, 2)


def _infer_product_pricing_mode(payload: dict[str, Any]) -> str:
    if payload.get("material_id"):
        return "LARGE_FORMAT"
    return "SHEET"


def _build_shop_row(
    shop: Shop,
    can_calculate: bool,
    *,
    total: str | None = None,
    currency: str | None = None,
    reason: str = "",
    missing_fields: list[str] | None = None,
    preview: dict[str, Any] | None = None,
    selection: dict[str, Any] | None = None,
    similarity_score: float = 0.0,
    exact_or_estimated: bool = False,
) -> dict[str, Any]:
    return {
        "id": shop.id,
        "name": shop.name,
        "slug": shop.slug,
        "can_calculate": can_calculate,
        "currency": currency or getattr(shop, "currency", "KES") or "KES",
        "reason": reason,
        "missing_fields": _unique_strings(missing_fields or []),
        "total": total,
        "preview": preview,
        "selection": selection or {},
        "similarity_score": similarity_score,
        "exact_or_estimated": exact_or_estimated,
    }


def _paper_label(paper: Paper) -> str:
    return f"{paper.sheet_size} {paper.gsm}gsm {paper.get_paper_type_display()}"


def _build_marketplace_summary(successful_rows: list[dict[str, Any]], failed_rows: list[dict[str, Any]]) -> str:
    if successful_rows:
        return f"Found {len(successful_rows)} shop matches with backend pricing previews."
    if failed_rows:
        return "No exact backend preview yet. Complete the missing requirements to unlock price ranges."
    return "No public shops are ready for this request yet."


def _unique_strings(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if not value:
            continue
        text = str(value)
        if text not in result:
            result.append(text)
    return result


def _as_decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def _format_decimal(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))
