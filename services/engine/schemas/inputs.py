from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaSpec:
    name: str
    width_mm: float
    height_mm: float | None = None
    is_roll: bool = False
    printable_margin_top_mm: float = 0
    printable_margin_right_mm: float = 0
    printable_margin_bottom_mm: float = 0
    printable_margin_left_mm: float = 0

    @property
    def printable_width_mm(self) -> float:
        return max(
            0.0,
            float(self.width_mm)
            - float(self.printable_margin_left_mm)
            - float(self.printable_margin_right_mm),
        )

    @property
    def printable_height_mm(self) -> float | None:
        if self.height_mm is None:
            return None
        return max(
            0.0,
            float(self.height_mm)
            - float(self.printable_margin_top_mm)
            - float(self.printable_margin_bottom_mm),
        )


@dataclass(frozen=True)
class JobSpec:
    product_type: str
    finished_width_mm: float
    finished_height_mm: float
    quantity: int
    bleed_mm: float = 0
    gap_mm: float = 0
    allow_rotation: bool = True
    sides: int = 1
    pages: int = 0
    cover_pages: int = 0
    inner_pages: int = 0
    roll_overlap_mm: float = 0
    tile_max_length_mm: float | None = None


@dataclass(frozen=True)
class FinishingSpec:
    lamination_sides: int = 0
    lamination_mode: str | None = None
    cutting_mode: str | None = None
    folding_lines: int = 0
    crease_lines: int = 0
    stitched: bool = False
    eyelets: int = 0
    hems: bool = False
    welds: bool = False
