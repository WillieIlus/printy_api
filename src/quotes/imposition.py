from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import ceil, floor
from typing import Optional


# -------------------------------------------------------------------
# DECIMAL HELPERS
# -------------------------------------------------------------------
ZERO = Decimal("0")
ONE = Decimal("1")


def to_decimal(value) -> Decimal:
    """
    Convert numeric-like input to Decimal safely.
    Falls back to Decimal('0') on invalid input.
    """
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return ZERO


def clamp_positive_int(value: int, default: int = 1) -> int:
    """
    Ensure integer-like values used as divisors or counts are safe.
    """
    try:
        ivalue = int(value)
    except Exception:
        return default
    return ivalue if ivalue > 0 else default


# -------------------------------------------------------------------
# DATA STRUCTURES
# -------------------------------------------------------------------
@dataclass(frozen=True)
class SheetLayout:
    sheet_width_mm: Decimal
    sheet_height_mm: Decimal
    item_width_mm: Decimal
    item_height_mm: Decimal
    bleed_mm: Decimal = ZERO
    gutter_mm: Decimal = ZERO
    allow_rotation: bool = True


@dataclass(frozen=True)
class ImpositionResult:
    items_per_sheet: int
    sheets_needed: int
    rotated_used: bool


@dataclass(frozen=True)
class PricingReadyResult:
    """Imposition result with waste and pricing-ready outputs."""

    items_per_sheet: int
    sheets_needed: int
    sheets_with_waste: int
    print_impressions: int  # sides × sheets (duplex = 2×, simplex = 1×)
    click_count: int  # typically = print_impressions for digital
    paper_consumption_sheets: int  # = sheets_with_waste
    rotated_used: bool = False


@dataclass(frozen=True)
class BookletSpec:
    """Full booklet specification for imposition-based calculation."""

    quantity: int
    total_pages: int
    final_width_mm: Decimal
    final_height_mm: Decimal
    insert_sheet_width_mm: Decimal
    insert_sheet_height_mm: Decimal
    cover_sheet_width_mm: Decimal
    cover_sheet_height_mm: Decimal
    insert_bleed_mm: Decimal = ZERO
    cover_bleed_mm: Decimal = ZERO
    insert_gutter_mm: Decimal = ZERO
    cover_gutter_mm: Decimal = ZERO
    insert_duplex: bool = True
    cover_duplex: bool = True
    cover_page_count: int = 4


@dataclass(frozen=True)
class BookletResult:
    original_page_count: int
    normalized_page_count: int
    cover_page_count: int
    inner_page_count: int
    inner_sheets_per_copy: int
    total_inner_sheets: int
    total_cover_sheets: int
    quantity: int
    # Imposition-derived (when from BookletSpec)
    insert_pages_per_side: int = 0
    insert_pages_per_sheet: int = 0
    cover_pages_per_side: int = 0
    cover_pages_per_sheet: int = 0


# -------------------------------------------------------------------
# CORE GRID / FITTING
# -------------------------------------------------------------------
def _fit_count(
    available_width: Decimal,
    available_height: Decimal,
    item_width: Decimal,
    item_height: Decimal,
    gutter: Decimal,
) -> int:
    """
    Calculate how many rectangular items fit into a rectangular area.
    Gutter is applied between neighboring items.
    """
    if item_width <= 0 or item_height <= 0:
        return 0

    cols = floor((available_width + gutter) / (item_width + gutter))
    rows = floor((available_height + gutter) / (item_height + gutter))
    return max(cols, 0) * max(rows, 0)


