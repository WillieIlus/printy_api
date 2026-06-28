from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import OperationalError, ProgrammingError
from django.db import transaction
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError

from inventory.choices import MachineType, PaperCategory, PaperType, SheetSize
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, ColorMode, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PrintingRate
from services.pricing.imposition import build_imposition_breakdown
from services.pricing.marketplace_pricing import build_marketplace_pricing_summary, get_marketplace_margin_settings


DEFAULT_PAPER_DEFINITIONS: list[dict[str, Any]] = [
    {"key": "130gsm_matte_art", "id": "paper-130gsm-matte-art", "label": "130gsm Matte/Art", "paper_name": "130gsm", "gsm": 130, "paper_type": "Matte/Art", "category": "Matte/Art", "size": "SRA3", "supports_double_side": True, "paper_base_price": "10.00", "single_print_base": "15.00", "double_print_base": "30.00", "heavy_paper_surcharge": "10.00", "surcharge_threshold_gsm": 250, "single_side_price": "25.00", "double_side_price": "40.00", "active": False},
    {"key": "150gsm_matte_art", "id": "paper-150gsm-matte-art", "label": "150gsm Matte/Art", "paper_name": "150gsm", "gsm": 150, "paper_type": "Matte/Art", "category": "Matte/Art", "size": "SRA3", "supports_double_side": True, "paper_base_price": "15.00", "single_print_base": "15.00", "double_print_base": "30.00", "heavy_paper_surcharge": "10.00", "surcharge_threshold_gsm": 250, "single_side_price": "30.00", "double_side_price": "45.00", "active": False},
    {"key": "170gsm_matte_art", "id": "paper-170gsm-matte-art", "label": "170gsm Matte/Art", "paper_name": "170gsm", "gsm": 170, "paper_type": "Matte/Art", "category": "Matte/Art", "size": "SRA3", "supports_double_side": True, "paper_base_price": "18.00", "single_print_base": "15.00", "double_print_base": "30.00", "heavy_paper_surcharge": "10.00", "surcharge_threshold_gsm": 250, "single_side_price": "33.00", "double_side_price": "48.00", "active": False},
    {"key": "200gsm_matte", "id": "paper-200gsm-matte", "label": "200gsm Matte", "paper_name": "200gsm", "gsm": 200, "paper_type": "Matte", "category": "Matte", "size": "SRA3", "supports_double_side": True, "paper_base_price": "20.00", "single_print_base": "15.00", "double_print_base": "30.00", "heavy_paper_surcharge": "10.00", "surcharge_threshold_gsm": 250, "single_side_price": "35.00", "double_side_price": "50.00", "active": False},
    {"key": "250gsm_matte", "id": "paper-250gsm-matte", "label": "250gsm Matte", "paper_name": "250gsm", "gsm": 250, "paper_type": "Matte", "category": "Matte", "size": "SRA3", "supports_double_side": True, "paper_base_price": "30.00", "single_print_base": "15.00", "double_print_base": "30.00", "heavy_paper_surcharge": "10.00", "surcharge_threshold_gsm": 250, "single_side_price": "55.00", "double_side_price": "70.00", "active": False},
    {"key": "300gsm_matte_art_card", "id": "paper-300gsm-matte-art-card", "label": "300gsm Matte/Art Card", "paper_name": "300gsm", "gsm": 300, "paper_type": "Matte/Art Card", "category": "Matte/Art Card", "size": "SRA3", "supports_double_side": True, "paper_base_price": "35.00", "single_print_base": "15.00", "double_print_base": "30.00", "heavy_paper_surcharge": "10.00", "surcharge_threshold_gsm": 250, "single_side_price": "60.00", "double_side_price": "75.00", "active": False},
    {"key": "350gsm_matte_art_card", "id": "paper-350gsm-matte-art-card", "label": "350gsm Matte/Art Card", "paper_name": "350gsm", "gsm": 350, "paper_type": "Matte/Art Card", "category": "Matte/Art Card", "size": "SRA3", "supports_double_side": True, "paper_base_price": "40.00", "single_print_base": "15.00", "double_print_base": "30.00", "heavy_paper_surcharge": "10.00", "surcharge_threshold_gsm": 250, "single_side_price": "65.00", "double_side_price": "80.00", "active": False},
    {"key": "300gsm_ivory", "id": "paper-300gsm-ivory", "label": "300gsm Ivory", "paper_name": "300 Ivory", "gsm": 300, "paper_type": "Ivory", "category": "Ivory", "size": "SRA3", "supports_double_side": True, "paper_base_price": "50.00", "single_print_base": "15.00", "double_print_base": "30.00", "heavy_paper_surcharge": "10.00", "surcharge_threshold_gsm": 250, "single_side_price": "75.00", "double_side_price": "90.00", "active": False},
    {"key": "150gsm_tictac_sticker", "id": "paper-150gsm-tictac-sticker", "label": "Tic Tac Sticker", "paper_name": "Tic Tac Sticker", "gsm": 150, "paper_type": "Sticker", "category": "Sticker", "size": "SRA3", "supports_double_side": False, "paper_base_price": "25.00", "single_print_base": "15.00", "double_print_base": "30.00", "heavy_paper_surcharge": "10.00", "surcharge_threshold_gsm": 250, "single_side_price": "40.00", "double_side_price": None, "active": False},
]

DEFAULT_FINISHING_DEFINITIONS: list[dict[str, Any]] = [
    {"key": "perfect_binding", "id": "finishing-perfect-binding", "label": "Perfect Bind", "name": "Perfect Bind", "pricing_mode": "per_piece", "unit": "piece", "price": "50.00", "minimum_charge": None, "active": False},
    {"key": "ivory_duplex", "id": "finishing-ivory-duplex", "label": "Ivory 300 Duplex", "name": "Ivory 300 Duplex", "pricing_mode": "per_sheet", "unit": "sheet", "price": "100.00", "minimum_charge": None, "active": False},
    {"key": "creasing", "id": "finishing-creasing", "label": "Creasing", "name": "Creasing", "pricing_mode": "flat_per_job", "unit": "job", "price": "300.00", "minimum_charge": None, "active": False},
    {"key": "wire_o", "id": "finishing-wire-o", "label": "Wire-O-Wire", "name": "Wire-O-Wire", "pricing_mode": "per_book", "unit": "book", "price": "50.00", "minimum_charge": None, "active": False},
    {"key": "potch_lamination", "id": "finishing-potch-lamination", "label": "Potch Lamination", "name": "Potch Lamination", "pricing_mode": "per_piece", "unit": "piece", "price": "100.00", "minimum_charge": None, "active": False},
    {"key": "gloss_lamination_single", "id": "finishing-gloss-lamination-single", "label": "Gloss Lamination Single", "name": "Gloss Lamination Single", "pricing_mode": "per_sheet", "unit": "sheet", "price": "15.00", "minimum_charge": None, "active": False},
    {"key": "gloss_lamination_double", "id": "finishing-gloss-lamination-double", "label": "Gloss Lamination Double", "name": "Gloss Lamination Double", "pricing_mode": "per_sheet", "unit": "sheet", "price": "20.00", "minimum_charge": "60.00", "active": False},
    {"key": "matte_lamination_single", "id": "finishing-matte-lamination-single", "label": "Matt Lamination Single", "name": "Matt Lamination Single", "pricing_mode": "per_sheet", "unit": "sheet", "price": "15.00", "minimum_charge": None, "active": False},
    {"key": "matte_lamination_double", "id": "finishing-matte-lamination-double", "label": "Matt Lamination Double", "name": "Matt Lamination Double", "pricing_mode": "per_sheet", "unit": "sheet", "price": "20.00", "minimum_charge": "60.00", "active": False},
    {"key": "cutting", "id": "finishing-cutting", "label": "Cutting Standard", "name": "Cutting Standard", "pricing_mode": "flat_per_job", "unit": "job", "price": "150.00", "minimum_charge": None, "active": False},
    {"key": "stitching_booklet", "id": "finishing-stitching-booklet", "label": "Stitching Booklet", "name": "Stitching Booklet", "pricing_mode": "per_book", "unit": "book", "price": "5.00", "minimum_charge": None, "active": False},
    {"key": "uv_lamination", "id": "finishing-uv-lamination", "label": "UV Lamination", "name": "UV Lamination", "pricing_mode": "per_sheet", "unit": "sheet", "price": "10.00", "minimum_charge": "300.00", "active": False},
    {"key": "round_corner_piece", "id": "finishing-round-corner-piece", "label": "Round Cornering Piece", "name": "Round Cornering Piece", "pricing_mode": "per_piece", "unit": "piece", "price": "1.00", "minimum_charge": "50.00", "active": False},
    {"key": "round_corner_book", "id": "finishing-round-corner-book", "label": "Round Cornering Book", "name": "Round Cornering Book", "pricing_mode": "per_book", "unit": "book", "price": "200.00", "minimum_charge": None, "active": False},
]

