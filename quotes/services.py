"""
Quote engine — calculates prices for quote items using FK references only.
Never resolves Paper/Material/Finishing by attributes; uses QuoteItem.paper,
QuoteItem.material, QuoteItemFinishing.finishing_rate.
"""
from decimal import Decimal
from math import ceil

from django.utils import timezone

from catalog.choices import PricingMode
from catalog.imposition import pieces_per_sheet, sheets_needed
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode, ServicePricingType, Sides
from pricing.models import FinishingRate, PrintingRate, ServiceRate
from quotes.choices import QuoteStatus
from quotes.diagnostics import (
    build_item_diagnostics,
    build_pricing_diagnostics,
)
from quotes.models import QuoteItem, QuoteRequest


# ---------------------------------------------------------------------------
# Price calculation diagnostics (missing fields + formula description)
# ---------------------------------------------------------------------------


def _get_effective_pricing_mode(item: QuoteItem) -> str:
    """Return pricing_mode for calculation: from product (PRODUCT) or item (CUSTOM)."""
    if item.item_type == "PRODUCT" and item.product_id:
        return item.product.pricing_mode or "SHEET"
    # CUSTOM: use item's pricing_mode; default SHEET unless material provided
    if item.pricing_mode:
        return item.pricing_mode
    return "LARGE_FORMAT" if item.material_id else "SHEET"


def get_quote_item_missing_fields(item: QuoteItem) -> list[tuple[str, str]]:
    """
    Return list of (model_label, field_name) that must be filled for price to calculate.
    E.g. [("QuoteItem", "paper"), ("PrintingRate", "one_side_price")].
    """
    missing = []
    product = item.product
    quantity = item.quantity or 0
    pricing_mode = _get_effective_pricing_mode(item)

    if item.item_type == "PRODUCT" and not product:
        missing.append(("QuoteItem", "product"))
        return missing
    if item.item_type == "CUSTOM" and not item.title and not item.spec_text:
        missing.append(("QuoteItem", "title or spec_text"))
        return missing
    if quantity <= 0:
        missing.append(("QuoteItem", "quantity"))
        return missing

    if pricing_mode == PricingMode.SHEET:
        if product and (not product.default_finished_width_mm or not product.default_finished_height_mm):
            missing.append(("Product", "default_finished_width_mm, default_finished_height_mm"))
        if not item.paper_id:
            missing.append(("QuoteItem", "paper"))
            return missing
        paper = item.paper
        if not paper.selling_price:
            missing.append(("Paper", "selling_price"))
        if item.machine_id and item.sides and item.color_mode:
            rate, price = PrintingRate.resolve(
                item.machine, paper.sheet_size, item.color_mode, item.sides, paper=paper
            )
            if rate is None:
                missing.append(
                    (
                        "PrintingRate",
                        f"Create rate: machine + {paper.sheet_size} + {item.color_mode}",
                    )
                )
            elif price is None:
                missing.append(
                    (
                        "PrintingRate",
                        "single_price / double_price",
                    )
                )
        else:
            if not item.machine_id:
                missing.append(("QuoteItem", "machine"))
            if not item.sides:
                missing.append(("QuoteItem", "sides"))
            if not item.color_mode:
                missing.append(("QuoteItem", "color_mode"))
        if paper.width_mm is None or paper.height_mm is None:
            missing.append(("Paper", "width_mm, height_mm (for PER_SQM finishing)"))

    elif pricing_mode == PricingMode.LARGE_FORMAT:
        if not item.material_id:
            missing.append(("QuoteItem", "material"))
            return missing
        if not item.chosen_width_mm:
            missing.append(("QuoteItem", "chosen_width_mm"))
        if not item.chosen_height_mm:
            missing.append(("QuoteItem", "chosen_height_mm"))
        material = item.material
        if material and not material.selling_price:
            missing.append(("Material", "selling_price"))

    else:
        missing.append(("QuoteItem", "pricing_mode (SHEET or LARGE_FORMAT)"))

    return missing


