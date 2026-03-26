from decimal import Decimal

from inventory.models import Paper
from pricing.models import PrintingRate
from services.pricing.finishings import compute_finishing_total
from services.pricing.imposition import compute_copies_per_sheet, compute_good_sheets


def _resolve_sides_multiplier(sides: str | None) -> int:
    return 2 if sides == "DUPLEX" else 1


def build_sheet_pricing(*, product, quantity: int, paper: Paper, machine, color_mode: str, sides: str, finishings: list[dict] | None = None) -> dict:
    copies_per_sheet = compute_copies_per_sheet(
        product.default_finished_width_mm,
        product.default_finished_height_mm,
        paper.width_mm or 0,
        paper.height_mm or 0,
        product.default_bleed_mm or 3,
    )
    good_sheets = compute_good_sheets(quantity, copies_per_sheet)

    rate, print_rate = PrintingRate.resolve(machine, paper.sheet_size, color_mode, sides)
    paper_cost = Decimal(str(paper.selling_price)) * Decimal(good_sheets)
    print_cost = Decimal(str(print_rate or "0")) * Decimal(good_sheets)

    finishing_lines = []
    finishing_total = Decimal("0")
    for entry in finishings or []:
        line = compute_finishing_total(
            entry["rule"],
            quantity=quantity,
            good_sheets=good_sheets,
            selected_side=entry.get("selected_side", "both"),
        )
        finishing_lines.append(line)
        finishing_total += Decimal(line["total"])

    total = paper_cost + print_cost + finishing_total
    return {
        "pricing_mode": "SHEET",
        "quantity": quantity,
        "copies_per_sheet": copies_per_sheet,
        "good_sheets": good_sheets,
        "sides": sides,
        "print_side_count": _resolve_sides_multiplier(sides),
        "paper": {
            "id": paper.id,
            "label": f"{paper.sheet_size} {paper.gsm}gsm",
            "sheet_size": paper.sheet_size,
            "cost_per_sheet": str(paper.selling_price),
            "total": str(paper_cost),
        },
        "printing": {
            "machine_id": machine.id if machine else None,
            "machine_name": getattr(machine, "name", ""),
            "color_mode": color_mode,
            "resolved_rate_id": rate.id if rate else None,
            "rate_per_sheet": str(print_rate or "0"),
            "total": str(print_cost),
        },
        "finishings": finishing_lines,
        "totals": {
            "paper_cost": str(paper_cost),
            "print_cost": str(print_cost),
            "finishing_total": str(finishing_total),
            "grand_total": str(total),
            "unit_price": str(total / Decimal(quantity)) if quantity else "0",
        },
    }