def count_items_on_sheet(layout: SheetLayout) -> tuple[int, bool]:
    """
    Return (best_fit_count, rotated_used).
    Rotation swaps item width/height to maximize fit.
    """
    sheet_w = to_decimal(layout.sheet_width_mm)
    sheet_h = to_decimal(layout.sheet_height_mm)
    bleed = to_decimal(layout.bleed_mm)
    gutter = to_decimal(layout.gutter_mm)

    item_w = to_decimal(layout.item_width_mm) + (bleed * 2)
    item_h = to_decimal(layout.item_height_mm) + (bleed * 2)

    normal_fit = _fit_count(sheet_w, sheet_h, item_w, item_h, gutter)

    if not layout.allow_rotation:
        return normal_fit, False

    rotated_fit = _fit_count(sheet_w, sheet_h, item_h, item_w, gutter)
    if rotated_fit > normal_fit:
        return rotated_fit, True

    return normal_fit, False


def calculate_sheets_needed(quantity: int, items_per_sheet: int) -> int:
    """
    Calculate total sheets required for a flat job.
    """
    safe_quantity = max(int(quantity or 0), 0)
    safe_ips = clamp_positive_int(items_per_sheet, default=1)
    return ceil(safe_quantity / safe_ips)


def apply_waste(
    base_sheets: int,
    extra_sheets: int = 0,
    waste_percent: Decimal | float = ZERO,
) -> int:
    """
    Apply waste to base sheet count.
    - extra_sheets: fixed spoilage / machine setup sheets
    - waste_percent: percentage spoilage (e.g. Decimal('2') for 2%)
    """
    base = max(int(base_sheets or 0), 0)
    extra = max(int(extra_sheets or 0), 0)
    pct = to_decimal(waste_percent)
    percent_extra = ceil(base * pct / Decimal("100")) if pct else 0
    return base + extra + percent_extra


def calculate_imposition(layout: SheetLayout, quantity: int) -> ImpositionResult:
    """
    Full flat-work imposition result for a given layout and quantity.
    """
    ips, rotated_used = count_items_on_sheet(layout)
    sheets = calculate_sheets_needed(quantity, ips)
    return ImpositionResult(
        items_per_sheet=ips,
        sheets_needed=sheets,
        rotated_used=rotated_used,
    )


def calculate_imposition_pricing_ready(
    layout: SheetLayout,
    quantity: int,
    *,
    extra_sheets: int = 0,
    waste_percent: Decimal | float = ZERO,
    sides: int = 2,
) -> PricingReadyResult:
    """
    Imposition result with waste and pricing-ready outputs.
    sides: 1 = simplex, 2 = duplex (impressions = sheets × sides)
    """
    ips, rotated_used = count_items_on_sheet(layout)
    base_sheets = calculate_sheets_needed(quantity, ips)
    sheets_with_waste = apply_waste(base_sheets, extra_sheets, waste_percent)
    impressions = sheets_with_waste * sides
    return PricingReadyResult(
        items_per_sheet=ips,
        sheets_needed=base_sheets,
        sheets_with_waste=sheets_with_waste,
        print_impressions=impressions,
        click_count=impressions,
        paper_consumption_sheets=sheets_with_waste,
        rotated_used=rotated_used,
    )


# -------------------------------------------------------------------
# BOOKLET LOGIC
# -------------------------------------------------------------------
def normalize_booklet_page_count(page_count: int) -> int:
    """
    Saddle-stitched booklets are generally normalized to multiples of 4.
    """
    safe_pages = max(int(page_count or 0), 0)
    remainder = safe_pages % 4
    if remainder:
        safe_pages += 4 - remainder
    return safe_pages


