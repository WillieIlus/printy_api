"""Quote-related type definitions and dataclasses."""
from dataclasses import dataclass, field, asdict
from decimal import Decimal
from typing import Any


@dataclass
class FinishingLineItem:
    """Single finishing cost line."""

    name: str
    charge_unit: str
    rate_price: str
    computed_cost: str


@dataclass
class PricingResult:
    """Result of quote item pricing computation."""

    can_calculate: bool = False
    pricing_mode: str = ""

    # Imposition (SHEET)
    copies_per_sheet: int = 0
    sheets_needed: int = 0

    # Area (LARGE_FORMAT)
    area_m2: str = "0"

    # Cost components
    paper_cost: str = "0"
    print_cost: str = "0"
    material_cost: str = "0"
    finishing_total: str = "0"
    services_total: str = "0"

    # Totals
    unit_price: str = "0"
    line_total: str = "0"

    # Breakdown details
    finishing_lines: list = field(default_factory=list)
    paper_label: str = ""
    machine_label: str = ""
    sides_label: str = ""
    color_label: str = ""
    material_label: str = ""

    # Missing fields (empty = fully calculable)
    missing_fields: list = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ImpositionResult, SheetLayout, BookletResult live in imposition.py
