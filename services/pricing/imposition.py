from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from math import ceil, floor

# Orientation sentinel returned when a piece cannot fit on the sheet in any
# orientation. Downstream layers treat this as "non-calculable" rather than
# silently degrading the job to a single copy per sheet.
NO_FIT_ORIENTATION = "none"


def _to_decimal(value) -> Decimal | None:
    """Coerce a millimetre value to Decimal without losing supplied precision.

    Accepts int, float, Decimal or numeric string. Floats are routed through
    ``str()`` so that 106.9 becomes Decimal("106.9") rather than the binary
    expansion of the float. Returns None for anything non-numeric.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    try:
        coerced = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None
    return coerced if coerced.is_finite() else None


def _json_number(value: Decimal | None):
    """Render a Decimal as an int when whole, otherwise as a float.

    Keeps the JSON payload shape stable: whole millimetre inputs stay ints so
    existing API consumers and snapshots are unaffected.
    """
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


@dataclass
class ImpositionBreakdown:
    finished_width_mm: int | float
    finished_height_mm: int | float
    sheet_width_mm: int | float
    sheet_height_mm: int | float
    bleed_mm: int | float
    copies_per_sheet: int
    good_sheets: int
    orientation: str
    explanation: str
    cols: int
    rows: int
    fits: bool

    def to_dict(self) -> dict:
        return asdict(self)


def compute_copies_per_sheet(
    finished_width_mm,
    finished_height_mm,
    sheet_width_mm,
    sheet_height_mm,
    bleed_mm=3,
) -> tuple[int, str]:
    """Return (copies_per_sheet, orientation).

    Both orientations are always evaluated and the larger yield wins. When the
    piece does not fit on the sheet in either orientation the result is
    ``(0, NO_FIT_ORIENTATION)`` -- never a clamped 1, because a 1-up result is
    indistinguishable downstream from a genuine single-copy fit.
    """
    finished_width = _to_decimal(finished_width_mm)
    finished_height = _to_decimal(finished_height_mm)
    sheet_width = _to_decimal(sheet_width_mm)
    sheet_height = _to_decimal(sheet_height_mm)
    bleed = _to_decimal(bleed_mm)
    if bleed is None or bleed < 0:
        bleed = Decimal("0")

    if None in (finished_width, finished_height, sheet_width, sheet_height):
        return 0, NO_FIT_ORIENTATION
    if finished_width <= 0 or finished_height <= 0 or sheet_width <= 0 or sheet_height <= 0:
        return 0, NO_FIT_ORIENTATION

    piece_width = finished_width + (bleed * 2)
    piece_height = finished_height + (bleed * 2)
    if piece_width <= 0 or piece_height <= 0:
        return 0, NO_FIT_ORIENTATION

    # floor() is required here: a partial piece is not a printable piece. The
    # supplied millimetre values are never rounded down before this point.
    normal = int(floor(sheet_width / piece_width)) * int(floor(sheet_height / piece_height))
    rotated = int(floor(sheet_width / piece_height)) * int(floor(sheet_height / piece_width))

    best = max(normal, rotated)
    if best <= 0:
        return 0, NO_FIT_ORIENTATION
    if rotated > normal:
        return rotated, "rotated"
    return normal, "normal"


def compute_good_sheets(quantity: int, copies_per_sheet: int) -> int:
    if copies_per_sheet is None or copies_per_sheet <= 0:
        return 0
    if quantity <= 0:
        return 0
    return ceil(quantity / copies_per_sheet)


def build_imposition_breakdown(
    *,
    quantity: int,
    finished_width_mm,
    finished_height_mm,
    sheet_width_mm,
    sheet_height_mm,
    bleed_mm=3,
) -> ImpositionBreakdown:
    copies_per_sheet, orientation = compute_copies_per_sheet(
        finished_width_mm,
        finished_height_mm,
        sheet_width_mm,
        sheet_height_mm,
        bleed_mm,
    )
    fits = copies_per_sheet > 0
    good_sheets = compute_good_sheets(quantity, copies_per_sheet)

    finished_width = _to_decimal(finished_width_mm) or Decimal("0")
    finished_height = _to_decimal(finished_height_mm) or Decimal("0")
    sheet_width = _to_decimal(sheet_width_mm) or Decimal("0")
    sheet_height = _to_decimal(sheet_height_mm) or Decimal("0")
    bleed = _to_decimal(bleed_mm)
    if bleed is None or bleed < 0:
        bleed = Decimal("0")

    piece_width = finished_width + (bleed * 2)
    piece_height = finished_height + (bleed * 2)

    if not fits:
        cols = rows = 0
        explanation = (
            f"No fit: {finished_width_mm}x{finished_height_mm} mm (plus {bleed_mm} mm bleed each "
            f"side) does not fit on a {sheet_width_mm}x{sheet_height_mm} mm sheet in either "
            "orientation."
        )
    else:
        if orientation == "rotated":
            cols = int(floor(sheet_width / piece_height))
            rows = int(floor(sheet_height / piece_width))
        else:
            cols = int(floor(sheet_width / piece_width))
            rows = int(floor(sheet_height / piece_height))
        explanation = (
            f"{copies_per_sheet} copy/copies per sheet using {orientation} layout; "
            f"{good_sheets} good sheet(s) needed for quantity {quantity}."
        )

    return ImpositionBreakdown(
        finished_width_mm=_json_number(finished_width),
        finished_height_mm=_json_number(finished_height),
        sheet_width_mm=_json_number(sheet_width),
        sheet_height_mm=_json_number(sheet_height),
        bleed_mm=_json_number(bleed),
        copies_per_sheet=copies_per_sheet,
        good_sheets=good_sheets,
        orientation=orientation,
        explanation=explanation,
        cols=cols,
        rows=rows,
        fits=fits,
    )