def calculate_booklet_from_spec(spec: BookletSpec) -> BookletResult:
    """
    Calculate booklet breakdown using actual insert/cover imposition.

    Uses SheetLayout + count_items_on_sheet for:
    - pages per side (flat pages on parent sheet)
    - pages per sheet (× sides: 1 simplex, 2 duplex)
    - insert sheets per copy, cover sheets per copy
    """
    qty = max(int(spec.quantity or 0), 0)
    normalized = normalize_booklet_page_count(spec.total_pages)
    cover_pages = max(int(spec.cover_page_count or 0), 0)
    inner_pages = max(normalized - cover_pages, 0)

    # Insert layout: flat page on insert parent sheet
    insert_layout = SheetLayout(
        sheet_width_mm=spec.insert_sheet_width_mm,
        sheet_height_mm=spec.insert_sheet_height_mm,
        item_width_mm=spec.final_width_mm,
        item_height_mm=spec.final_height_mm,
        bleed_mm=spec.insert_bleed_mm,
        gutter_mm=spec.insert_gutter_mm,
        allow_rotation=True,
    )
    insert_pps, _ = count_items_on_sheet(insert_layout)
    insert_pages_per_side = max(1, insert_pps)
    insert_pages_per_sheet = insert_pages_per_side * (2 if spec.insert_duplex else 1)

    inner_sheets_per_copy = ceil(inner_pages / insert_pages_per_sheet) if inner_pages > 0 else 0
    total_inner_sheets = inner_sheets_per_copy * qty

    # Cover layout: cover page on cover parent sheet
    cover_pages_per_sheet = 0
    total_cover_sheets = 0
    cover_pages_per_side = 0

    if cover_pages > 0:
        cover_layout = SheetLayout(
            sheet_width_mm=spec.cover_sheet_width_mm,
            sheet_height_mm=spec.cover_sheet_height_mm,
            item_width_mm=spec.final_width_mm,
            item_height_mm=spec.final_height_mm,
            bleed_mm=spec.cover_bleed_mm,
            gutter_mm=spec.cover_gutter_mm,
            allow_rotation=True,
        )
        cover_pps, _ = count_items_on_sheet(cover_layout)
        cover_pages_per_side = max(1, cover_pps)
        cover_pages_per_sheet = cover_pages_per_side * (2 if spec.cover_duplex else 1)
        cover_sheets_per_copy = ceil(cover_pages / cover_pages_per_sheet)
        total_cover_sheets = cover_sheets_per_copy * qty

    return BookletResult(
        original_page_count=max(int(spec.total_pages or 0), 0),
        normalized_page_count=normalized,
        cover_page_count=cover_pages,
        inner_page_count=inner_pages,
        inner_sheets_per_copy=inner_sheets_per_copy,
        total_inner_sheets=total_inner_sheets,
        total_cover_sheets=total_cover_sheets,
        quantity=qty,
        insert_pages_per_side=insert_pages_per_side,
        insert_pages_per_sheet=insert_pages_per_sheet,
        cover_pages_per_side=cover_pages_per_side,
        cover_pages_per_sheet=cover_pages_per_sheet,
    )


def calculate_booklet_sheets(
    quantity: int,
    page_count: int,
    cover_page_count: int = 4,
) -> BookletResult:
    """
    Simplified booklet calculation (legacy).
    Uses "4 pages per sheet" assumption. Prefer calculate_booklet_from_spec()
    for A4 on A3 etc. with actual imposition.
    """
    safe_quantity = max(int(quantity or 0), 0)
    safe_cover_pages = max(int(cover_page_count or 0), 0)

    normalized_pages = normalize_booklet_page_count(page_count)
    inner_pages = max(normalized_pages - safe_cover_pages, 0)

    inner_sheets_per_copy = ceil(inner_pages / 4) if inner_pages > 0 else 0
    total_inner_sheets = inner_sheets_per_copy * safe_quantity

    total_cover_sheets = safe_quantity if safe_cover_pages > 0 else 0

    return BookletResult(
        original_page_count=max(int(page_count or 0), 0),
        normalized_page_count=normalized_pages,
        cover_page_count=safe_cover_pages,
        inner_page_count=inner_pages,
        inner_sheets_per_copy=inner_sheets_per_copy,
        total_inner_sheets=total_inner_sheets,
        total_cover_sheets=total_cover_sheets,
        quantity=safe_quantity,
    )


