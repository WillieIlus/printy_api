"""
Pricing diagnostics — actionable summaries when calculations cannot be completed.
Kenyan-printshop friendly messages: what is missing, why it matters, where to fix.
"""
from typing import Any

from pricing.choices import ChargeUnit


def build_suggestion(code: str, message: str, target: dict[str, Any]) -> dict:
    """Build a single suggestion dict."""
    return {"code": code, "message": message, "target": target}


def build_item_diagnostics(
    item,
    missing_raw: list[tuple[str, str]],
    missing_flat: list[str],
    shop_id: int,
) -> dict:
    """
    Build item-level diagnostics from missing fields.
    Returns: {can_calculate, missing_fields, suggestions, reason}
    """
    suggestions = []
    reason_parts = []

    pricing_mode = _get_item_pricing_mode(item)
    paper = getattr(item, "paper", None)
    machine = getattr(item, "machine", None)
    product = getattr(item, "product", None)

    for mf in missing_flat:
        if mf == "paper":
            if paper:
                sheet_hint = f"Add paper selling price for {paper.sheet_size} {paper.gsm}gsm under Shop → Papers."
            elif getattr(item, "spec_text", ""):
                sheet_hint = "Add paper selling price (check spec for size/gsm) under Shop → Papers."
            else:
                sheet_hint = "Add paper selling price under Shop → Papers."
            suggestions.append(
                build_suggestion(
                    "ADD_PAPER",
                    sheet_hint,
                    {"resource": "papers", "shop_id": shop_id},
                )
            )
            reason_parts.append("paper pricing")
        elif mf == "machine":
            suggestions.append(
                build_suggestion(
                    "SELECT_MACHINE",
                    "Select machine for this item.",
                    {"resource": "quote_item", "field": ["machine"]},
                )
            )
            reason_parts.append("machine")
        elif mf == "printing_rate":
            machine_name = machine.name if machine else "machine"
            sheet_size = paper.sheet_size if paper else "sheet"
            color = getattr(item, "color_mode", "") or "COLOR"
            target = (
                {"resource": "printing_rates", "machine_id": machine.id}
                if machine and machine.id
                else {"resource": "machines", "shop_id": shop_id}
            )
            suggestions.append(
                build_suggestion(
                    "ADD_PRINTING_RATE",
                    f"Set {machine_name} printing rate for {sheet_size} {color} (single/double) under Machine → Printing Rates.",
                    target,
                )
            )
            reason_parts.append("printing rate")
        elif mf == "quantity":
            suggestions.append(
                build_suggestion(
                    "ADD_QUANTITY",
                    "Add quantity or set Product.min_quantity.",
                    {"resource": "quote_item", "field": ["quantity"]},
                )
            )
            reason_parts.append("quantity")
        elif mf == "dimensions":
            if pricing_mode == "LARGE_FORMAT":
                suggestions.append(
                    build_suggestion(
                        "ADD_DIMENSIONS",
                        "Add artwork size (width × height) so we can compute area and finishing.",
                        {"resource": "quote_item", "field": ["chosen_width_mm", "chosen_height_mm"]},
                    )
                )
            else:
                suggestions.append(
                    build_suggestion(
                        "ADD_DIMENSIONS",
                        "Add finished size so we can compute pieces per sheet and finishing sheets.",
                        {"resource": "product", "field": ["default_finished_width_mm", "default_finished_height_mm"]},
                    )
                )
            reason_parts.append("dimensions")
        elif mf == "color_mode":
            suggestions.append(
                build_suggestion(
                    "SELECT_COLOR_MODE",
                    "Choose color: Black & White or Color.",
                    {"resource": "quote_item", "field": ["color_mode"]},
                )
            )
            reason_parts.append("color mode")
        elif mf == "sides":
            suggestions.append(
                build_suggestion(
                    "SELECT_SIDES",
                    "Choose sides: Single or Double.",
                    {"resource": "quote_item", "field": ["sides"]},
                )
            )
            reason_parts.append("sides")
        elif mf == "material":
            suggestions.append(
                build_suggestion(
                    "ADD_MATERIAL_PRICE",
                    "Add material with selling price under Shop → Materials.",
                    {"resource": "materials", "shop_id": shop_id},
                )
            )
            reason_parts.append("material")
        elif mf == "product":
            suggestions.append(
                build_suggestion(
                    "SELECT_PRODUCT",
                    "Select a product for this item.",
                    {"resource": "quote_item", "field": ["product"]},
                )
            )
            reason_parts.append("product")
        elif mf == "title":
            suggestions.append(
                build_suggestion(
                    "ADD_TITLE",
                    "Add title or spec for this custom item.",
                    {"resource": "quote_item", "field": ["title", "spec_text"]},
                )
            )
            reason_parts.append("title/spec")

    # PER_SHEET finishing but dimensions missing
    has_per_sheet_finishing = False
    finishings = getattr(item, "finishings", None)
    for qif in (finishings.all() if finishings else []):
        fr = getattr(qif, "finishing_rate", None)
        if fr and getattr(fr, "charge_unit", None) == ChargeUnit.PER_SHEET:
            has_per_sheet_finishing = True
            break
    if has_per_sheet_finishing and "dimensions" in missing_flat:
        suggestions.append(
            build_suggestion(
                "ADD_DIMENSIONS_FOR_FINISHING",
                "Add artwork size so we can compute sheets needed for finishing.",
                {"resource": "quote_item", "field": ["chosen_width_mm", "chosen_height_mm"]}
                if pricing_mode == "LARGE_FORMAT"
                else {"resource": "product", "field": ["default_finished_width_mm", "default_finished_height_mm"]},
            )
        )

    # Deduplicate suggestions by code
    seen_codes = set()
    unique_suggestions = []
    for s in suggestions:
        if s["code"] not in seen_codes:
            seen_codes.add(s["code"])
            unique_suggestions.append(s)

    reason = "; ".join(reason_parts) if reason_parts else "Missing data to calculate price."
    return {
        "can_calculate": False,
        "missing_fields": missing_flat,
        "suggestions": unique_suggestions,
        "reason": reason,
    }


