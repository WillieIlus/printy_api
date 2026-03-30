"""
QuoteCalculator - fast, deterministic pricing for a single quote item.
Staff-only. Does not save; returns JSON for live preview.

Input: product_id, size, quantity, paper_id (or grammage+paper_type), finishing_ids, etc.
Output: sheets_required, imposition, costs, lead_time_estimate_hours

Rounding: KES (2 decimals), minimum suggested_price enforced.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from catalog.models import Product
from inventory.choices import SheetSize
from inventory.models import Machine, Paper
from pricing.choices import Sides
from pricing.models import FinishingRate
from services.engine.integration import build_job_spec, build_media_spec_from_paper, classify_finishing_spec
from services.engine.services.quote_calculator import QuoteCalculator as EngineQuoteCalculator
from services.pricing.engine import calculate_sheet_pricing


DEFAULT_OVERHEAD_PERCENT = Decimal("10")
DEFAULT_MARGIN_PERCENT = Decimal("20")
MIN_SUGGESTED_PRICE_KES = Decimal("50")
LEAD_TIME_BASE_HOURS = 2
LEAD_TIME_PER_100_SHEETS_HOURS = Decimal("0.5")


def _round_kes(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class ImpositionResult:
    per_sheet: int
    orientation: str
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


def _resolve_paper(
    shop_id: int,
    paper_id: Optional[int] = None,
    grammage: Optional[int] = None,
    paper_type: Optional[str] = None,
    sheet_size: Optional[str] = None,
) -> Optional[Paper]:
    if paper_id:
        return Paper.objects.filter(shop_id=shop_id, pk=paper_id, is_active=True).first()
    if grammage is not None and paper_type:
        qs = Paper.objects.filter(shop_id=shop_id, gsm=grammage, paper_type=paper_type, is_active=True)
        if sheet_size:
            qs = qs.filter(sheet_size=sheet_size)
        return qs.first()
    return None


def _best_sheet_size_for_paper(paper: Paper) -> str:
    return paper.sheet_size or SheetSize.SRA3


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
    result = QuoteCalculatorResult(
        sheets_required=0,
        imposition=asdict(ImpositionResult(per_sheet=1, orientation="normal", sheet_size_used="")),
        costs=asdict(
            CostsResult(
                paper_cost="0",
                print_cost="0",
                finishing_cost="0",
                overhead="0",
                margin="0",
                total_cost="0",
                suggested_price="0",
            )
        ),
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

    if product.pricing_mode != "SHEET":
        result.can_calculate = False
        result.reason = "Calculator supports SHEET mode only."
        return result

    paper = _resolve_paper(product.shop_id, paper_id, grammage, paper_type, sheet_size)
    if not paper:
        result.can_calculate = False
        result.reason = "Paper not found. Provide paper_id or (grammage + paper_type)."
        return result

    resolved_width = width_mm if width_mm is not None else (product.default_finished_width_mm or 0)
    resolved_height = height_mm if height_mm is not None else (product.default_finished_height_mm or 0)
    finishing_rules = list(
        FinishingRate.objects.filter(shop_id=product.shop_id, pk__in=(finishing_ids or []), is_active=True)
    )
    engine_summary = EngineQuoteCalculator().calculate(
        build_job_spec(
            product=product,
            quantity=quantity,
            width_mm=resolved_width,
            height_mm=resolved_height,
            sides=sides,
        ),
        [build_media_spec_from_paper(paper)],
        classify_finishing_spec(finishing_rules, print_sides=sides),
    )
    layout = engine_summary.layout_result
    if not getattr(layout, "fits", False):
        result.can_calculate = False
        result.reason = "Selected sheet does not fit the finished size."
        return result

    machine = None
    if machine_id and sides and color_mode:
        machine = Machine.objects.filter(pk=machine_id, shop_id=product.shop_id, is_active=True).first()

    finishings = [{"rule": rule, "selected_side": "both"} for rule in finishing_rules]
    pricing = calculate_sheet_pricing(
        shop=product.shop,
        product=product,
        quantity=quantity,
        paper=paper,
        machine=machine,
        color_mode=color_mode,
        sides=sides,
        finishing_selections=finishings,
        use_cost_price=True,
        width_mm=resolved_width,
        height_mm=resolved_height,
    ).to_dict()

    result.sheets_required = layout.total_sheets
    result.imposition = asdict(
        ImpositionResult(
            per_sheet=layout.copies_per_sheet,
            orientation=layout.chosen_orientation,
            sheet_size_used=_best_sheet_size_for_paper(paper),
        )
    )
    result.imposition["utilization_ratio"] = layout.utilization_ratio
    result.imposition["waste_area_mm2"] = layout.waste_area_mm2
    result.imposition["items_across"] = layout.items_across
    result.imposition["items_down"] = layout.items_down

    paper_cost = Decimal(pricing["totals"]["paper_cost"])
    print_cost = Decimal(pricing["totals"]["print_cost"])
    finishing_cost = Decimal(pricing["totals"]["finishing_total"])

    base_cost = paper_cost + print_cost + finishing_cost
    overhead_pct = overhead_percent if overhead_percent is not None else DEFAULT_OVERHEAD_PERCENT
    margin_pct = margin_percent if margin_percent is not None else DEFAULT_MARGIN_PERCENT
    overhead = base_cost * (overhead_pct / 100)
    cost_with_overhead = base_cost + overhead
    margin = cost_with_overhead * (margin_pct / 100)
    total_cost = cost_with_overhead + margin
    suggested_price = max(MIN_SUGGESTED_PRICE_KES, _round_kes(total_cost))

    result.costs = asdict(
        CostsResult(
            paper_cost=str(_round_kes(paper_cost)),
            print_cost=str(_round_kes(print_cost)),
            finishing_cost=str(_round_kes(finishing_cost)),
            overhead=str(_round_kes(overhead)),
            margin=str(_round_kes(margin)),
            total_cost=str(_round_kes(total_cost)),
            suggested_price=str(suggested_price),
        )
    )

    lead_hours = Decimal(str(LEAD_TIME_BASE_HOURS)) + (
        Decimal(str(result.sheets_required)) / 100 * LEAD_TIME_PER_100_SHEETS_HOURS
    )
    result.lead_time_estimate_hours = str(_round_kes(lead_hours))
    return result