def get_quote_item_calculation_description(item: QuoteItem) -> str:
    """
    Human-readable description of how the price is calculated.
    """
    from quotes.pricing_service import compute_quote_item_pricing

    pricing = compute_quote_item_pricing(item)
    if pricing.calculation_description:
        extra = "\n".join(pricing.explanations or [])
        return f"{pricing.calculation_description}\n{extra}".strip()

    product = item.product
    quantity = item.quantity or 0
    pricing_mode = _get_effective_pricing_mode(item)
    if quantity <= 0:
        return "Set quantity first."
    if item.item_type == "PRODUCT" and not product:
        return "Set product first."
    if item.item_type == "CUSTOM" and not item.title and not item.spec_text:
        return "Set title or spec_text for CUSTOM item."

    lines = []

    if pricing_mode == PricingMode.SHEET:
        lines.append("SHEET mode:")
        lines.append("  copies_per_sheet = auto from product (width+6)×(height+6) on sheet")
        lines.append("  sheets = ceil(quantity / copies_per_sheet)")
        lines.append("  paper_cost = Paper.selling_price × sheets")
        lines.append("  print_cost = PrintingRate.single_price|double_price × sheets")
        lines.append("  [PrintingRate: Machine + sheet_size + color_mode]")
        if item.paper_id and product and item.paper.width_mm and item.paper.height_mm:
            pieces = product.get_copies_per_sheet(
                item.paper.sheet_size, item.paper.width_mm, item.paper.height_mm
            )
            sheets_val = sheets_needed(quantity, pieces)
            lines.append(f"  → {quantity} qty, {pieces} up → {sheets_val} sheets")
        elif item.paper_id:
            lines.append(f"  → {quantity} qty (CUSTOM: 1 per sheet)")
        lines.append("  + finishing: PER_PIECE|PER_SIDE|PER_SHEET|PER_SQM|FLAT from FinishingRate")
        lines.append("  line_total = paper + print + finishing; unit_price = line_total / quantity")

    elif pricing_mode == PricingMode.LARGE_FORMAT:
        lines.append("LARGE_FORMAT mode:")
        lines.append("  area_sqm = (chosen_width_mm/1000) × (chosen_height_mm/1000) × quantity")
        lines.append("  base = Material.selling_price × area_sqm")
        lines.append("  + finishing costs")
        lines.append("  line_total = base + finishing; unit_price = line_total / quantity")

    else:
        lines.append("Product must have pricing_mode = SHEET or LARGE_FORMAT.")

    return "\n".join(lines)


def _sides_count(sides: str | None) -> int:
    """Return number of sides: 1 for SIMPLEX, 2 for DUPLEX."""
    if not sides:
        return 1
    return 2 if sides == Sides.DUPLEX else 1


def _sheet_area_sqm(paper) -> Decimal:
    """Area of one sheet in sqm. Paper has width_mm, height_mm."""
    w = paper.width_mm or 0
    h = paper.height_mm or 0
    if not w or not h:
        return Decimal("0")
    return (Decimal(w) / 1000) * (Decimal(h) / 1000)


def _apply_finishing_cost(
    finishing_rate: FinishingRate,
    quantity: int,
    area_sqm: Decimal,
    sides_count: int,
    price_override: Decimal | None,
    apply_to_sides: str = "BOTH",
    sheets_needed_val: int = 0,
) -> Decimal:
    """
    Apply finishing cost by charge_unit.
    PER_PIECE -> price × qty
    PER_SIDE -> price × qty × sides
    PER_SHEET -> price × sheets_needed
    PER_SQM -> price × area_sqm
    FLAT -> flat price (+ setup_fee)
    """
    total = Decimal("0")
    if price_override is not None:
        price_single = price_override
        price_double = price_override * 2
    else:
        price_single = finishing_rate.price
        price_double = (
            finishing_rate.double_side_price
            if finishing_rate.double_side_price is not None
            else finishing_rate.price * 2
        )
    if apply_to_sides == "SINGLE":
        effective_sides = 1
    elif apply_to_sides == "DOUBLE":
        effective_sides = 2
    else:
        effective_sides = sides_count

    if finishing_rate.charge_unit == ChargeUnit.PER_PIECE:
        total += (price_double if effective_sides == 2 else price_single) * quantity
    elif finishing_rate.charge_unit == ChargeUnit.PER_SIDE:
        total += price_single * quantity * effective_sides
    elif finishing_rate.charge_unit == ChargeUnit.PER_SHEET:
        sheets = sheets_needed_val or max(1, quantity)
        lamination_side_pricing = (
            finishing_rate.billing_basis == FinishingBillingBasis.PER_SHEET
            and finishing_rate.side_mode == FinishingSideMode.PER_SELECTED_SIDE
        )
        if lamination_side_pricing:
            sheet_rate = (
                finishing_rate.double_side_price
                if effective_sides == 2 and finishing_rate.double_side_price is not None
                else finishing_rate.price * effective_sides
            )
            total += sheet_rate * sheets
        else:
            total += finishing_rate.price * sheets
        if finishing_rate.setup_fee:
            total += finishing_rate.setup_fee
    elif finishing_rate.charge_unit == ChargeUnit.PER_SIDE_PER_SHEET:
        sheets = sheets_needed_val or max(1, quantity)
        sheet_rate = (
            finishing_rate.double_side_price
            if effective_sides == 2 and finishing_rate.double_side_price is not None
            else price_single * effective_sides
        )
        total += sheet_rate * sheets
        if finishing_rate.setup_fee:
            total += finishing_rate.setup_fee
    elif finishing_rate.charge_unit == ChargeUnit.PER_SQM:
        total += finishing_rate.price * area_sqm
    elif finishing_rate.charge_unit == ChargeUnit.FLAT:
        total += price_double if effective_sides == 2 else price_single
        if finishing_rate.setup_fee:
            total += finishing_rate.setup_fee

    return total


