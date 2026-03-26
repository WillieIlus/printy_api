from math import ceil


def compute_copies_per_sheet(finished_width_mm: int, finished_height_mm: int, sheet_width_mm: int, sheet_height_mm: int, bleed_mm: int = 3) -> int:
    if not finished_width_mm or not finished_height_mm or not sheet_width_mm or not sheet_height_mm:
        return 1
    piece_width = finished_width_mm + (bleed_mm * 2)
    piece_height = finished_height_mm + (bleed_mm * 2)
    normal = (sheet_width_mm // piece_width) * (sheet_height_mm // piece_height)
    rotated = (sheet_width_mm // piece_height) * (sheet_height_mm // piece_width)
    return max(1, normal, rotated)


def compute_good_sheets(quantity: int, copies_per_sheet: int) -> int:
    if quantity <= 0:
        return 0
    return ceil(quantity / max(1, copies_per_sheet))
