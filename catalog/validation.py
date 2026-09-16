"""
Product configuration validation.
Explicit validation against Product model rules: dimensions, grammage, sheet sizes, pricing mode.
No fuzzy introspection; uses known schema only.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from catalog.choices import PricingMode

# Allow 1mm over max for bleed / rounding (e.g. 106mm when max 105mm for A6 business cards)
DIMENSION_TOLERANCE_MM = 1

if TYPE_CHECKING:
    from catalog.models import Product
    from inventory.models import Paper


def validate_product_configuration(
    product: "Product",
    *,
    paper: "Paper | None" = None,
    width_mm: int | None = None,
    height_mm: int | None = None,
    pricing_mode: str | None = None,
) -> dict:
    """
    Validate a product configuration against product rules.

    Returns:
        {
            "is_valid": bool,
            "errors": list[str],
            "warnings": list[str],
        }

    Rules checked:
        - width/height within min/max range
        - paper.sheet_size in allowed_sheet_sizes (if restricted)
        - paper.gsm within min_gsm..max_gsm
        - pricing_mode matches product.pricing_mode
    """
    errors: list[str] = []
    warnings: list[str] = []

    # --- Dimensions ---
    w = width_mm if width_mm is not None else product.default_finished_width_mm
    h = height_mm if height_mm is not None else product.default_finished_height_mm

    if w is not None and w > 0 and h is not None and h > 0:
        if product.min_width_mm is not None and w < product.min_width_mm:
            errors.append(
                f"Width {w}mm is below minimum {product.min_width_mm}mm."
            )
        if product.min_height_mm is not None and h < product.min_height_mm:
            errors.append(
                f"Height {h}mm is below minimum {product.min_height_mm}mm."
            )
        if product.max_width_mm is not None and w > product.max_width_mm + DIMENSION_TOLERANCE_MM:
            errors.append(
                f"Width {w}mm exceeds maximum {product.max_width_mm}mm (e.g. business cards max A6)."
            )
        if product.max_height_mm is not None and h > product.max_height_mm + DIMENSION_TOLERANCE_MM:
            errors.append(
                f"Height {h}mm exceeds maximum {product.max_height_mm}mm."
            )

    # --- Paper: allowed sheet sizes ---
    if paper is not None:
        allowed = product.allowed_sheet_sizes
        if allowed is not None and len(allowed) > 0:
            sheet_code = paper.sheet_size
            if sheet_code not in allowed:
                errors.append(
                    f"Sheet size {sheet_code} is not allowed. Allowed: {', '.join(allowed)}."
                )

        # --- Paper: grammage range ---
        if product.min_gsm is not None and paper.gsm < product.min_gsm:
            errors.append(
                f"Paper {paper.gsm}gsm is below minimum {product.min_gsm}gsm."
            )
        if product.max_gsm is not None and paper.gsm > product.max_gsm:
            errors.append(
                f"Paper {paper.gsm}gsm exceeds maximum {product.max_gsm}gsm."
            )

    # --- Pricing mode compatibility ---
    if pricing_mode is not None:
        if pricing_mode != product.pricing_mode:
            errors.append(
                f"Pricing mode '{pricing_mode}' does not match product mode '{product.pricing_mode}'."
            )

    is_valid = len(errors) == 0
    return {
        "is_valid": is_valid,
        "errors": errors,
        "warnings": warnings,
    }