def _get_service_price(
    service_rate: ServiceRate,
    price_override: Decimal | None,
    distance_km: Decimal | None = None,
) -> Decimal | None:
    """Resolve service price. Returns None if cannot resolve (e.g. TIERED without distance)."""
    if price_override is not None:
        return price_override
    if service_rate.pricing_type == ServicePricingType.FIXED:
        return service_rate.price
    if service_rate.pricing_type == ServicePricingType.TIERED_DISTANCE:
        return service_rate.get_price_for_distance(distance_km)
    return None


def calculate_quote_item(item: QuoteItem, force: bool = False) -> tuple[Decimal, Decimal]:
    """
    Calculate unit_price and line_total for a QuoteItem.
    Uses FK references: paper, material, finishings (QuoteItemFinishing).
    Supports PRODUCT and CUSTOM item types.
    If pricing_locked_at is set and force=False, returns existing values without recalc.
    """
    if item.pricing_locked_at and not force:
        return (
            item.unit_price or Decimal("0"),
            item.line_total or Decimal("0"),
        )

    product = item.product
    quantity = item.quantity or 0
    pricing_mode = _get_effective_pricing_mode(item)

    if quantity <= 0:
        return Decimal("0"), Decimal("0")
    if item.item_type == "PRODUCT" and not product:
        return Decimal("0"), Decimal("0")

    total = Decimal("0")
    area_sqm = Decimal("0")
    sides_count = _sides_count(item.sides)
    sheets_count = 0

    if pricing_mode == PricingMode.SHEET:
        # SHEET mode: must have paper FK
        if not item.paper_id:
            return Decimal("0"), Decimal("0")

        paper = item.paper
        # Imposition: pieces_per_sheet from dimensions; sheets_needed = ceil(qty / pieces_per_sheet)
        if product and paper.width_mm and paper.height_mm:
            pieces = product.get_copies_per_sheet(
                paper.sheet_size, paper.width_mm, paper.height_mm
            )
        else:
            pieces = 1
        sheets_count = sheets_needed(quantity, pieces)
        total += paper.selling_price * sheets_count

        # Printing cost: resolve PrintingRate by machine+sheet_size+sides+color_mode
        if item.machine_id and item.sides and item.color_mode:
            _, print_price = PrintingRate.resolve(
                item.machine, paper.sheet_size, item.color_mode, item.sides, paper=paper
            )
            if print_price is not None:
                total += print_price * sheets_count

        # Area for PER_SQM finishing
        sheet_area = _sheet_area_sqm(paper)
        area_sqm = sheet_area * sheets_count

    elif pricing_mode == PricingMode.LARGE_FORMAT:
        # LARGE_FORMAT: must have material FK and chosen dimensions
        if not item.material_id or not item.chosen_width_mm or not item.chosen_height_mm:
            return Decimal("0"), Decimal("0")

        material = item.material
        w_mm = item.chosen_width_mm
        h_mm = item.chosen_height_mm
        area_sqm = (Decimal(w_mm) / 1000) * (Decimal(h_mm) / 1000) * quantity

        base = material.selling_price * area_sqm
        total += base

    else:
        return Decimal("0"), Decimal("0")

    # Apply finishing costs (from QuoteItemFinishing FK; uses sheets_count for PER_SHEET)
    for qif in item.finishings.select_related("finishing_rate").all():
        total += _apply_finishing_cost(
            qif.finishing_rate,
            quantity,
            area_sqm,
            sides_count,
            qif.price_override,
            getattr(qif, "apply_to_sides", None) or "BOTH",
            sheets_needed_val=sheets_count,
        )

    # Apply item-level services (e.g. design)
    for qis in item.services.select_related("service_rate").filter(is_selected=True):
        price = _get_service_price(qis.service_rate, qis.price_override, None)
        if price is not None:
            total += price

    unit_price = total / quantity
    line_total = total
    return unit_price, line_total


