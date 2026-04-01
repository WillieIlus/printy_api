from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional

from catalog.choices import PricingMode
from inventory.models import Machine, Paper
from pricing.choices import ColorMode, Sides
from pricing.models import Material, PrintingRate
from services.pricing.finishings import compute_finishing_total
from services.pricing.imposition import build_imposition_breakdown


PRICING_MODE_LABELS = {
    PricingMode.SHEET: "Sheet",
    PricingMode.LARGE_FORMAT: "Large format",
}

PRICING_MODE_EXPLANATIONS = {
    PricingMode.SHEET: "Charged per sheet. Price depends on paper, printing sides, and finishing.",
    PricingMode.LARGE_FORMAT: "Charged by area. Price depends on material coverage and finishing.",
}


@dataclass
class PricingEngineResult:
    pricing_mode: str
    quantity: int
    currency: str
    totals: dict
    breakdown: dict
    explanations: list[str]
    vat: dict | None = None
    can_calculate: bool = True
    reason: str = ""
    copies_per_sheet: int | None = None
    good_sheets: int | None = None
    parent_sheets_required: int | None = None
    parent_sheet_name: str | None = None
    rotated: bool | None = None
    roll_width_mm: int | None = None
    roll_length_mm: int | None = None
    tiles_x: int | None = None
    tiles_y: int | None = None
    total_tiles: int | None = None
    explanation_lines: list[str] | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _decimal(value, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def _format_money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _humanize_finishing_explanation(line: dict, currency: str) -> str:
    name = line.get("name", "Finishing")
    billing_basis = line.get("billing_basis")
    side_mode = line.get("side_mode")
    rate = line.get("rate")
    selected_side = line.get("selected_side")
    side_count = line.get("side_count")
    good_sheets = line.get("good_sheets")
    units = line.get("units_count") or line.get("units")
    minimum_charge = line.get("minimum_charge")
    total = line.get("total")
    formula = line.get("formula")

    if billing_basis == "per_sheet" and side_mode == "per_selected_side":
        parts = [f"{name}:", f"{good_sheets} sheets", f"{currency} {rate}"]
        if formula == "good_sheets x both_side_rate":
            parts.append("both-side rate")
        elif selected_side == "both" or side_count == 2:
            parts.append("2 sides")
        else:
            parts.append("1 side")
        explanation = " ".join(part for part in parts if part)
    else:
        explanation = line.get("explanation") or f"{name}: {units} units x {currency} {rate}"

    if minimum_charge and total and Decimal(str(minimum_charge)) > 0 and Decimal(str(total)) == Decimal(str(minimum_charge)):
        explanation += f" (minimum {currency} {minimum_charge})"
    return explanation


def _resolve_vat_summary(shop, subtotal: Decimal) -> dict:
    is_vat_enabled = bool(getattr(shop, "is_vat_enabled", False))
    vat_rate = _decimal(getattr(shop, "vat_rate", Decimal("0")))
    vat_mode = getattr(shop, "vat_mode", "exclusive") or "exclusive"

    if not is_vat_enabled or vat_rate <= 0:
        return {
            "subtotal": subtotal,
            "vat_amount": Decimal("0"),
            "grand_total": subtotal,
            "vat": {
                "amount": _format_money(Decimal("0")),
                "rate": _format_money(vat_rate),
                "is_inclusive": vat_mode == "inclusive",
                "mode": vat_mode,
                "label": "VAT disabled",
            },
        }

    vat_fraction = vat_rate / Decimal("100")
    if vat_mode == "inclusive":
        grand_total = subtotal
        base_subtotal = grand_total / (Decimal("1") + vat_fraction)
        vat_amount = grand_total - base_subtotal
        subtotal_value = base_subtotal
    else:
        subtotal_value = subtotal
        vat_amount = subtotal_value * vat_fraction
        grand_total = subtotal_value + vat_amount

    return {
        "subtotal": subtotal_value,
        "vat_amount": vat_amount,
        "grand_total": grand_total,
        "vat": {
            "amount": _format_money(vat_amount),
            "rate": _format_money(vat_rate),
            "is_inclusive": vat_mode == "inclusive",
            "mode": vat_mode,
            "label": f"VAT {vat_mode}",
        },
    }


def _resolve_print_side_count(sides: str | None) -> int:
    return 2 if sides == Sides.DUPLEX else 1


def _material_area_sqm(width_mm: int, height_mm: int, quantity: int) -> Decimal:
    if not width_mm or not height_mm or quantity <= 0:
        return Decimal("0")
    return (Decimal(width_mm) / 1000) * (Decimal(height_mm) / 1000) * Decimal(quantity)


def select_paper_for_pricing(
    *,
    product,
    shop,
    valid_papers: list[Paper],
    machine: Optional[Machine] = None,
    sides: str | None = None,
    color_mode: str | None = None,
    quantity: Optional[int] = None,
) -> Optional[Paper]:
    if not valid_papers:
        return None
    if len(valid_papers) == 1:
        return valid_papers[0]

    default_paper = next((paper for paper in valid_papers if paper.is_default), None)
    if default_paper:
        return default_paper

    if not machine:
        machine = (
            Machine.objects.filter(shop=shop, is_active=True)
            .filter(printing_rates__is_default=True, printing_rates__is_active=True)
            .distinct()
            .first()
        ) or Machine.objects.filter(shop=shop, is_active=True).first()

    qty = quantity or product.min_quantity or 1
    selected_sides = sides or product.default_sides or Sides.SIMPLEX
    selected_color_mode = color_mode or ColorMode.COLOR

    def pricing_score(paper: Paper):
        sheet_width, sheet_height = paper.get_dimensions_mm()
        imposition = build_imposition_breakdown(
            quantity=qty,
            finished_width_mm=product.default_finished_width_mm or 0,
            finished_height_mm=product.default_finished_height_mm or 0,
            sheet_width_mm=sheet_width or 0,
            sheet_height_mm=sheet_height or 0,
            bleed_mm=product.default_bleed_mm or 3,
        )
        _, print_rate = PrintingRate.resolve(machine, paper.sheet_size, selected_color_mode, selected_sides)
        total_per_sheet = _decimal(paper.selling_price) + _decimal(print_rate)
        return (total_per_sheet * Decimal(imposition.good_sheets), paper.id)

    return sorted(valid_papers, key=pricing_score)[0]


def calculate_sheet_pricing(
    *,
    shop,
    product=None,
    quantity: int,
    paper: Paper,
    machine,
    color_mode: str,
    sides: str,
    finishing_selections: list[dict] | None = None,
    use_cost_price: bool = False,
    width_mm: int | None = None,
    height_mm: int | None = None,
) -> PricingEngineResult:
    sheet_width, sheet_height = paper.get_dimensions_mm()
    imposition = build_imposition_breakdown(
        quantity=quantity,
        finished_width_mm=width_mm or getattr(product, "default_finished_width_mm", 0) or 0,
        finished_height_mm=height_mm or getattr(product, "default_finished_height_mm", 0) or 0,
        sheet_width_mm=sheet_width or 0,
        sheet_height_mm=sheet_height or 0,
        bleed_mm=getattr(product, "default_bleed_mm", 3) or 3,
    )
    resolved_rate, print_rate = PrintingRate.resolve(machine, paper.sheet_size, color_mode, sides)
    paper_rate = _decimal(paper.buying_price if use_cost_price else paper.selling_price)
    print_rate_value = _decimal(print_rate)
    paper_cost = paper_rate * Decimal(imposition.good_sheets)
    print_cost = print_rate_value * Decimal(imposition.good_sheets)
    finishing_total, finishing_lines = compute_finishing_total(
        finishing_selections,
        quantity=quantity,
        good_sheets=imposition.good_sheets,
    )
    subtotal = paper_cost + print_cost + finishing_total
    vat_summary = _resolve_vat_summary(shop, subtotal)
    grand_total = vat_summary["grand_total"]
    unit_price = grand_total / Decimal(quantity) if quantity else Decimal("0")

    explanations = [
        imposition.explanation,
        f"Paper: {paper_rate} x {imposition.good_sheets} sheet(s).",
        f"Printing: {print_rate_value} x {imposition.good_sheets} sheet(s) for {color_mode} / {sides}.",
    ]
    explanations.extend(_humanize_finishing_explanation(line, getattr(shop, "currency", "KES") or "KES") for line in finishing_lines)

    return PricingEngineResult(
        pricing_mode=PricingMode.SHEET,
        quantity=quantity,
        currency=getattr(shop, "currency", "KES") or "KES",
        totals={
            "subtotal": _format_money(vat_summary["subtotal"]),
            "paper_cost": _format_money(paper_cost),
            "print_cost": _format_money(print_cost),
            "material_cost": _format_money(Decimal("0")),
            "finishing_total": _format_money(finishing_total),
            "vat_amount": _format_money(vat_summary["vat_amount"]),
            "vat": _format_money(vat_summary["vat_amount"]),
            "vat_mode": vat_summary["vat"]["mode"],
            "grand_total": _format_money(grand_total),
            "unit_price": _format_money(unit_price),
        },
        breakdown={
            "pricing_mode_label": PRICING_MODE_LABELS[PricingMode.SHEET],
            "pricing_mode_explanation": PRICING_MODE_EXPLANATIONS[PricingMode.SHEET],
            "imposition": imposition.to_dict(),
            "paper": {
                "id": paper.id,
                "label": f"{paper.sheet_size} {paper.gsm}gsm {paper.get_paper_type_display()}",
                "sheet_size": paper.sheet_size,
                "cost_per_sheet": _format_money(paper_rate),
                "total": _format_money(paper_cost),
            },
            "printing": {
                "machine_id": machine.id if machine else None,
                "machine_name": getattr(machine, "name", ""),
                "resolved_rate_id": resolved_rate.id if resolved_rate else None,
                "color_mode": color_mode,
                "sides": sides,
                "side_logic": {
                    "selected_sides": sides,
                    "print_side_count": _resolve_print_side_count(sides),
                },
                "rate_per_sheet": _format_money(print_rate_value),
                "total": _format_money(print_cost),
            },
            "finishings": finishing_lines,
            "vat": vat_summary["vat"],
        },
        explanations=explanations + [
            f"VAT: {_format_money(vat_summary['vat_amount'])} ({vat_summary['vat']['mode']})."
        ],
        vat=vat_summary["vat"],
        copies_per_sheet=imposition.copies_per_sheet,
        good_sheets=imposition.good_sheets,
        parent_sheets_required=imposition.good_sheets,
        parent_sheet_name=paper.sheet_size,
        rotated=imposition.orientation == "rotated",
        explanation_lines=explanations + [
            f"VAT: {_format_money(vat_summary['vat_amount'])} ({vat_summary['vat']['mode']})."
        ],
    )


def calculate_large_format_pricing(
    *,
    shop,
    product=None,
    quantity: int,
    material: Material,
    width_mm: int,
    height_mm: int,
    finishing_selections: list[dict] | None = None,
    use_cost_price: bool = False,
) -> PricingEngineResult:
    area_sqm = _material_area_sqm(width_mm, height_mm, quantity)
    material_rate = _decimal(material.buying_price if use_cost_price else material.selling_price)
    material_cost = material_rate * area_sqm
    finishing_total, finishing_lines = compute_finishing_total(
        finishing_selections,
        quantity=quantity,
        good_sheets=0,
        area_sqm=area_sqm,
    )
    subtotal = material_cost + finishing_total
    vat_summary = _resolve_vat_summary(shop, subtotal)
    grand_total = vat_summary["grand_total"]
    unit_price = grand_total / Decimal(quantity) if quantity else Decimal("0")
    explanations = [
        f"Material area: {area_sqm.quantize(Decimal('0.0001'))} sqm for {quantity} piece(s).",
        f"Material: {material_rate} x {area_sqm.quantize(Decimal('0.0001'))} sqm.",
    ]
    explanations.extend(_humanize_finishing_explanation(line, getattr(shop, "currency", "KES") or "KES") for line in finishing_lines)

    return PricingEngineResult(
        pricing_mode=PricingMode.LARGE_FORMAT,
        quantity=quantity,
        currency=getattr(shop, "currency", "KES") or "KES",
        totals={
            "subtotal": _format_money(vat_summary["subtotal"]),
            "paper_cost": _format_money(Decimal("0")),
            "print_cost": _format_money(Decimal("0")),
            "material_cost": _format_money(material_cost),
            "finishing_total": _format_money(finishing_total),
            "vat_amount": _format_money(vat_summary["vat_amount"]),
            "vat": _format_money(vat_summary["vat_amount"]),
            "vat_mode": vat_summary["vat"]["mode"],
            "grand_total": _format_money(grand_total),
            "unit_price": _format_money(unit_price),
        },
        breakdown={
            "pricing_mode_label": PRICING_MODE_LABELS[PricingMode.LARGE_FORMAT],
            "pricing_mode_explanation": PRICING_MODE_EXPLANATIONS[PricingMode.LARGE_FORMAT],
            "material": {
                "id": material.id,
                "label": f"{material.material_type} ({material.unit})",
                "rate_per_unit": _format_money(material_rate),
                "unit": material.unit,
                "total": _format_money(material_cost),
            },
            "dimensions": {
                "width_mm": width_mm,
                "height_mm": height_mm,
                "area_sqm": str(area_sqm.quantize(Decimal("0.0001"))),
            },
            "finishings": finishing_lines,
            "vat": vat_summary["vat"],
        },
        explanations=explanations + [
            f"VAT: {_format_money(vat_summary['vat_amount'])} ({vat_summary['vat']['mode']})."
        ],
        vat=vat_summary["vat"],
        explanation_lines=explanations + [
            f"VAT: {_format_money(vat_summary['vat_amount'])} ({vat_summary['vat']['mode']})."
        ],
    )
