from dataclasses import asdict, dataclass
from math import ceil


@dataclass
class ImpositionBreakdown:
    finished_width_mm: int
    finished_height_mm: int
    sheet_width_mm: int
    sheet_height_mm: int
    bleed_mm: int
    copies_per_sheet: int
    good_sheets: int
    orientation: str
    explanation: str

    def to_dict(self) -> dict:
        return asdict(self)


def compute_copies_per_sheet(
    finished_width_mm: int,
    finished_height_mm: int,
    sheet_width_mm: int,
    sheet_height_mm: int,
    bleed_mm: int = 3,
) -> tuple[int, str]:
    if not finished_width_mm or not finished_height_mm or not sheet_width_mm or not sheet_height_mm:
        return 1, "normal"

    piece_width = finished_width_mm + (bleed_mm * 2)
    piece_height = finished_height_mm + (bleed_mm * 2)
    normal = (sheet_width_mm // piece_width) * (sheet_height_mm // piece_height)
    rotated = (sheet_width_mm // piece_height) * (sheet_height_mm // piece_width)
    if rotated > normal:
        return max(1, rotated), "rotated"
    return max(1, normal), "normal"


def compute_good_sheets(quantity: int, copies_per_sheet: int) -> int:
    if quantity <= 0:
        return 0
    return ceil(quantity / max(1, copies_per_sheet))


def build_imposition_breakdown(
    *,
    quantity: int,
    finished_width_mm: int,
    finished_height_mm: int,
    sheet_width_mm: int,
    sheet_height_mm: int,
    bleed_mm: int = 3,
) -> ImpositionBreakdown:
    copies_per_sheet, orientation = compute_copies_per_sheet(
        finished_width_mm,
        finished_height_mm,
        sheet_width_mm,
        sheet_height_mm,
        bleed_mm,
    )
    good_sheets = compute_good_sheets(quantity, copies_per_sheet)
    explanation = (
        f"{copies_per_sheet} copy/copies per sheet using {orientation} layout; "
        f"{good_sheets} good sheet(s) needed for quantity {quantity}."
    )
    return ImpositionBreakdown(
        finished_width_mm=finished_width_mm,
        finished_height_mm=finished_height_mm,
        sheet_width_mm=sheet_width_mm,
        sheet_height_mm=sheet_height_mm,
        bleed_mm=bleed_mm,
        copies_per_sheet=copies_per_sheet,
        good_sheets=good_sheets,
        orientation=orientation,
        explanation=explanation,
    )
