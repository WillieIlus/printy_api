"""
Booklet-specific logic for quote items.
Handles saddle-stitch, perfect bind, etc.
Uses actual insert/cover imposition from imposition.py.
"""
from decimal import Decimal

from .imposition import (
    BookletResult,
    BookletSpec,
    calculate_booklet_from_spec,
    calculate_booklet_sheets,
    normalize_booklet_page_count,
    to_decimal,
    ZERO,
)


def build_a4_spec(
    quantity: int,
    total_pages: int,
    *,
    insert_sheet: str = "A3",
    cover_sheet: str = "A3",
    cover_page_count: int = 4,
    insert_duplex: bool = True,
    cover_duplex: bool = True,
    bleed_mm: Decimal | float = 3,
) -> BookletSpec:
    """
    Build BookletSpec for A4 finished book on common parent sheets.
    insert_sheet/cover_sheet: "A3", "SRA3", or (width_mm, height_mm).
    """
    # A4 finished
    a4_w = Decimal("210")
    a4_h = Decimal("297")

    def _sheet_dims(name: str) -> tuple[Decimal, Decimal]:
        if name == "A3":
            return Decimal("297"), Decimal("420")  # short × long
        if name == "SRA3":
            return Decimal("320"), Decimal("450")
        if isinstance(name, (tuple, list)) and len(name) >= 2:
            return to_decimal(name[0]), to_decimal(name[1])
        return Decimal("297"), Decimal("420")

    iw, ih = _sheet_dims(insert_sheet)
    cw, ch = _sheet_dims(cover_sheet)
    bleed = to_decimal(bleed_mm)

    return BookletSpec(
        quantity=quantity,
        total_pages=total_pages,
        final_width_mm=a4_w,
        final_height_mm=a4_h,
        insert_sheet_width_mm=iw,
        insert_sheet_height_mm=ih,
        cover_sheet_width_mm=cw,
        cover_sheet_height_mm=ch,
        insert_bleed_mm=bleed,
        cover_bleed_mm=bleed,
        insert_gutter_mm=ZERO,
        cover_gutter_mm=ZERO,
        insert_duplex=insert_duplex,
        cover_duplex=cover_duplex,
        cover_page_count=cover_page_count,
    )


def sheets_for_booklet(page_count: int, sheets_per_signature: int = 4) -> int:
    """
    Legacy: Calculate sheets needed for a booklet.
    Prefer calculate_booklet_from_spec() for full breakdown.
    """
    normalized = normalize_booklet_page_count(page_count)
    if normalized <= 0:
        return 0
    return normalized // 4


def booklet_imposition_pages(page_count: int) -> int:
    """Pages needed for imposition (including blank pages for signature alignment)."""
    return max(4, normalize_booklet_page_count(page_count))