DEFAULT_SHOP_DETAILS = {
    "shop_name": "",
    "whatsapp_number": "",
    "location_area": "",
}


def _build_default_paper_rows() -> list[dict[str, Any]]:
    return deepcopy(DEFAULT_PAPER_DEFINITIONS)


def _build_default_finishing_rows() -> list[dict[str, Any]]:
    return deepcopy(DEFAULT_FINISHING_DEFINITIONS)


PAPER_DEFINITION_BY_KEY = {row["key"]: row for row in DEFAULT_PAPER_DEFINITIONS}
FINISHING_DEFINITION_BY_KEY = {row["key"]: row for row in DEFAULT_FINISHING_DEFINITIONS}

MARKET_GUIDE_MIN_SAMPLE_COUNT = 3
BUSINESS_CARD_WIDTH_MM = 90
BUSINESS_CARD_HEIGHT_MM = 55
FLYER_A5_WIDTH_MM = 148
FLYER_A5_HEIGHT_MM = 210
SRA3_WIDTH_MM = 320
SRA3_HEIGHT_MM = 450
DEFAULT_SINGLE_PRINT_BASE = Decimal("15.00")
DEFAULT_DOUBLE_PRINT_BASE = Decimal("30.00")
DEFAULT_HEAVY_PAPER_SURCHARGE = Decimal("10.00")
DEFAULT_SURCHARGE_THRESHOLD_GSM = 250
DEFAULT_LIGHT_STOCK_QUANTITY = 2000
DEFAULT_HEAVY_STOCK_QUANTITY = 500


def _deepcopy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return deepcopy(rows)


def _default_quantity_in_stock(row: dict[str, Any]) -> int:
    gsm = int(row.get("gsm") or 0)
    threshold = int(row.get("surcharge_threshold_gsm") or DEFAULT_SURCHARGE_THRESHOLD_GSM)
    return DEFAULT_HEAVY_STOCK_QUANTITY if gsm >= threshold else DEFAULT_LIGHT_STOCK_QUANTITY