def _get_item_pricing_mode(item) -> str:
    """Return effective pricing mode for item."""
    if item.item_type == "PRODUCT" and item.product_id:
        return getattr(item.product, "pricing_mode", "SHEET") or "SHEET"
    if getattr(item, "pricing_mode", None):
        return item.pricing_mode
    return "LARGE_FORMAT" if item.material_id else "SHEET"


def build_product_diagnostics(
    product,
    missing_fields: list[str],
) -> dict:
    """
    Build diagnostics for product price hint (catalog).
    No item_diagnostics; suggestions point to shop setup.
    """
    suggestions = []
    shop_id = None

    for mf in missing_fields:
        if mf == "paper":
            suggestions.append(
                build_suggestion(
                    "ADD_PAPER",
                    "Add paper with selling price under Shop → Papers.",
                    {"resource": "papers", "shop_id": shop_id},
                )
            )
        elif mf == "machine":
            suggestions.append(
                build_suggestion(
                    "ADD_MACHINE",
                    "Add machine under Shop → Machines.",
                    {"resource": "machines", "shop_id": shop_id},
                )
            )
        elif mf == "printing_rate":
            suggestions.append(
                build_suggestion(
                    "ADD_PRINTING_RATE",
                    "Set printing rate (single/double) for each machine + sheet size under Machine → Printing Rates.",
                    {"resource": "printing_rates", "shop_id": shop_id},
                )
            )
        elif mf == "dimensions":
            mode = getattr(product, "pricing_mode", "SHEET")
            if mode == "LARGE_FORMAT":
                suggestions.append(
                    build_suggestion(
                        "ADD_DIMENSIONS",
                        "Set product default size (width × height) under Shop → Products.",
                        {"resource": "products", "shop_id": shop_id},
                    )
                )
            else:
                suggestions.append(
                    build_suggestion(
                        "ADD_DIMENSIONS",
                        "Set product default size so we can compute pieces per sheet under Shop → Products.",
                        {"resource": "products", "shop_id": shop_id},
                    )
                )
        elif mf == "material":
            suggestions.append(
                build_suggestion(
                    "ADD_MATERIAL_PRICE",
                    "Add material with selling price under Shop → Materials.",
                    {"resource": "materials", "shop_id": shop_id},
                )
            )
        elif mf == "pricing_mode":
            suggestions.append(
                build_suggestion(
                    "SET_PRICING_MODE",
                    "Set pricing mode (Sheet or Large Format) under Shop → Products.",
                    {"resource": "products", "shop_id": shop_id},
                )
            )

    seen_codes = set()
    unique_suggestions = []
    for s in suggestions:
        if s["code"] not in seen_codes:
            seen_codes.add(s["code"])
            unique_suggestions.append(s)

    reason = "Configure papers, machines, and rates under Shop setup." if missing_fields else ""
    return {
        "can_calculate": len(missing_fields) == 0,
        "missing_fields": missing_fields,
        "suggestions": unique_suggestions,
        "reason": reason,
    }


def build_pricing_diagnostics(
    can_calculate: bool,
    reason: str,
    missing_fields: list[str],
    needs_review_items: list[int],
    item_diagnostics: dict[str, dict],
    suggestions: list[dict] | None = None,
) -> dict:
    """
    Build full PricingDiagnostics structure.
    suggestions: aggregated from all items (or pass None to derive from item_diagnostics).
    """
    if suggestions is None:
        seen_codes = set()
        suggestions = []
        for item_id, diag in item_diagnostics.items():
            for s in diag.get("suggestions", []):
                if s["code"] not in seen_codes:
                    seen_codes.add(s["code"])
                    suggestions.append(s)

    return {
        "can_calculate": can_calculate,
        "reason": reason,
        "missing_fields": missing_fields,
        "suggestions": suggestions,
        "needs_review_items": needs_review_items,
        "item_diagnostics": item_diagnostics,
    }
