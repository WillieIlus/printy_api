from __future__ import annotations

from math import ceil, floor


def ceil_div(value: float, divisor: float) -> int:
    if divisor <= 0:
        return 0
    return int(ceil(value / divisor))


def fit_count(available: float, item: float, gap: float) -> int:
    if available <= 0 or item <= 0:
        return 0
    return max(0, floor((available + gap) / (item + gap)))


def occupied_span(count: int, item: float, gap: float) -> float:
    if count <= 0:
        return 0.0
    return (count * item) + (max(0, count - 1) * gap)


def area(width: float, height: float) -> float:
    if width <= 0 or height <= 0:
        return 0.0
    return float(width) * float(height)


def tiled_panel_count(total_size: float, max_tile_size: float, overlap: float) -> int:
    if total_size <= max_tile_size:
        return 1
    effective_step = max_tile_size - overlap
    if effective_step <= 0:
        return 0
    return ceil_div(total_size - overlap, effective_step)


def average_tile_size(total_size: float, tile_count: int, overlap: float) -> float:
    if tile_count <= 0:
        return 0.0
    return (total_size + (max(tile_count - 1, 0) * overlap)) / tile_count

