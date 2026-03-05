"""
QuoteCalculator — fast, deterministic pricing for a single quote item.
Staff-only. Does not save; returns JSON for live preview.

Input: product_id, size, quantity, paper_id (or grammage+paper_type), finishing_ids, etc.
Output: sheets_required, imposition, costs, lead_time_estimate_hours

Rounding: KES (2 decimals), minimum suggested_price enforced.
"""
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
from math import ceil
from typing import Optional

from catalog.imposition import pieces_per_sheet as _pieces_per_sheet
from catalog.models import Product
from inventory.choices import SHEET_SIZE_DIMENSIONS, SheetSize
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, Sides
from pricing.models import FinishingRate, PrintingRate


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_OVERHEAD_PERCENT = Decimal("10")  # 10% of base cost
DEFAULT_MARGIN_PERCENT = Decimal("20")   # 20% markup on (cost + overhead)
MIN_SUGGESTED_PRICE_KES = Decimal("50")  # Minimum customer-facing price
LEAD_TIME_BASE_HOURS = 2
LEAD_TIME_PER_100_SHEETS_HOURS = Decimal("0.5")


def _round_kes(value: Decimal) -> Decimal:
    """Round to 2 decimals, half-up (KES standard)."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ImpositionResult:
    per_sheet: int
    orientation: str  # "normal" or "rotated" (whichever gives more)
    sheet_size_used: str


@dataclass
class CostsResult:
    paper_cost: str
    print_cost: str
    finishing_cost: str
    overhead: str
    margin: str
    total_cost: str
    suggested_price: str


@dataclass
class QuoteCalculatorResult:
    sheets_required: int
    imposition: dict
    costs: dict
    lead_time_estimate_hours: str
    can_calculate: bool = True
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_paper(
    shop_id: int,
    paper_id: Optional[int] = None,
    grammage: Optional[int] = None,
    paper_type: Optional[str] = None,
    sheet_size: Optional[str] = None,
) -> Optional[Paper]:
    """Resolve Paper by id or by grammage+paper_type. Returns first match."""
    if paper_id:
        return Paper.objects.filter(shop_id=shop_id, pk=paper_id, is_active=True).first()
    if grammage is not None and paper_type:
        qs = Paper.objects.filter(shop_id=shop_id, gsm=grammage, paper_type=paper_type, is_active=True)
        if sheet_size:
            qs = qs.filter(sheet_size=sheet_size)
        return qs.first()
    return None


def _compute_copies_per_sheet(
    width_mm: int,
    height_mm: int,
    sheet_size: str,
    bleed_mm: int = 3,
) -> tuple[int, str]:
    """Returns (copies_per_sheet, orientation). Uses both orientations; picks best."""
    dims = SHEET_SIZE_DIMENSIONS.get(sheet_size)
    if not dims:
        return 1, "normal"
    sw, sh = dims
    n1 = _pieces_per_sheet(width_mm, height_mm, sw, sh, bleed_mm)
    n2 = _pieces_per_sheet(width_mm, height_mm, sh, sw, bleed_mm)  # sheet rotated 90°
    if n2 > n1:
        return max(1, n2), "rotated"
    return max(1, n1), "normal"


def _compute_finishing_cost(
    finishing_ids: list[int],
    shop_id: int,
    quantity: int,
    area_sqm: Decimal,
    sides_count: int,
    sheets_count: int,
) -> tuple[Decimal, list[dict]]:
    """Compute total finishing cost from finishing_ids. Returns (total, line_items)."""
    if not finishing_ids:
        return Decimal("0"), []
    rates = list(
        FinishingRate.objects.filter(
            shop_id=shop_id,
            pk__in=finishing_ids,
            is_active=True,
        )
    )
    total = Decimal("0")
    lines = []
    for fr in rates:
        p_single = fr.price
        p_double = fr.double_side_price if fr.double_side_price is not None else fr.price * 2
        eff_sides = sides_count
        cost = Decimal("0")
        cu = fr.charge_unit
        if cu == ChargeUnit.PER_PIECE:
            cost = (p_double if eff_sides == 2 else p_single) * quantity
        elif cu == ChargeUnit.PER_SIDE:
            cost = p_single * quantity * eff_sides
        elif cu == ChargeUnit.PER_SHEET:
            cost = fr.price * (sheets_count or max(1, quantity))
            if fr.setup_fee:
                cost += fr.setup_fee
        elif cu == ChargeUnit.PER_SQM:
            cost = fr.price * area_sqm
        elif cu == ChargeUnit.FLAT:
            cost = p_double if eff_sides == 2 else p_single
            if fr.setup_fee:
                cost += fr.setup_fee
        total += cost
        lines.append({"name": fr.name, "charge_unit": cu, "computed_cost": str(_round_kes(cost))})
    return total, lines


def _best_sheet_size_for_paper(paper: Paper) -> str:
    """Return sheet size for paper (paper has sheet_size)."""
    return paper.sheet_size or SheetSize.SRA3


# ---------------------------------------------------------------------------
# Main calculator
# ---------------------------------------------------------------------------

def calculate_quote_item(
    product_id: int,
    quantity: int,
    *,
    width_mm: Optional[int] = None,
    height_mm: Optional[int] = None,
    paper_id: Optional[int] = None,
    grammage: Optional[int] = None,
    paper_type: Optional[str] = None,
    sheet_size: Optional[str] = None,
    finishing_ids: Optional[list[int]] = None,
    machine_id: Optional[int] = None,
    sides: str = Sides.SIMPLEX,
    color_mode: str = "COLOR",
    overhead_percent: Optional[Decimal] = None,
    margin_percent: Optional[Decimal] = None,
) -> QuoteCalculatorResult:
    """
    Compute pricing for a single quote item. Deterministic: same input => same output.
    Does NOT save. Staff-only (caller must enforce).
    """
    result = QuoteCalculatorResult(
        sheets_required=0,
        imposition=asdict(ImpositionResult(per_sheet=1, orientation="normal", sheet_size_used="")),
        costs=asdict(CostsResult(
            paper_cost="0",
            print_cost="0",
            finishing_cost="0",
            overhead="0",
            margin="0",
            total_cost="0",
            suggested_price="0",
        )),
        lead_time_estimate_hours="0",
    )

    product = Product.objects.filter(pk=product_id, is_active=True).select_related("shop").first()
    if not product:
        result.can_calculate = False
        result.reason = "Product not found."
        return result

    if quantity <= 0:
        result.can_calculate = False
        result.reason = "Quantity must be > 0."
        return result

    shop_id = product.shop_id
    w = width_mm if width_mm is not None else (product.default_finished_width_mm or 0)
    h = height_mm if height_mm is not None else (product.default_finished_height_mm or 0)
    bleed = product.default_bleed_mm or 3

    if product.pricing_mode != "SHEET":
        result.can_calculate = False
        result.reason = "Calculator supports SHEET mode only."
        return result

    paper = _resolve_paper(shop_id, paper_id, grammage, paper_type, sheet_size)
    if not paper:
        result.can_calculate = False
        result.reason = "Paper not found. Provide paper_id or (grammage + paper_type)."
        return result

    sheet_size_used = _best_sheet_size_for_paper(paper)
    copies_per_sheet, orientation = _compute_copies_per_sheet(w, h, sheet_size_used, bleed)
    sheets_required = max(1, ceil(quantity / copies_per_sheet))

    result.sheets_required = sheets_required
    result.imposition = asdict(ImpositionResult(
        per_sheet=copies_per_sheet,
        orientation=orientation,
        sheet_size_used=sheet_size_used,
    ))

    # Costs (use buying_price for internal cost)
    paper_cost = paper.buying_price * sheets_required
    print_cost = Decimal("0")
    if machine_id and sides and color_mode:
        machine = Machine.objects.filter(pk=machine_id, shop_id=shop_id, is_active=True).first()
        if machine:
            _, price = PrintingRate.resolve(machine, paper.sheet_size, color_mode, sides)
            if price is not None:
                print_cost = price * sheets_required

    sides_count = 2 if sides == Sides.DUPLEX else 1
    sheet_area = Decimal("0")
    if paper.width_mm and paper.height_mm:
        sheet_area = (Decimal(paper.width_mm) / 1000) * (Decimal(paper.height_mm) / 1000)
    area_sqm = sheet_area * sheets_required

    finishing_ids = finishing_ids or []
    finishing_cost, _ = _compute_finishing_cost(
        finishing_ids, shop_id, quantity, area_sqm, sides_count, sheets_required
    )

    base_cost = paper_cost + print_cost + finishing_cost
    overhead_pct = overhead_percent if overhead_percent is not None else DEFAULT_OVERHEAD_PERCENT
    margin_pct = margin_percent if margin_percent is not None else DEFAULT_MARGIN_PERCENT

    overhead = base_cost * (overhead_pct / 100)
    cost_with_overhead = base_cost + overhead
    margin = cost_with_overhead * (margin_pct / 100)
    total_cost = cost_with_overhead + margin
    suggested_price = max(MIN_SUGGESTED_PRICE_KES, _round_kes(total_cost))

    result.costs = asdict(CostsResult(
        paper_cost=str(_round_kes(paper_cost)),
        print_cost=str(_round_kes(print_cost)),
        finishing_cost=str(_round_kes(finishing_cost)),
        overhead=str(_round_kes(overhead)),
        margin=str(_round_kes(margin)),
        total_cost=str(_round_kes(total_cost)),
        suggested_price=str(suggested_price),
    ))

    # Lead time: base + per 100 sheets
    lead_hours = Decimal(str(LEAD_TIME_BASE_HOURS)) + (
        Decimal(str(sheets_required)) / 100 * LEAD_TIME_PER_100_SHEETS_HOURS
    )
    result.lead_time_estimate_hours = str(_round_kes(lead_hours))

    return result