def _to_decimal(value: Any, *, allow_null: bool = False) -> Decimal | None:
    if value in (None, ""):
        if allow_null:
            return None
        raise ValidationError("A numeric value is required.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError("Enter a valid numeric amount.") from exc
    if amount < 0:
        raise ValidationError("Prices cannot be negative.")
    return amount.quantize(Decimal("0.01"))


def _decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01")))


def _decimal_stats(values: list[Decimal]) -> dict[str, str | int | None]:
    if not values:
        return {"min": None, "max": None, "median": None, "mean": None, "sample_count": 0}
    ordered = sorted(values)
    count = len(ordered)
    midpoint = count // 2
    median = ordered[midpoint] if count % 2 == 1 else (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")
    mean = sum(ordered) / Decimal(count)
    return {
        "min": _decimal_string(ordered[0]),
        "max": _decimal_string(ordered[-1]),
        "median": _decimal_string(median),
        "mean": _decimal_string(mean),
        "sample_count": count,
    }


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _format_formula(parts: list[Decimal]) -> str:
    return " + ".join(str(int(part)) if part == part.to_integral_value() else str(part) for part in parts[:-1]) + f" = {parts[-1]}"


def _is_sticker_row(row: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            _normalize_text(row.get("label")),
            _normalize_text(row.get("paper_name")),
            _normalize_text(row.get("paper_type")),
            _normalize_text(row.get("category")),
        ]
    ).lower()
    return "sticker" in haystack or "tictac" in haystack or "tic tac" in haystack


def _resolve_paper_base_price(row: dict[str, Any], definition: dict[str, Any]) -> Decimal:
    raw_base = row.get("paper_base_price")
    if raw_base not in (None, ""):
        return _to_decimal(raw_base)
    raw_single = row.get("single_side_price")
    if raw_single not in (None, ""):
        gsm = int(row.get("gsm") or definition.get("gsm") or 0)
        surcharge = _to_decimal(row.get("heavy_paper_surcharge") or definition.get("heavy_paper_surcharge") or DEFAULT_HEAVY_PAPER_SURCHARGE)
        threshold = int(row.get("surcharge_threshold_gsm") or definition.get("surcharge_threshold_gsm") or DEFAULT_SURCHARGE_THRESHOLD_GSM)
        base = _to_decimal(raw_single) - _to_decimal(row.get("single_print_base") or definition.get("single_print_base") or DEFAULT_SINGLE_PRINT_BASE)
        if gsm >= threshold:
            base -= surcharge
        return max(base, Decimal("0.00"))
    return _to_decimal(definition.get("paper_base_price") or "0.00")


def _enrich_paper_row(row: dict[str, Any], *, definition: dict[str, Any] | None = None) -> dict[str, Any]:
    resolved_definition = definition or _resolve_paper_definition(row) or row
    enriched = deepcopy(row)
    gsm = int(enriched.get("gsm") or resolved_definition.get("gsm") or 0)
    paper_base_price = _resolve_paper_base_price(enriched, resolved_definition)
    single_print_base = _to_decimal(enriched.get("single_print_base") or resolved_definition.get("single_print_base") or DEFAULT_SINGLE_PRINT_BASE)
    double_print_base = _to_decimal(enriched.get("double_print_base") or resolved_definition.get("double_print_base") or DEFAULT_DOUBLE_PRINT_BASE)
    heavy_paper_surcharge = _to_decimal(enriched.get("heavy_paper_surcharge") or resolved_definition.get("heavy_paper_surcharge") or DEFAULT_HEAVY_PAPER_SURCHARGE)
    surcharge_threshold_gsm = int(enriched.get("surcharge_threshold_gsm") or resolved_definition.get("surcharge_threshold_gsm") or DEFAULT_SURCHARGE_THRESHOLD_GSM)
    double_sided_enabled = bool(resolved_definition.get("supports_double_side", True)) and not _is_sticker_row(enriched)
    surcharge_applies = gsm >= surcharge_threshold_gsm
    surcharge_amount = heavy_paper_surcharge if surcharge_applies else Decimal("0.00")
    single_total = paper_base_price + single_print_base + surcharge_amount
    double_total = paper_base_price + double_print_base + surcharge_amount if double_sided_enabled else None
    single_parts = [paper_base_price, single_print_base]
    double_parts = [paper_base_price, double_print_base]
    if surcharge_amount > 0:
        single_parts.append(surcharge_amount)
        double_parts.append(surcharge_amount)
    single_parts.append(single_total)
    warnings: list[str] = []
    if surcharge_applies:
        warnings.append(f"Heavy paper surcharge applied because GSM is {surcharge_threshold_gsm}+.")
    if not double_sided_enabled:
        warnings.append("Double-sided is disabled for sticker stock.")
    enriched.update(
        {
            "paper_base_price": _decimal_string(paper_base_price),
            "single_print_base": _decimal_string(single_print_base),
            "double_print_base": _decimal_string(double_print_base),
            "heavy_paper_surcharge": _decimal_string(heavy_paper_surcharge),
            "surcharge_threshold_gsm": surcharge_threshold_gsm,
            "single_side_price": _decimal_string(single_total),
            "double_side_price": _decimal_string(double_total),
            "double_sided_enabled": double_sided_enabled,
            "supports_double_side": double_sided_enabled,
            "manager_visible_single_total": _decimal_string(single_total),
            "manager_visible_double_total": _decimal_string(double_total),
            "formula_shop_visible": {
                "single": _format_formula(single_parts),
                "double": _format_formula(double_parts + [double_total]) if double_total is not None else None,
            },
            "warnings": warnings,
        }
    )
    return enriched


def _capability_preview_for_paper(row: dict[str, Any], finishing_rows: list[dict[str, Any]]) -> dict[str, Any]:
    gsm = int(row.get("gsm") or 0)
    label = _normalize_text(row.get("label") or row.get("paper_name"))
    allowed: list[str]
    blocked: list[str] = []
    recommended: list[str] = []
    notes: list[str] = []
    disabled_options: list[str] = []
    warnings = list(row.get("warnings") or [])
    if _is_sticker_row(row):
        allowed = ["Label Sheets", "Die-cut Sticker Sheets"]
        disabled_options = ["double_sided", "lamination_uv", "lamination_duplex"]
        notes.append("Tic Tac Sticker supports single-sided sticker work only.")
        recommended.append("Gloss or matt single-sided lamination if supported.")
    elif 130 <= gsm <= 170:
        allowed = ["Flyers", "Booklet Inner Pages", "Brochures", "Letterheads"]
        blocked = ["Business Cards", "Rigid Covers"]
        notes.append(f"With {label} you can now offer light and medium stock work.")
    else:
        allowed = ["Business Cards", "Book Covers", "Postcards", "Presentation Folders"]
        blocked = ["Thin letterheads"]
        recommended.append("Creasing recommended for folded heavy stock.")
        notes.append(f"With {label} you can now offer heavier card products.")
        if not _has_finishing(finishing_rows, ("creasing",)):
            warnings.append("Creasing recommended for folded heavy stock.")
    if not _has_finishing(finishing_rows, ("cutting",)):
        warnings.append("No cutting price set; business card jobs may be incomplete.")
    return {
        "allowed_product_types": allowed,
        "blocked_product_types": blocked,
        "required_or_recommended_finishing": recommended,
        "capability_notes": notes,
        "disabled_options": disabled_options,
        "warnings": warnings,
    }


def _finishing_quantity_basis(row: dict[str, Any], *, quantity: int, sheets_needed: int) -> tuple[int, str]:
    mode = _normalize_text(row.get("pricing_mode")).lower()
    if mode == "per_sheet":
        return sheets_needed, "sheet"
    if mode == "per_book":
        return quantity, "book"
    if mode == "per_piece":
        return quantity, "piece"
    return 1, "job"


def _build_finishing_preview(row: dict[str, Any], *, quantity: int, sheets_needed: int) -> dict[str, Any]:
    rate = _to_decimal(row.get("price") or "0.00")
    minimum_charge = _to_decimal(row.get("minimum_charge"), allow_null=True)
    units, basis = _finishing_quantity_basis(row, quantity=quantity, sheets_needed=sheets_needed)
    raw_total = rate * Decimal(units)
    final_total = raw_total
    minimum_applied = False
    if minimum_charge is not None and final_total < minimum_charge:
        final_total = minimum_charge
        minimum_applied = True
    formula = (
        f"{units} x {int(rate) if rate == rate.to_integral_value() else rate} = {int(raw_total) if raw_total == raw_total.to_integral_value() else raw_total}"
        if units != 1 or _normalize_text(row.get('pricing_mode')).lower() != "flat_per_job"
        else f"Flat rate = {int(rate) if rate == rate.to_integral_value() else rate}"
    )
    if minimum_applied and minimum_charge is not None:
        formula = f"{formula}; minimum {int(minimum_charge) if minimum_charge == minimum_charge.to_integral_value() else minimum_charge} applied"
    return {
        "finishing_name": row.get("label") or row.get("name"),
        "charge_type": row.get("pricing_mode"),
        "rate": _decimal_string(rate),
        "quantity_basis": basis,
        "raw_total": _decimal_string(raw_total),
        "minimum_applied": minimum_applied,
        "final_total": _decimal_string(final_total),
        "shop_visible_formula": formula,
        "manager_visible_total": _decimal_string(final_total),
    }


def _build_sample_job_preview(row: dict[str, Any], finishing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gsm = int(row.get("gsm") or 0)
    if _is_sticker_row(row):
        job_label = "Sample 100 label pieces"
        width_mm = BUSINESS_CARD_WIDTH_MM
        height_mm = BUSINESS_CARD_HEIGHT_MM
    elif 130 <= gsm <= 170:
        job_label = "Sample 100 A5 flyers"
        width_mm = FLYER_A5_WIDTH_MM
        height_mm = FLYER_A5_HEIGHT_MM
    else:
        job_label = "Sample 100 business cards"
        width_mm = BUSINESS_CARD_WIDTH_MM
        height_mm = BUSINESS_CARD_HEIGHT_MM
    imposition = build_imposition_breakdown(
        quantity=100,
        finished_width_mm=width_mm,
        finished_height_mm=height_mm,
        sheet_width_mm=SRA3_WIDTH_MM,
        sheet_height_mm=SRA3_HEIGHT_MM,
    )
    sheets_needed = int(imposition.good_sheets or 0)
    pieces_per_sheet = int(imposition.copies_per_sheet or 0)
    single_total = _to_decimal(row.get("single_side_price") or "0.00")
    double_total = _to_decimal(row.get("double_side_price"), allow_null=True)
    single_production = Decimal(sheets_needed) * single_total
    double_production = Decimal(sheets_needed) * double_total if double_total is not None else None
    previews = [
        _build_finishing_preview(finishing_row, quantity=100, sheets_needed=sheets_needed)
        for finishing_row in finishing_rows
        if _is_active_finishing(finishing_row)
    ]
    finishing_total = sum((_to_decimal(item["final_total"]) or Decimal("0.00")) for item in previews) if previews else Decimal("0.00")
    return [
        {
            "label": job_label,
            "pieces_per_sheet": pieces_per_sheet,
            "sheets_needed": sheets_needed,
            "single_sided_production": _decimal_string(single_production),
            "double_sided_production": _decimal_string(double_production),
            "finishing_estimate": _decimal_string(finishing_total) if finishing_total > 0 else None,
            "total_production_cost": _decimal_string(single_production + finishing_total),
            "finishing_previews": previews,
        }
    ]


def _paper_row_from_definition(definition: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(definition)
    if row.get("quantity_in_stock") in (None, ""):
        row["quantity_in_stock"] = _default_quantity_in_stock(row)
    return row


def _finishing_row_from_definition(definition: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(definition)


def _resolve_paper_definition(row: dict[str, Any]) -> dict[str, Any] | None:
    key = _normalize_text(row.get("key"))
    if key and key in PAPER_DEFINITION_BY_KEY:
        return PAPER_DEFINITION_BY_KEY[key]

    row_id = _normalize_text(row.get("id"))
    if row_id:
        for definition in DEFAULT_PAPER_DEFINITIONS:
            if definition["id"] == row_id:
                return definition

    paper_name = _normalize_text(row.get("paper_name")).lower().replace(" ", "")
    paper_type = _normalize_text(row.get("paper_type")).lower()
    for definition in DEFAULT_PAPER_DEFINITIONS:
        definition_name = _normalize_text(definition.get("paper_name")).lower().replace(" ", "")
        definition_type = _normalize_text(definition.get("paper_type")).lower()
        if paper_name == definition_name and paper_type == definition_type:
            return definition
    return None


def _resolve_finishing_definition(row: dict[str, Any]) -> dict[str, Any] | None:
    key = _normalize_text(row.get("key"))
    if key and key in FINISHING_DEFINITION_BY_KEY:
        return FINISHING_DEFINITION_BY_KEY[key]

    row_id = _normalize_text(row.get("id"))
    if row_id:
        for definition in DEFAULT_FINISHING_DEFINITIONS:
            if definition["id"] == row_id:
                return definition

    name = _normalize_text(row.get("name")).lower()
    for definition in DEFAULT_FINISHING_DEFINITIONS:
        if _normalize_text(definition.get("name")).lower() == name:
            return definition
    return None


def _normalize_paper_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    ordered_keys = [_normalize_text(row.get("key")) for row in (rows or []) if row.get("key")]
    input_rows = {_normalize_text(row.get("key")): row for row in (rows or []) if row.get("key")}
    ordered_definitions = [
        PAPER_DEFINITION_BY_KEY[key]
        for key in ordered_keys
        if key in PAPER_DEFINITION_BY_KEY
    ]
    ordered_definitions.extend(
        definition
        for definition in DEFAULT_PAPER_DEFINITIONS
        if definition["key"] not in input_rows
    )
    normalized: list[dict[str, Any]] = []

    for definition in ordered_definitions:
        row = input_rows.get(definition["key"])
        if row is None:
            # Re-add missing default as inactive
            normalized_row = _paper_row_from_definition(definition)
            normalized_row["active"] = False
            normalized.append(normalized_row)
            continue

        normalized_row = _paper_row_from_definition(definition)
        normalized_row.update(row)
        normalized_row["active"] = bool(row.get("active"))
        if normalized_row["active"]:
            try:
                normalized.append(_enrich_paper_row(normalized_row, definition=definition))
            except ValidationError:
                idx = (rows or []).index(row)
                raise ValidationError({"paper_prices": {idx: {"paper_base_price": ["Enter a valid non-negative paper base price."]}}})
        else:
            normalized.append(_enrich_paper_row(normalized_row, definition=definition))
    return normalized


def _normalize_finishing_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    ordered_keys = [_normalize_text(row.get("key")) for row in (rows or []) if row.get("key")]
    input_rows = {_normalize_text(row.get("key")): row for row in (rows or []) if row.get("key")}
    ordered_definitions = [
        FINISHING_DEFINITION_BY_KEY[key]
        for key in ordered_keys
        if key in FINISHING_DEFINITION_BY_KEY
    ]
    ordered_definitions.extend(
        definition
        for definition in DEFAULT_FINISHING_DEFINITIONS
        if definition["key"] not in input_rows
    )
    normalized: list[dict[str, Any]] = []

    for definition in ordered_definitions:
        row = input_rows.get(definition["key"])
        if row is None:
            # Re-add missing default as inactive
            normalized_row = _finishing_row_from_definition(definition)
            normalized_row["active"] = False
            normalized.append(normalized_row)
            continue

        active = bool(row.get("active"))
        price = _to_decimal(row.get("price"), allow_null=not active)
        minimum_charge = _to_decimal(row.get("minimum_charge"), allow_null=True)
        if active and price is None:
            idx = (rows or []).index(row)
            raise ValidationError({"finishings": {idx: {"price": ["Enter a valid non-negative finishing price."]}}})

        normalized_row = _finishing_row_from_definition(definition)
        normalized_row.update(
            {
                "price": _decimal_string(price),
                "minimum_charge": _decimal_string(minimum_charge),
                "active": active,
            }
        )
        normalized.append(normalized_row)
    return normalized


def _normalize_shop_details(details: dict[str, Any] | None) -> dict[str, str]:
    payload = deepcopy(DEFAULT_SHOP_DETAILS)
    for key in payload:
        payload[key] = _normalize_text((details or {}).get(key))
    return payload


def _is_active_paper(row: dict[str, Any]) -> bool:
    return bool(row.get("active")) and row.get("single_side_price") not in (None, "")


def _is_active_finishing(row: dict[str, Any]) -> bool:
    return bool(row.get("active")) and row.get("price") not in (None, "")


def _paper_matches(row: dict[str, Any], *, names: tuple[str, ...] = (), gsms: tuple[int, ...] = (), paper_types: tuple[str, ...] = ()) -> bool:
    if not _is_active_paper(row):
        return False
    paper_name = _normalize_text(row.get("paper_name")).lower()
    paper_type = _normalize_text(row.get("paper_type")).lower()
    label = _normalize_text(row.get("label")).lower()
    category = _normalize_text(row.get("category")).lower()
    gsm = row.get("gsm")
    return (
        (bool(names) and any(name in paper_name or name in label for name in names))
        or (bool(gsms) and gsm in gsms)
        or (bool(paper_types) and any(item in paper_type or item in category for item in paper_types))
    )


def _has_finishing(rows: list[dict[str, Any]], names: tuple[str, ...]) -> bool:
    for row in rows:
        if _is_active_finishing(row) and any(name in _normalize_text(row.get("name")).lower() for name in names):
            return True
    return False


def _build_unlocked_products(paper_rows: list[dict[str, Any]], finishing_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    unlocked: list[dict[str, str]] = []

    heavy_stock = any(_paper_matches(row, gsms=(200, 250, 300, 350), names=("200", "250", "300", "350")) for row in paper_rows)
    light_stock = any(_paper_matches(row, gsms=(130, 150, 170), names=("130", "150", "170")) for row in paper_rows)
    any_paper = any(_is_active_paper(row) for row in paper_rows)
    sticker_stock = any(_paper_matches(row, names=("tic tac", "sticker", "tictac"), paper_types=("sticker",)) for row in paper_rows)

    has_cutting = _has_finishing(finishing_rows, ("cutting",))
    has_lamination = _has_finishing(finishing_rows, ("lamination", "potch"))
    has_saddle = _has_finishing(finishing_rows, ("stitching", "saddle"))
    has_perfect = _has_finishing(finishing_rows, ("perfect",))
    has_spiral = _has_finishing(finishing_rows, ("spiral", "wire-o", "wire o"))

    if heavy_stock and has_cutting:
        unlocked.append({"key": "business-cards", "label": "Business Cards", "reason": "Heavy card stock plus cutting is ready."})
    if heavy_stock and has_lamination and has_cutting:
        unlocked.append({"key": "laminated-business-cards", "label": "Laminated Business Cards", "reason": "Card stock, lamination, and cutting are ready."})
    if light_stock:
        unlocked.append({"key": "flyers", "label": "Flyers", "reason": "Light digital paper is ready."})
        unlocked.append({"key": "posters", "label": "Posters", "reason": "Light digital paper is ready."})
    if light_stock and has_cutting:
        unlocked.append({"key": "brochures", "label": "Brochures", "reason": "Light paper plus cutting is ready."})
    if any_paper and has_saddle:
        unlocked.append({"key": "booklets", "label": "Booklets", "reason": "Paper plus saddle stitching is ready."})
    if any_paper and has_perfect:
        unlocked.append({"key": "perfect-bound-books", "label": "Perfect Bound Books", "reason": "Paper plus perfect binding is ready."})
    if any_paper and has_spiral:
        unlocked.append({"key": "spiral-bound-reports", "label": "Spiral Bound Reports", "reason": "Paper plus spiral binding is ready."})
    if sticker_stock and has_cutting:
        unlocked.append({"key": "stickers", "label": "Stickers", "reason": "Sticker stock plus cutting is ready."})

    return unlocked


def _market_guide_or_placeholder(values: list[Decimal]) -> dict[str, Any]:
    stats = _decimal_stats(values)
    enough = len(values) >= MARKET_GUIDE_MIN_SAMPLE_COUNT
    return {
        "min": stats["min"] if enough else None,
        "max": stats["max"] if enough else None,
        "median": stats["median"] if enough else None,
        "mean": stats["mean"] if enough else None,
        "sample_count": stats["sample_count"],
        "has_enough_data": enough,
        "message": None if enough else "Market guide appears after enough anonymous shop samples.",
    }


def _iter_saved_rate_cards():
    from shops.models import Shop

    return (
        Shop.objects.filter(is_active=True)
        .filter(papers__is_active=True, machines__printing_rates__is_active=True)
        .distinct()
        .prefetch_related("papers", "machines__printing_rates", "finishing_rates")
    )


def _safe_saved_rate_cards() -> list[Any]:
    try:
        return list(_iter_saved_rate_cards())
    except (ProgrammingError, OperationalError):
        return []


def _definition_for_canonical_paper(paper: Paper) -> dict[str, Any] | None:
    label = " ".join(
        [
            _normalize_text(getattr(paper, "display_name", "")),
            _normalize_text(getattr(paper, "name", "")),
            _normalize_text(getattr(paper, "category", "")),
            _normalize_text(getattr(paper, "paper_type", "")),
        ]
    ).lower()
    same_gsm = [definition for definition in DEFAULT_PAPER_DEFINITIONS if int(definition.get("gsm") or 0) == int(paper.gsm or 0)]
    if not same_gsm:
        return None
    if "sticker" in label or "tictac" in label or "tic tac" in label:
        return next((definition for definition in same_gsm if _is_sticker_row(definition)), same_gsm[0])
    if "ivory" in label:
        return next((definition for definition in same_gsm if "ivory" in _normalize_text(definition.get("key")).lower()), same_gsm[0])
    if "card" in label or "artcard" in label or "art card" in label:
        return next((definition for definition in same_gsm if "card" in _normalize_text(definition.get("key")).lower()), same_gsm[0])
    return same_gsm[0]


def _printing_rate_for_paper(paper: Paper) -> PrintingRate | None:
    return (
        PrintingRate.objects.filter(machine__shop=paper.shop, sheet_size=paper.sheet_size, is_active=True)
        .select_related("machine")
        .order_by("-is_default", "-color_mode", "id")
        .first()
    )


def _paper_row_from_canonical(paper: Paper) -> dict[str, Any] | None:
    definition = _definition_for_canonical_paper(paper)
    if not definition:
        return None
    rate = _printing_rate_for_paper(paper)
    row = _paper_row_from_definition(definition)
    row.update(
        {
            "paper_base_price": _decimal_string(Decimal(paper.selling_price or 0)),
            "quantity_in_stock": paper.quantity_in_stock if paper.quantity_in_stock is not None else _default_quantity_in_stock(row),
            "active": bool(paper.is_active),
        }
    )
    if rate:
        row.update(
            {
                "single_print_base": _decimal_string(Decimal(rate.single_price or 0)),
                "double_print_base": _decimal_string(
                    Decimal(rate.double_price) if rate.double_price is not None else Decimal(rate.single_price or 0) * Decimal("2")
                ),
                "heavy_paper_surcharge": _decimal_string(Decimal(rate.duplex_surcharge or 0)),
                "surcharge_threshold_gsm": rate.duplex_surcharge_min_gsm or row.get("surcharge_threshold_gsm"),
            }
        )
    return _enrich_paper_row(row, definition=definition)


def _finishing_mode_from_canonical(rate: FinishingRate) -> str:
    if rate.billing_basis == FinishingBillingBasis.PER_SHEET:
        return "per_sheet"
    if rate.billing_basis == FinishingBillingBasis.PER_PIECE:
        return "per_piece"
    if rate.billing_basis in {
        FinishingBillingBasis.FLAT_PER_JOB,
        FinishingBillingBasis.FLAT_PER_GROUP,
        FinishingBillingBasis.FLAT_PER_LINE,
    }:
        return rate.billing_basis
    return "flat_per_job"


def _finishing_definition_for_canonical(rate: FinishingRate) -> dict[str, Any] | None:
    normalized_name = _normalize_text(rate.name).lower()
    normalized_slug = _normalize_text(rate.slug).lower().replace("-", "_")
    for definition in DEFAULT_FINISHING_DEFINITIONS:
        key = _normalize_text(definition.get("key")).lower()
        name = _normalize_text(definition.get("name")).lower()
        if key == normalized_slug or name == normalized_name or key in normalized_slug or name in normalized_name:
            return definition
    return None


def _finishing_row_from_canonical(rate: FinishingRate) -> dict[str, Any]:
    definition = _finishing_definition_for_canonical(rate) or {
        "key": rate.slug or f"finishing-{rate.id}",
        "id": f"finishing-{rate.id}",
        "label": rate.name,
        "name": rate.name,
        "pricing_mode": _finishing_mode_from_canonical(rate),
        "unit": rate.display_unit_label or rate.charge_unit,
        "price": str(rate.price),
        "minimum_charge": str(rate.minimum_charge) if rate.minimum_charge is not None else None,
        "active": rate.is_active,
    }
    row = _finishing_row_from_definition(definition)
    row.update(
        {
            "name": rate.name,
            "label": rate.name,
            "pricing_mode": _finishing_mode_from_canonical(rate),
            "unit": rate.display_unit_label or row.get("unit"),
            "price": _decimal_string(Decimal(rate.price or 0)),
            "minimum_charge": _decimal_string(Decimal(rate.minimum_charge)) if rate.minimum_charge is not None else None,
            "active": bool(rate.is_active),
        }
    )
    return row


def _canonical_paper_rows_for_shop(shop) -> list[dict[str, Any]]:
    rows_by_key: dict[str, dict[str, Any]] = {}
    for paper in Paper.objects.filter(shop=shop, is_active=True).order_by("sheet_size", "gsm", "paper_type", "id"):
        row = _paper_row_from_canonical(paper)
        if row and row["key"] not in rows_by_key:
            rows_by_key[row["key"]] = row
    rows = []
    for definition in DEFAULT_PAPER_DEFINITIONS:
        rows.append(rows_by_key.get(definition["key"]) or (_paper_row_from_definition(definition) | {"active": False}))
    return rows


def _canonical_finishing_rows_for_shop(shop) -> list[dict[str, Any]]:
    rows_by_key: dict[str, dict[str, Any]] = {}
    custom_rows: list[dict[str, Any]] = []
    for rate in FinishingRate.objects.filter(shop=shop, is_active=True).order_by("name", "id"):
        row = _finishing_row_from_canonical(rate)
        if row["key"] in FINISHING_DEFINITION_BY_KEY:
            rows_by_key[row["key"]] = row
        else:
            custom_rows.append(row)
    rows = []
    for definition in DEFAULT_FINISHING_DEFINITIONS:
        rows.append(rows_by_key.get(definition["key"]) or (_finishing_row_from_definition(definition) | {"active": False}))
    return rows + custom_rows


def _paper_category_from_row(row: dict[str, Any]) -> str:
    text = " ".join([_normalize_text(row.get("category")), _normalize_text(row.get("paper_type")), _normalize_text(row.get("label"))]).lower()
    if "sticker" in text or "tictac" in text or "tic tac" in text:
        return PaperCategory.TICTAC
    if "ivory" in text:
        return PaperCategory.IVORY
    if "card" in text or "art" in text:
        return PaperCategory.ARTCARD
    if "gloss" in text:
        return PaperCategory.GLOSS
    if "matt" in text or "matte" in text:
        return PaperCategory.MATTE
    return PaperCategory.OTHER


def _paper_type_from_row(row: dict[str, Any]) -> str:
    text = " ".join([_normalize_text(row.get("paper_type")), _normalize_text(row.get("label"))]).lower()
    if "gloss" in text:
        return PaperType.GLOSS
    if "matt" in text or "matte" in text:
        return PaperType.MATTE
    return PaperType.OTHER


def _ensure_default_machine(shop) -> Machine:
    machine = Machine.objects.filter(shop=shop, is_active=True).order_by("id").first()
    if machine:
        return machine
    return Machine.objects.create(
        shop=shop,
        name="Digital Press",
        machine_type=MachineType.DIGITAL,
        max_width_mm=450,
        max_height_mm=320,
        is_active=True,
    )


def _persist_paper_rows(shop, paper_rows: list[dict[str, Any]]) -> None:
    machine = _ensure_default_machine(shop) if any(_is_active_paper(row) for row in paper_rows) else None
    rate_written_for_sheet: set[str] = set()
    for row in paper_rows:
        definition = _resolve_paper_definition(row)
        if not definition:
            continue
        sheet_size = _normalize_text(row.get("size")) or SheetSize.SRA3
        if sheet_size not in SheetSize.values:
            sheet_size = SheetSize.SRA3
        paper_type = _paper_type_from_row(row)
        defaults = {
            "name": _normalize_text(row.get("label") or row.get("paper_name") or definition.get("label")),
            "category": _paper_category_from_row(row),
            "display_name": _normalize_text(row.get("label") or definition.get("label")),
            "is_cover_stock": int(row.get("gsm") or definition.get("gsm") or 0) >= 170,
            "is_insert_stock": not _is_sticker_row(row),
            "is_sticker_stock": _is_sticker_row(row),
            "buying_price": _to_decimal(row.get("paper_base_price") or "0.00"),
            "selling_price": _to_decimal(row.get("paper_base_price") or "0.00"),
            "quantity_in_stock": int(row.get("quantity_in_stock") or _default_quantity_in_stock(row)),
            "is_active": bool(row.get("active")),
        }
        paper, _ = Paper.objects.update_or_create(
            shop=shop,
            sheet_size=sheet_size,
            gsm=int(row.get("gsm") or definition.get("gsm")),
            paper_type=paper_type,
            defaults=defaults,
        )
        if not row.get("active"):
            paper.is_active = False
            paper.save(update_fields=["is_active", "updated_at"])
            continue
        if machine and sheet_size not in rate_written_for_sheet:
            PrintingRate.objects.update_or_create(
                machine=machine,
                sheet_size=sheet_size,
                color_mode=ColorMode.COLOR,
                defaults={
                    "single_price": _to_decimal(row.get("single_print_base") or DEFAULT_SINGLE_PRINT_BASE),
                    "double_price": _to_decimal(row.get("double_print_base"), allow_null=True),
                    "duplex_surcharge": _to_decimal(row.get("heavy_paper_surcharge") or DEFAULT_HEAVY_PAPER_SURCHARGE),
                    "duplex_surcharge_enabled": _to_decimal(row.get("heavy_paper_surcharge") or DEFAULT_HEAVY_PAPER_SURCHARGE) > 0,
                    "duplex_surcharge_min_gsm": int(row.get("surcharge_threshold_gsm") or DEFAULT_SURCHARGE_THRESHOLD_GSM),
                    "is_active": True,
                    "is_default": not PrintingRate.objects.filter(machine=machine, is_default=True).exists(),
                },
            )
            rate_written_for_sheet.add(sheet_size)


def _finishing_fields_from_mode(mode: str) -> tuple[str, str, str]:
    normalized = _normalize_text(mode).lower()
    if normalized == "per_sheet":
        return ChargeUnit.PER_SHEET, FinishingBillingBasis.PER_SHEET, FinishingSideMode.PER_SELECTED_SIDE
    if normalized in {"per_piece", "per_book"}:
        return ChargeUnit.PER_PIECE, FinishingBillingBasis.PER_PIECE, FinishingSideMode.IGNORE_SIDES
    return ChargeUnit.FLAT, FinishingBillingBasis.FLAT_PER_JOB, FinishingSideMode.IGNORE_SIDES


def _persist_finishing_rows(shop, finishing_rows: list[dict[str, Any]]) -> None:
    for row in finishing_rows:
        definition = _resolve_finishing_definition(row)
        name = _normalize_text(row.get("name") or row.get("label") or (definition or {}).get("name"))
        if not name:
            continue
        slug = slugify(_normalize_text(row.get("key")) or name)
        if not row.get("active"):
            FinishingRate.objects.filter(shop=shop, slug=slug).update(is_active=False)
            continue
        charge_unit, billing_basis, side_mode = _finishing_fields_from_mode(row.get("pricing_mode"))
        price = _to_decimal(row.get("price") or "0.00")
        FinishingRate.objects.update_or_create(
            shop=shop,
            slug=slug,
            defaults={
                "name": name,
                "charge_unit": charge_unit,
                "billing_basis": billing_basis,
                "side_mode": side_mode,
                "price": price,
                "double_side_price": price * Decimal("2") if side_mode == FinishingSideMode.PER_SELECTED_SIDE else None,
                "minimum_charge": _to_decimal(row.get("minimum_charge"), allow_null=True),
                "setup_fee": Decimal("0.00"),
                "is_active": True,
            },
        )


def _pricing_settings_payload(pricing_settings: dict[str, Any]) -> dict[str, Any]:
    broker_margin = pricing_settings.get("broker_margin_percent", pricing_settings.get("gross_margin_percent", Decimal("0.00")))
    service_margin = pricing_settings.get("service_margin_percent", pricing_settings.get("printer_side_fee_percent", Decimal("0.00")))
    return {
        "broker_margin_percent": _decimal_string(Decimal(broker_margin or 0)),
        "service_margin_percent": _decimal_string(Decimal(service_margin or 0)),
        "broker_margin_locked": bool(pricing_settings.get("broker_margin_locked", pricing_settings.get("gross_margin_locked", True))),
        "service_margin_locked": bool(pricing_settings.get("service_margin_locked", pricing_settings.get("printer_side_fee_locked", True))),
    }


def build_market_guides(paper_rows: list[dict[str, Any]], finishing_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    guides: dict[str, dict[str, Any]] = {}
    saved_shops = _safe_saved_rate_cards()

    for row in paper_rows:
        single_values: list[Decimal] = []
        double_values: list[Decimal] = []
        for shop in saved_shops:
            for saved_row in _canonical_paper_rows_for_shop(shop):
                if not saved_row.get("active"):
                    continue
                if _normalize_text(saved_row.get("key")) != _normalize_text(row.get("key")):
                    continue
                try:
                    single_values.append(_to_decimal(saved_row.get("single_side_price")))
                    if saved_row.get("double_side_price") not in (None, ""):
                        parsed_double = _to_decimal(saved_row.get("double_side_price"), allow_null=True)
                        if parsed_double is not None:
                            double_values.append(parsed_double)
                except ValidationError:
                    continue
        guide = {
            "single_side_price": _market_guide_or_placeholder(single_values),
            "double_side_price": _market_guide_or_placeholder(double_values),
        }
        guides[row["key"]] = guide
        guides[row["id"]] = guide

    for row in finishing_rows:
        values: list[Decimal] = []
        for shop in saved_shops:
            for saved_row in _canonical_finishing_rows_for_shop(shop):
                if not saved_row.get("active"):
                    continue
                if _normalize_text(saved_row.get("key")) != _normalize_text(row.get("key")):
                    continue
                try:
                    values.append(_to_decimal(saved_row.get("price")))
                except ValidationError:
                    continue
        guide = {"price": _market_guide_or_placeholder(values)}
        guides[row["key"]] = guide
        guides[row["id"]] = guide

    return guides


def build_business_card_example(paper_rows: list[dict[str, Any]], finishing_rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidate_paper = next((row for row in paper_rows if _is_active_paper(row) and int(row.get("gsm") or 0) >= 250 and not _is_sticker_row(row)), None)
    lamination = next((row for row in finishing_rows if _is_active_finishing(row) and "lamination" in _normalize_text(row.get("name")).lower()), None)
    cutting = next((row for row in finishing_rows if _is_active_finishing(row) and "cutting" in _normalize_text(row.get("name")).lower()), None)

    imposition = build_imposition_breakdown(
        quantity=100,
        finished_width_mm=BUSINESS_CARD_WIDTH_MM,
        finished_height_mm=BUSINESS_CARD_HEIGHT_MM,
        sheet_width_mm=SRA3_WIDTH_MM,
        sheet_height_mm=SRA3_HEIGHT_MM,
    )
    sheets_needed = imposition.good_sheets or 5

    missing_fields: list[str] = []
    if not candidate_paper or candidate_paper.get("double_side_price") in (None, ""):
        missing_fields.append("300gsm double price")
    if lamination is None:
        missing_fields.append("lamination")
    if cutting is None:
        missing_fields.append("cutting")

    print_total = Decimal("0.00")
    lamination_total = Decimal("0.00")
    cutting_total = Decimal("0.00")

    if candidate_paper and candidate_paper.get("double_side_price") not in (None, ""):
        print_total = (_to_decimal(candidate_paper.get("double_side_price"), allow_null=True) or Decimal("0")) * Decimal(sheets_needed)
    if lamination is not None:
        lamination_total = _to_decimal(lamination.get("price")) * Decimal(sheets_needed)
    if cutting is not None:
        cutting_total = _to_decimal(cutting.get("price"))

    estimated_total = print_total + lamination_total + cutting_total
    marketplace_pricing = (
        build_marketplace_pricing_summary(base_price=estimated_total)
        if estimated_total > 0
        else None
    )
    is_complete = not missing_fields
    return {
        "title": "Example: 100 business cards",
        "paper_label": candidate_paper.get("label") if candidate_paper else "300gsm or 350gsm double-sided",
        "sheets_needed": sheets_needed,
        "missing_fields": missing_fields,
        "is_complete": is_complete,
        "is_active": bool(candidate_paper or lamination or cutting),
        "status_text": "Quote proof is ready." if is_complete else f"Waiting for {', '.join(missing_fields)}...",
        "line_items": [
            {
                "key": "print",
                "label": "Print",
                "active": print_total > 0,
                "detail": (
                    f"{sheets_needed} SRA3 sheets x KES {candidate_paper.get('double_side_price')} double-sided"
                    if candidate_paper and candidate_paper.get("double_side_price") not in (None, "") else
                    "Waiting for 300gsm or 350gsm double-sided price"
                ),
                "total": _decimal_string(print_total) if print_total > 0 else None,
            },
            {
                "key": "lamination",
                "label": "Matte lamination",
                "active": lamination is not None,
                "detail": (
                    f"{sheets_needed} sheets x KES {lamination.get('price')}"
                    if lamination is not None else
                    "Waiting for lamination price"
                ),
                "total": _decimal_string(lamination_total) if lamination is not None else None,
            },
            {
                "key": "cutting",
                "label": "Cutting",
                "active": cutting is not None,
                "detail": (
                    f"Flat rate x KES {cutting.get('price')}"
                    if cutting is not None else
                    "Waiting for cutting price"
                ),
                "total": _decimal_string(cutting_total) if cutting is not None else None,
            },
        ],
        "production_cost": _decimal_string(estimated_total) if estimated_total > 0 else None,
        "client_price": marketplace_pricing["client_price"] if marketplace_pricing else None,
        "estimated_total": marketplace_pricing["client_price"] if marketplace_pricing else None,
        "pricing_breakdown": marketplace_pricing,
        "sample_job_previews": _build_sample_job_preview(candidate_paper, finishing_rows) if candidate_paper else [],
    }


def _build_completion_feed(paper_rows: list[dict[str, Any]], finishing_rows: list[dict[str, Any]]) -> list[str]:
    feed: list[str] = []
    if any(_paper_matches(row, gsms=(300,), names=("300",)) for row in paper_rows):
        feed.append("Add 300gsm Matte pricing -> Now you can price business cards, cards, covers")
    if _has_finishing(finishing_rows, ("matt lamination", "matte lamination", "gloss lamination")):
        feed.append("Add Matte Lamination -> Now you can price laminated business cards, menus, covers")
    if _has_finishing(finishing_rows, ("cutting",)):
        feed.append("Add Cutting -> Now you can price finished business cards and flyers")
    if any(_paper_matches(row, gsms=(150, 170), names=("150", "170")) for row in paper_rows):
        feed.append("Add 150gsm / 170gsm -> Now you can price flyers, posters, brochures")
    if _has_finishing(finishing_rows, ("saddle", "stitching")):
        feed.append("Add Saddle Stitching -> Now you can price booklets")
    if _has_finishing(finishing_rows, ("perfect", "spiral", "wire-o", "wire o")):
        feed.append("Add Perfect/Wire-O Binding -> Now you can price books, reports, proposals")
    return feed


def _build_next_suggestions(paper_rows: list[dict[str, Any]], finishing_rows: list[dict[str, Any]]) -> list[str]:
    suggestions: list[str] = []
    if not any(_paper_matches(row, gsms=(300, 350), names=("300", "350")) for row in paper_rows):
        suggestions.append("Start with 300gsm or 350gsm card stock so business cards unlock first.")
    if not _has_finishing(finishing_rows, ("cutting",)):
        suggestions.append("Add Cutting next so finished cards and flyers become quote-ready.")
    if not any(_paper_matches(row, gsms=(130, 150, 170), names=("130", "150", "170")) for row in paper_rows):
        suggestions.append("Add 130gsm, 150gsm, or 170gsm next for flyers and brochures.")
    if not _has_finishing(finishing_rows, ("matt lamination", "matte lamination", "gloss lamination")):
        suggestions.append("Add lamination to unlock premium card work.")
    if not _has_finishing(finishing_rows, ("saddle", "stitching", "perfect", "spiral", "wire-o", "wire o")):
        suggestions.append("Add at least one binding rule for booklets and reports.")
    return suggestions[:3]


def summarize_rate_card(paper_rows: list[dict[str, Any]], finishing_rows: list[dict[str, Any]]) -> dict[str, Any]:
    active_paper_rows = [row for row in paper_rows if _is_active_paper(row)]
    active_finishing_rows = [row for row in finishing_rows if _is_active_finishing(row)]
    unlocked = _build_unlocked_products(paper_rows, finishing_rows)
    capability_preview = [_capability_preview_for_paper(row, finishing_rows) | {"paper_key": row.get("key"), "paper_label": row.get("label")} for row in active_paper_rows]
    return {
        "pricing_items_added": len(active_paper_rows) + len(active_finishing_rows),
        "paper_rows_added": len(active_paper_rows),
        "finishing_rows_added": len(active_finishing_rows),
        "products_unlocked": len(unlocked),
        "unlocked_products": unlocked,
        "capability_preview": capability_preview,
        "completion_feed": _build_completion_feed(paper_rows, finishing_rows),
        "next_suggestions": _build_next_suggestions(paper_rows, finishing_rows),
    }


def _decorate_rate_card_rows(paper_rows: list[dict[str, Any]], finishing_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    decorated_papers: list[dict[str, Any]] = []
    for row in paper_rows:
        enriched = _enrich_paper_row(row)
        enriched["capability_preview"] = _capability_preview_for_paper(enriched, finishing_rows)
        enriched["sample_job_previews"] = _build_sample_job_preview(enriched, finishing_rows) if _is_active_paper(enriched) else []
        decorated_papers.append(enriched)

    decorated_finishings: list[dict[str, Any]] = []
    for row in finishing_rows:
        enriched = deepcopy(row)
        sample_context = next((paper_row["sample_job_previews"][0] for paper_row in decorated_papers if paper_row.get("sample_job_previews")), None)
        if sample_context:
            preview = _build_finishing_preview(
                enriched,
                quantity=100,
                sheets_needed=int(sample_context.get("sheets_needed") or 0),
            )
            enriched["preview"] = preview
            enriched["manager_visible_total"] = preview["manager_visible_total"]
            enriched["shop_visible_formula"] = preview["shop_visible_formula"]
        decorated_finishings.append(enriched)
    return decorated_papers, decorated_finishings


def build_public_rate_card_builder_config() -> dict[str, Any]:
    paper_rows, finishing_rows = _decorate_rate_card_rows(_build_default_paper_rows(), _build_default_finishing_rows())
    summary = summarize_rate_card(paper_rows, finishing_rows)
    pricing_settings = get_marketplace_margin_settings()
    return {
        "paper_rows": paper_rows,
        "finishing_rows": finishing_rows,
        "shop_details": deepcopy(DEFAULT_SHOP_DETAILS),
        "summary": summary,
        "market_guides": build_market_guides(paper_rows, finishing_rows),
        "example_quote": build_business_card_example(paper_rows, finishing_rows),
        "market_label": "Nairobi Market Guide",
        "pricing_settings": _pricing_settings_payload(pricing_settings),
    }


def preview_public_rate_card_builder(*, paper_rows: list[dict[str, Any]], finishing_rows: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_papers = _normalize_paper_rows(paper_rows)
    normalized_finishings = _normalize_finishing_rows(finishing_rows)
    normalized_papers, normalized_finishings = _decorate_rate_card_rows(normalized_papers, normalized_finishings)
    pricing_settings = get_marketplace_margin_settings()
    return {
        "paper_rows": normalized_papers,
        "finishing_rows": normalized_finishings,
        "summary": summarize_rate_card(normalized_papers, normalized_finishings),
        "market_guides": build_market_guides(normalized_papers, normalized_finishings),
        "example_quote": build_business_card_example(normalized_papers, normalized_finishings),
        "market_label": "Nairobi Market Guide",
        "pricing_settings": _pricing_settings_payload(pricing_settings),
    }


def build_shop_rate_card_setup(shop) -> dict[str, Any]:
    paper_rows = _normalize_paper_rows(_canonical_paper_rows_for_shop(shop) or _build_default_paper_rows())
    finishing_rows = _normalize_finishing_rows(_canonical_finishing_rows_for_shop(shop) or _build_default_finishing_rows())
    paper_rows, finishing_rows = _decorate_rate_card_rows(paper_rows, finishing_rows)
    shop_details = _normalize_shop_details(
        {
            "shop_name": getattr(shop, "name", ""),
            "whatsapp_number": getattr(shop, "public_whatsapp_number", "") or getattr(shop, "phone_number", ""),
            "location_area": getattr(shop, "service_area", "") or getattr(shop, "city", ""),
        }
    )
    pricing_settings = get_marketplace_margin_settings(shop)
    return {
        "paper_rows": paper_rows,
        "finishing_rows": finishing_rows,
        "shop_details": shop_details,
        "summary": summarize_rate_card(paper_rows, finishing_rows),
        "market_guides": build_market_guides(paper_rows, finishing_rows),
        "example_quote": build_business_card_example(paper_rows, finishing_rows),
        "market_label": "Nairobi Market Guide",
        "completed": bool(getattr(shop, "pricing_ready", False)),
        "pricing_settings": _pricing_settings_payload(pricing_settings),
    }


def save_shop_rate_card_setup(shop, *, paper_rows: list[dict[str, Any]], finishing_rows: list[dict[str, Any]], shop_details: dict[str, Any] | None = None, completed: bool | None = None) -> dict[str, Any]:
    normalized_papers = _normalize_paper_rows(paper_rows)
    normalized_finishings = _normalize_finishing_rows(finishing_rows)
    normalized_papers, normalized_finishings = _decorate_rate_card_rows(normalized_papers, normalized_finishings)
    normalized_details = _normalize_shop_details(shop_details)
    summary = summarize_rate_card(normalized_papers, normalized_finishings)
    pricing_settings = get_marketplace_margin_settings(shop)

    payload = {
        "paper_rows": normalized_papers,
        "finishing_rows": normalized_finishings,
        "shop_details": normalized_details,
        "summary": summary,
        "market_guides": build_market_guides(normalized_papers, normalized_finishings),
        "example_quote": build_business_card_example(normalized_papers, normalized_finishings),
        "market_label": "Nairobi Market Guide",
        "completed": bool(completed),
        "pricing_settings": _pricing_settings_payload(pricing_settings),
    }

    if normalized_details["shop_name"]:
        shop.name = normalized_details["shop_name"]
    if normalized_details["whatsapp_number"]:
        shop.public_whatsapp_number = normalized_details["whatsapp_number"]
        if not _normalize_text(getattr(shop, "phone_number", "")):
            shop.phone_number = normalized_details["whatsapp_number"]
    if normalized_details["location_area"]:
        shop.service_area = normalized_details["location_area"]
        if not _normalize_text(getattr(shop, "city", "")):
            shop.city = normalized_details["location_area"]

    if completed is not None:
        shop.pricing_ready = bool(completed)
        shop.public_match_ready = bool(completed)

    with transaction.atomic():
        shop.save(update_fields=["name", "public_whatsapp_number", "phone_number", "service_area", "city", "pricing_ready", "public_match_ready", "updated_at"])
        _persist_paper_rows(shop, normalized_papers)
        _persist_finishing_rows(shop, normalized_finishings)
    return payload


def complete_shop_rate_card_setup(shop) -> dict[str, Any]:
    current = build_shop_rate_card_setup(shop)
    payload = save_shop_rate_card_setup(
        shop,
        paper_rows=current["paper_rows"],
        finishing_rows=current["finishing_rows"],
        shop_details=current["shop_details"],
        completed=True,
    )
    return {
        "completed": True,
        "summary": payload["summary"],
        "shop_details": payload["shop_details"],
    }
