from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MediaFitResult:
    media: object | None
    fits: bool
    rotated: bool
    item_width_mm: float
    item_height_mm: float
    printable_width_mm: float
    printable_height_mm: float | None
    items_across: int = 0
    items_down: int = 0
    copies_per_sheet: int = 0
    waste_area_mm2: float = 0
    utilization_ratio: float = 0


@dataclass(frozen=True)
class FlatSheetLayoutResult:
    fits: bool
    copies_per_sheet: int
    chosen_orientation: str
    sheet_width_mm: float
    sheet_height_mm: float
    total_sheets: int
    waste_area_mm2: float
    utilization_ratio: float
    media_name: str | None = None
    printable_width_mm: float = 0
    printable_height_mm: float = 0
    items_across: int = 0
    items_down: int = 0
    duplex_note: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RollLayoutResult:
    fits_directly: bool
    rotated: bool
    items_across: int
    total_rows: int
    roll_length_mm: float
    needs_tiling: bool
    tiles_x: int
    tiles_y: int
    total_tiles: int
    tile_width_mm: float
    tile_height_mm: float
    overlap_mm: float
    media_name: str | None = None
    printable_width_mm: float = 0
    waste_width_mm: float = 0
    total_tile_instances: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BookletSpread:
    sheet_number: int
    outer_front: tuple[int, int]
    outer_back: tuple[int, int]
    inner_front: tuple[int, int]
    inner_back: tuple[int, int]


@dataclass(frozen=True)
class BookletLayoutResult:
    valid: bool
    adjusted_page_count: int
    blanks_added: int
    self_cover: bool
    cover_pages: int
    inner_pages: int
    sheets_per_booklet: int
    cover_sheet_count: int
    inner_sheet_count: int
    total_sheet_count: int
    spread_map: list[BookletSpread] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FinishingPlanResult:
    lamination_units: int = 0
    cut_units: int = 0
    fold_units: int = 0
    crease_units: int = 0
    stitch_units: int = 0
    eyelet_units: int = 0
    estimated_cut_passes: int = 0
    extra_finish_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class QuoteSummaryResult:
    engine_type: str
    media_name: str | None
    parent_sheets_required: int = 0
    roll_length_required_mm: float = 0
    finishing: FinishingPlanResult | None = None
    layout_result: object | None = None
    notes: list[str] = field(default_factory=list)
