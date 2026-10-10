"""Public, anonymized matching against configured production shops."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import Q
from django.utils.text import slugify

from inventory.models import Machine, Paper
from pricing.models import FinishingRate, PrintingRate
from services.pricing.engine import calculate_sheet_pricing
from services.pricing.finishing_normalization import normalize_finishing_slug, resolve_finishing_rate_for_slug
from services.pricing.marketplace_pricing import apply_marketplace_pricing_to_preview
from shops.models import Shop


MAX_PUBLIC_MATCHES = 12

# Sheet products whose finished pieces have to be cut out of the imposed parent
# sheet. The finished size is a pure imposition concern: it decides how many
# pieces fit per SRA3 sheet, and the shop quotes on those sheets plus cutting.
# Requiring a cutting path here would block shops, so cutting is priced whenever
# the shop has one and silently skipped (never excluding the shop) when it does
# not.
SHEET_PRODUCTS_REQUIRING_CUTTING = {"business_card", "flyer", "label_sticker"}


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


# A client may name a paper with either vocabulary, and the two spellings of the
# same finish differ ("matt" in the category vocabulary vs "MATTE" in the
# paper_type/finish vocabulary). iexact only folds case, so these aliases have
# to be expanded explicitly or a "matt" request misses every MATTE finish.
PAPER_TYPE_ALIASES = {
    "matt": ("matte",),
    "matte": ("matt",),
    "gloss": ("glossy",),
    "glossy": ("gloss",),
    "soft-touch": ("softtouch",),
    "softtouch": ("soft-touch",),
}


def _paper_type_terms(paper_type: str) -> list[str]:
    """Return every vocabulary/spelling a client may use to name a paper type.

    ``Paper.paper_type`` stores a *finish* (COATED/MATTE/GLOSS/...) while
    ``Paper.category`` stores the *client-facing* name (matt/gloss/bond/
    artcard/...). Callers legitimately send either vocabulary in
    ``paper_type``, so both are matched case-insensitively, including the
    matt<->matte and gloss<->glossy spelling pairs.
    """
    value = (paper_type or "").strip().lower()
    if not value:
        return []

    terms: list[str] = []

    def _add(term: str) -> None:
        if term and term not in terms:
            terms.append(term)

    _add(value)
    _add(value.upper())
    for alias in PAPER_TYPE_ALIASES.get(value, ()):
        _add(alias)
        _add(alias.upper())
    return terms


def _paper_type_query(paper_type: str) -> Q:
    query = Q()
    for term in _paper_type_terms(paper_type):
        query |= Q(category__iexact=term) | Q(paper_type__iexact=term)
    return query


def _paper_score(paper: Paper, *, paper_type: str | None, paper_gsm: int | None) -> tuple[int, int, int]:
    terms = [term.lower() for term in _paper_type_terms(paper_type or "")]
    if not terms:
        category_penalty = 0
    else:
        category_penalty = 0 if (paper.category or "").lower() in terms or (paper.paper_type or "").lower() in terms else 1000
    gsm_penalty = abs(int(paper.gsm or 0) - int(paper_gsm or paper.gsm or 0))
    default_penalty = 0 if paper.is_default else 1
    return category_penalty, gsm_penalty, default_penalty


def _nearest_gsm_subset(qs, target_gsm: int):
    """Filter ``qs`` down to the papers closest to ``target_gsm``.

    Used for soft paper hints, where the buyer's request is a preference and the
    shop still prices the job with its closest available stock. Returns a queryset
    so the caller keeps its existing ordering and slicing.
    """
    distances = [(abs(int(row["gsm"] or 0) - target_gsm), row["gsm"]) for row in qs.values("gsm")]
    if not distances:
        return qs.none()
    within_window = [gsm for distance, gsm in distances if distance <= 40]
    if within_window:
        return qs.filter(gsm__in=within_window)
    nearest = min(distance for distance, _ in distances)
    closest = {gsm for distance, gsm in distances if distance == nearest}
    return qs.filter(gsm__in=closest)


def _candidate_papers(shop: Shop, payload: dict[str, Any]) -> list[Paper]:
    qs = Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0)
    paper_type = (payload.get("paper_type") or "").strip()
    paper_gsm = int(payload.get("paper_gsm") or 0) or None

    # An explicit stock choice from the client always wins. Falling through to
    # the fuzzy paper_type/paper_gsm resolver used to silently discard it and
    # price a completely different paper. Conversely, a paper_id that cannot be
    # honoured (unknown, another shop, inactive, unpriced) must NOT quietly
    # resolve to a substitute: that is the same silent-substitution bug, so it
    # reports "no paper matches" instead.
    paper_id = payload.get("paper_id")
    if paper_id:
        try:
            return list(qs.filter(pk=int(paper_id))[:1])
        except (TypeError, ValueError):
            return []

    # A soft hint ("advise me / use the closest stock") must not exclude the shop.
    # A hard selection must not silently resolve to a different paper.
    is_soft_request = bool(payload.get("paper_request_is_soft"))

    if paper_type:
        typed_qs = qs.filter(_paper_type_query(paper_type))
        if typed_qs.exists():
            qs = typed_qs
    if paper_gsm:
        if is_soft_request:
            qs = _nearest_gsm_subset(qs, paper_gsm)
        else:
            # Never silently widen: if the requested grammage has no match in the
            # selected family, report "no paper matches" so the caller is forced
            # to disambiguate instead of receiving a cheaper wrong weight.
            qs = qs.filter(gsm__gte=max(1, paper_gsm - 40), gsm__lte=paper_gsm + 40)

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


def _resolve_cutting_rate(shop: Shop) -> FinishingRate | None:
    rows = FinishingRate.objects.filter(shop=shop, is_active=True).order_by("id")
    for row in rows:
        candidates = {slugify(row.slug or ""), slugify(row.name or "")}
        if any("cutting" in (candidate or "") for candidate in candidates):
            return row
    return None


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

    product_type = (str(payload.get("product_type") or "").strip()).lower()
    if product_type in SHEET_PRODUCTS_REQUIRING_CUTTING:
        cutting = _resolve_cutting_rate(shop)
        if cutting is not None and not any(
            getattr(selection.get("rule"), "id", None) == cutting.id for selection in selections
        ):
            selections.append({"rule": cutting, "selected_side": "both"})

    return selections, missing


def _public_match(
    index: int,
    shop: Shop,
    preview: dict[str, Any],
    product_type: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "production_preview": _production_intelligence(product_type, preview, payload),
        "price_range": str(total) if total else None,
    }


FINISHED_SIZE_NAME_BY_PRODUCT = {
    "business_card": "Business Card",
    "flyer": "Flyer",
    "poster": "Poster",
    "letterhead": "Letterhead",
    "certificate": "Certificate",
    "invitation_card": "Invitation Card",
    "brochure": "Brochure",
    "sticker": "Label / Sticker",
    "label_sticker": "Label / Sticker",
    "booklet": "Booklet",
    "large_format": "Large Format",
}


def _finished_size_label(payload: dict[str, Any] | None, product_type: str = "") -> str:
    """Human label for the FINISHED product size (e.g. "Business Card 85 x 55 mm").

    This is deliberately distinct from ``press_sheet.label``, which describes
    the parent sheet the job is imposed on. Reporting the press sheet as the
    size label made an 85x55mm card job read as "SRA3 350gsm Matte".
    """
    payload = payload or {}
    explicit = str(payload.get("size_label") or payload.get("finished_size") or "").strip()
    if explicit:
        return explicit

    def _format_mm(value: Decimal) -> str:
        """Render millimetres without inventing or discarding precision."""
        normalised = value.normalize()
        if normalised == normalised.to_integral_value():
            return str(int(normalised))
        return format(normalised, "f")

    width = _decimal(payload.get("width_mm"))
    height = _decimal(payload.get("height_mm"))
    dimensions = f"{_format_mm(width)} x {_format_mm(height)} mm" if width > 0 and height > 0 else ""
    name = FINISHED_SIZE_NAME_BY_PRODUCT.get((product_type or "").strip().lower(), "")
    if name and dimensions:
        return f"{name} {dimensions}"
    return dimensions or name or "Custom size"


def _production_intelligence(
    product_type: str,
    preview: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Full sheet-layout disclosure the buyer's price is built from.

    The engine records every number (copies per sheet, cols x rows, bleed, press
    sheet size, good sheets and the waste-policy spoilage split) inside
    ``breakdown.imposition``/``breakdown.paper``. This projects the whole spec so
    the calculator can say exactly how the sheet is laid out — nothing hidden.
    """
    breakdown = preview.get("breakdown") or {}
    imposition = breakdown.get("imposition") or {}
    paper = breakdown.get("paper") or {}
    layout = imposition.get("layout") or {}
    finishings = breakdown.get("finishings") or []
    warnings = [
        text
        for text in (preview.get("explanations") or [])
        if not (isinstance(text, str) and text.strip().startswith("VAT:"))
    ]
    good_sheets = imposition.get("good_sheets") or preview.get("good_sheets")
    return {
        "pieces_per_sheet": imposition.get("copies_per_sheet") or preview.get("copies_per_sheet"),
        "sheets_required": good_sheets,
        "parent_sheet": paper.get("sheet_size") or preview.get("parent_sheet_name"),
        "good_sheets": good_sheets,
        "waste_sheets_added": imposition.get("waste_sheets_added"),
        "fixed_waste_sheets": imposition.get("fixed_waste_sheets"),
        "variable_waste_sheets": imposition.get("variable_waste_sheets"),
        "variable_waste_rate": imposition.get("variable_waste_rate"),
        "billable_sheets": imposition.get("billable_sheets") or preview.get("billable_sheets"),
        "production_sheets": imposition.get("production_sheets"),
        "max_billable_sheets": imposition.get("max_billable_sheets"),
        "maximum_spoilage_rate": imposition.get("maximum_spoilage_rate"),
        "maximum_spoilage_sheets": imposition.get("maximum_spoilage_sheets"),
        "spoilage_capped": imposition.get("spoilage_capped"),
        "layout": {
            "cols": layout.get("cols") or imposition.get("cols"),
            "rows": layout.get("rows") or imposition.get("rows"),
            "orientation": imposition.get("orientation") or ("rotated" if preview.get("rotated") else "normal"),
        },
        "bleed_mm": imposition.get("bleed_mm"),
        "press_sheet": {
            "label": paper.get("label") or paper.get("sheet_size"),
            "width_mm": paper.get("width_mm") or imposition.get("sheet_width_mm"),
            "height_mm": paper.get("height_mm") or imposition.get("sheet_height_mm"),
        },
        "imposition_label": imposition.get("explanation") or preview.get("reason"),
        "size_label": _finished_size_label(payload, product_type),
        "quantity": preview.get("quantity"),
        "cutting_required": True if str(product_type or "").lower() in {"business_card", "flyer", "label_sticker"} else None,
        "selected_finishings": [f.get("name") for f in finishings if f.get("name")],
        "suggested_finishings": [],
        "warnings": warnings,
    }


def build_public_match_payload(payload):
    matches: list[dict[str, Any]] = []
    unsupported_reasons: list[str] = []
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
                # Forward the supplied millimetre values untouched.
                width_mm=payload.get("width_mm"),
                height_mm=payload.get("height_mm"),
            )
            if not result.can_calculate:
                if result.reason and result.reason not in unsupported_reasons:
                    unsupported_reasons.append(result.reason)
                continue
            preview = apply_marketplace_pricing_to_preview(result.to_dict(), shop=shop)
            total = _positive_money((preview.get("totals") or {}).get("grand_total"))
            if not total:
                continue
            matches.append(
                _public_match(len(matches) + 1, shop, preview, payload.get("product_type"), payload)
            )
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
        "unsupported_reasons": unsupported_reasons,
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
