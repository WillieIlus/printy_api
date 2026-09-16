"""
Imposition helper for SHEET items.
Simple grid fit with rotation allowed.
"""
from math import ceil


def pieces_per_sheet(
    finished_width_mm: int,
    finished_height_mm: int,
    sheet_width_mm: int,
    sheet_height_mm: int,
    bleed_mm: int = 3,
) -> int:
    """
    Calculate how many pieces fit on one sheet (simple grid fit, rotation allowed).
    Uses both orientations; returns the maximum. At least 1.
    """
    pw = finished_width_mm + 2 * bleed_mm
    ph = finished_height_mm + 2 * bleed_mm
    if pw <= 0 or ph <= 0 or sheet_width_mm <= 0 or sheet_height_mm <= 0:
        return 1
    # Normal orientation
    n1 = max(0, sheet_width_mm // pw) * max(0, sheet_height_mm // ph)
    # Rotated 90°
    n2 = max(0, sheet_width_mm // ph) * max(0, sheet_height_mm // pw)
    return max(1, n1, n2)


def sheets_needed(quantity: int, pieces_per_sheet: int) -> int:
    """Sheets needed = ceil(quantity / pieces_per_sheet)."""
    if pieces_per_sheet <= 0:
        return max(1, quantity)
    return max(1, ceil(quantity / pieces_per_sheet))