# -------------------------------------------------------------------
# DJANGO ADAPTERS
# -------------------------------------------------------------------
def build_layout_from_job(job, *, use_cover_material: bool = False) -> Optional[SheetLayout]:
    """
    Build a SheetLayout from a job-like object.

    Expected attributes:
    - job.size.width_mm / height_mm
    - job.bleed_mm
    - job.gutter_mm
    - job.material.size.width_mm / height_mm
    - optionally job.cover_material.size.width_mm / height_mm
    """
    material = getattr(job, "cover_material", None) if use_cover_material else getattr(job, "material", None)
    if not material or not getattr(material, "size", None) or not getattr(job, "size", None):
        return None

    sheet = material.size
    final_size = job.size

    return SheetLayout(
        sheet_width_mm=to_decimal(sheet.width_mm),
        sheet_height_mm=to_decimal(sheet.height_mm),
        item_width_mm=to_decimal(final_size.width_mm),
        item_height_mm=to_decimal(final_size.height_mm),
        bleed_mm=to_decimal(getattr(job, "bleed_mm", 0)),
        gutter_mm=to_decimal(getattr(job, "gutter_mm", 0)),
        allow_rotation=True,
    )


def get_job_imposition(job) -> Optional[ImpositionResult]:
    """
    Flat-work imposition for the job's main material.
    """
    layout = build_layout_from_job(job, use_cover_material=False)
    if layout is None:
        return None
    return calculate_imposition(layout, quantity=getattr(job, "quantity", 0))


def get_job_imposition_pricing_ready(
    job,
    *,
    extra_sheets: int = 0,
    waste_percent: Decimal | float = ZERO,
    sides: int = 2,
) -> Optional[PricingReadyResult]:
    """
    Pricing-ready imposition for the job's main material.
    Use imposition.sheets_with_waste for paper consumption.
    """
    layout = build_layout_from_job(job, use_cover_material=False)
    if layout is None:
        return None
    sides_attr = getattr(job, "sides", None)
    effective_sides = 2 if (sides_attr == "DUPLEX" or sides_attr == "duplex") else (sides or 1)
    return calculate_imposition_pricing_ready(
        layout,
        quantity=getattr(job, "quantity", 0),
        extra_sheets=extra_sheets,
        waste_percent=waste_percent,
        sides=effective_sides,
    )


def get_cover_imposition(job) -> Optional[ImpositionResult]:
    """
    Flat-work imposition for the job's cover material.
    """
    if not getattr(job, "cover_material", None):
        return None

    layout = build_layout_from_job(job, use_cover_material=True)
    if layout is None:
        return None
    return calculate_imposition(layout, quantity=getattr(job, "quantity", 0))


# -------------------------------------------------------------------
# LEGACY COMPATIBILITY (pieces_per_sheet / sheets_needed)
# -------------------------------------------------------------------
def pieces_per_sheet(
    finished_width_mm: int | float,
    finished_height_mm: int | float,
    sheet_width_mm: int | float,
    sheet_height_mm: int | float,
    bleed_mm: int | float = 3,
) -> int:
    """
    Legacy: Calculate how many pieces fit on one sheet.
    Wraps count_items_on_sheet for backward compatibility.
    """
    layout = SheetLayout(
        sheet_width_mm=to_decimal(sheet_width_mm),
        sheet_height_mm=to_decimal(sheet_height_mm),
        item_width_mm=to_decimal(finished_width_mm),
        item_height_mm=to_decimal(finished_height_mm),
        bleed_mm=to_decimal(bleed_mm),
        gutter_mm=ZERO,
        allow_rotation=True,
    )
    ips, _ = count_items_on_sheet(layout)
    return max(1, ips)


def sheets_needed(quantity: int, pieces_per_sheet_val: int) -> int:
    """
    Legacy: Sheets needed = ceil(quantity / pieces_per_sheet).
    """
    return max(1, calculate_sheets_needed(quantity, pieces_per_sheet_val))