def _build_item_breakdown_lines(item: QuoteItem) -> list[dict]:
    """
    Build breakdown lines for quote items using the summary layer.
    Reuses build_quote_item_summary; no recalculated imposition or costing.
    """
    from quotes.summary import build_quote_item_summary, summary_to_breakdown_lines

    summary = build_quote_item_summary(item)
    lines = summary_to_breakdown_lines(summary)

    # Add printing detail (Color Single/Double) when available
    if item.machine_id and item.machine and item.paper_id and item.sides and item.color_mode:
        from pricing.models import PrintingRate
        rate, _ = PrintingRate.resolve(
            item.machine, item.paper.sheet_size, item.color_mode, item.sides, paper=item.paper
        )
        if rate:
            side_label = "Double" if item.sides == Sides.DUPLEX else "Single"
            # Replace generic "Print" with detailed label
            for i, ln in enumerate(lines):
                if ln.get("label") == "Print":
                    lines[i] = {
                        "label": f"Printing: {rate.get_color_mode_display()} {side_label}",
                        "amount": ln.get("amount", ""),
                    }
                    break

    # Add services (not in summary)
    for qis in item.services.select_related("service_rate").filter(is_selected=True):
        price = _get_service_price(qis.service_rate, qis.price_override, None)
        if price is not None:
            lines.append({"label": f"Service: {qis.service_rate.name}", "amount": f"{float(price):,.0f}"})

    return lines


def _missing_fields_for_item(item: QuoteItem) -> list[str]:
    """Convert get_quote_item_missing_fields to flat field names for API."""
    raw = get_quote_item_missing_fields(item)
    field_map = {
        ("QuoteItem", "product"): "product",
        ("QuoteItem", "quantity"): "quantity",
        ("QuoteItem", "paper"): "paper",
        ("QuoteItem", "machine"): "machine",
        ("QuoteItem", "sides"): "sides",
        ("QuoteItem", "color_mode"): "color_mode",
        ("QuoteItem", "material"): "material",
        ("QuoteItem", "chosen_width_mm"): "dimensions",
        ("QuoteItem", "chosen_height_mm"): "dimensions",
        ("QuoteItem", "title or spec_text"): "title",
        ("PrintingRate", "single_price / double_price"): "printing_rate",
        ("PrintingRate", "single_price / double_price"): "printing_rate",
        ("Paper", "selling_price"): "paper",
        ("Material", "selling_price"): "material",
    }
    seen = set()
    out = []
    for model_label, field_name in raw:
        key = (model_label, field_name)
        if "PrintingRate" in model_label or "printing" in field_name.lower():
            name = "printing_rate"
        elif "Product" in model_label and "default_finished" in field_name:
            name = "dimensions"
        elif "dimensions" in field_name.lower() or "chosen_width" in field_name or "chosen_height" in field_name:
            name = "dimensions"
        elif "paper" in field_name.lower():
            name = "paper"
        elif "material" in field_name.lower():
            name = "material"
        elif "machine" in field_name.lower():
            name = "machine"
        elif "product" in field_name.lower():
            name = "product"
        elif "quantity" in field_name.lower():
            name = "quantity"
        elif "sides" in field_name.lower():
            name = "sides"
        elif "color" in field_name.lower():
            name = "color_mode"
        else:
            name = field_name.replace(" ", "_")
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def build_preview_price_response(quote_request: QuoteRequest) -> dict:
    """
    Build standardized preview price response with PricingDiagnostics.
    Always includes: can_calculate, total, lines, needs_review_items, missing_fields, reason,
    suggestions, item_diagnostics.
    """
    currency = getattr(quote_request.shop, "currency", "KES") or "KES"
    shop_id = quote_request.shop_id
    total = Decimal("0")
    lines = []
    needs_review_items = []
    missing_fields_set = set()
    items_missing_fields = {}
    item_diagnostics = {}
    has_negotiable = False
    item_explanations = {}
    item_calculations = {}

    for item in quote_request.items.prefetch_related(
        "paper", "material", "machine", "product", "finishings__finishing_rate", "services__service_rate"
    ):
        missing = get_quote_item_missing_fields(item)
        if missing:
            needs_review_items.append(item.id)
            item_missing = _missing_fields_for_item(item)
            missing_fields_set.update(item_missing)
            items_missing_fields[str(item.id)] = item_missing
            diag = build_item_diagnostics(item, missing, item_missing, shop_id)
            item_diagnostics[str(item.id)] = diag
            item_label = (
                item.product.name if item.item_type == "PRODUCT" and item.product_id
                else item.title or "Custom item"
            )
            lines.append({"label": f"{item_label}: Needs review ({missing[0][1]})", "amount": ""})
            continue

        unit_price, line_total = calculate_quote_item(item, force=False)
        from quotes.pricing_service import compute_quote_item_pricing

        pricing = compute_quote_item_pricing(item)
        item_explanations[str(item.id)] = pricing.explanations
        item_calculations[str(item.id)] = pricing.calculation_description
        if line_total and line_total > 0:
            total += line_total
            item_label = (
                item.product.name if item.item_type == "PRODUCT" and item.product_id
                else item.title or "Custom item"
            )
            breakdown = _build_item_breakdown_lines(item)
            if breakdown:
                lines.append({"label": item_label, "amount": ""})
                lines.extend(breakdown)
                lines.append({"label": "Total", "amount": f"{line_total:,.0f}"})
            else:
                lines.append({"label": item_label, "amount": f"{line_total:,.0f}"})

        for qis in item.services.select_related("service_rate").filter(is_selected=True):
            if qis.price_override is None and getattr(qis.service_rate, "pricing_type", None) != "FIXED":
                has_negotiable = True

    can_calculate = len(needs_review_items) == 0
    reason = (
        f"{len(needs_review_items)} item(s) need more details to calculate."
        if needs_review_items
        else ""
    )

    diagnostics = build_pricing_diagnostics(
        can_calculate=can_calculate,
        reason=reason,
        missing_fields=sorted(missing_fields_set),
        needs_review_items=needs_review_items,
        item_diagnostics=item_diagnostics,
    )

    return {
        "currency": currency,
        "total": float(total),
        "lines": lines,
        "item_explanations": item_explanations,
        "item_calculations": item_calculations,
        **diagnostics,
        "hasNegotiable": has_negotiable,
        "items_missing_fields": items_missing_fields,
    }


def calculate_quote_request(quote_request: QuoteRequest, lock: bool = False) -> Decimal:
    """
    Calculate all quote items and optionally lock prices.
    - lock=False: recalculate unit_price/line_total in memory only (no persist).
      Skips items with pricing_locked_at unless explicitly forced (not exposed here).
    - lock=True: persist unit_price, line_total, pricing_locked_at; update quote totals.
    """
    from decimal import Decimal

    grand_total = Decimal("0")

    for item in quote_request.items.prefetch_related(
        "paper", "material", "machine", "finishings__finishing_rate", "services__service_rate"
    ):
        force = lock  # When locking, always recalc (seller explicitly pricing)
        unit_price, line_total = calculate_quote_item(item, force=force)

        if lock:
            item.unit_price = unit_price
            item.line_total = line_total
            item.pricing_locked_at = timezone.now()
            item.save()

        grand_total += line_total

    # Add quote-level services (e.g. delivery)
    for qrs in quote_request.services.select_related("service_rate").filter(is_selected=True):
        price = _get_service_price(
            qrs.service_rate, qrs.price_override, qrs.distance_km
        )
        if price is not None:
            grand_total += price

    if lock:
        quote_request.status = QuoteStatus.QUOTED
        quote_request.save(update_fields=["status", "updated_at"])
    return grand_total
