"""
Demo calculator service layer — computes pricing from demo models.
Handles SHEET mode (business cards, flyers, booklets) and LARGE_FORMAT mode.
"""
from decimal import Decimal
from math import ceil

from .models import (
    DemoProduct,
    DemoPaper,
    DemoPrintingRate,
    DemoFinishingRate,
    DemoMaterial,
)


def compute_demo_quote(
    product: DemoProduct,
    quantity: int,
    *,
    sheet_size: str | None = None,
    gsm: int | None = None,
) -> dict:
    """
    Compute a demo quote for a product at given quantity.

    Returns:
        dict with keys: material, printing, finishing, total
    """
    if product.pricing_mode == "LARGE_FORMAT":
        return _compute_large_format(product, quantity)
    return _compute_sheet(product, quantity, sheet_size=sheet_size, gsm=gsm)


def _compute_sheet(
    product: DemoProduct,
    quantity: int,
    *,
    sheet_size: str | None = None,
    gsm: int | None = None,
) -> dict:
    """Sheet mode: sheets = ceil(qty / copies_per_sheet), cost = paper + printing + finishing."""
    copies_per_sheet = max(1, product.copies_per_sheet)
    sheets_needed = ceil(quantity / copies_per_sheet)

    sheet_size = sheet_size or product.default_sheet_size or "SRA3"
    target_gsm = gsm or product.min_gsm or 150

    # Printing
    print_rate = DemoPrintingRate.objects.filter(
        sheet_size=sheet_size,
        color_mode="COLOR",
        is_active=True,
    ).first()
    is_duplex = product.default_sides == "DUPLEX"
    if print_rate:
        price_per_sheet = (
            print_rate.double_price if is_duplex else print_rate.single_price
        )
        printing = float(sheets_needed * price_per_sheet)
    else:
        printing = 0

    # Paper (material)
    paper = DemoPaper.objects.filter(
        sheet_size=sheet_size,
        gsm__gte=target_gsm,
        is_active=True,
    ).order_by("gsm").first()
    if paper:
        material = float(sheets_needed * paper.selling_price)
    else:
        # Fallback: any paper for this sheet size
        paper = DemoPaper.objects.filter(
            sheet_size=sheet_size,
            is_active=True,
        ).order_by("gsm").first()
        material = float(sheets_needed * paper.selling_price) if paper else 0

    # Finishing
    finishing = _compute_finishing(
        product,
        sheets_needed=sheets_needed,
        pieces=quantity,
    )

    total = material + printing + finishing
    return {
        "material": round(material, 2),
        "printing": round(printing, 2),
        "finishing": round(finishing, 2),
        "total": round(total, 2),
    }


def _compute_large_format(product: DemoProduct, quantity: int) -> dict:
    """Large format: area_sqm = (w/1000) * (h/1000) * qty, cost = material + printing + finishing."""
    width_m = product.default_finished_width_mm / 1000
    height_m = product.default_finished_height_mm / 1000
    area_sqm = width_m * height_m * quantity

    # Material
    mat = DemoMaterial.objects.filter(is_active=True).first()
    if mat:
        material = float(area_sqm * mat.selling_price)
    else:
        material = 0

    # Printing (SQM rate - use a default if no rate)
    sqm_print_rate = 350
    printing = float(area_sqm * sqm_print_rate)

    finishing = _compute_finishing(
        product,
        sheets_needed=quantity,
        pieces=quantity,
    )

    total = material + printing + finishing
    return {
        "material": round(material, 2),
        "printing": round(printing, 2),
        "finishing": round(finishing, 2),
        "total": round(total, 2),
    }


def _compute_finishing(
    product: DemoProduct,
    *,
    sheets_needed: int,
    pieces: int,
) -> float:
    """Compute finishing cost from product's finishing options."""
    total = Decimal("0")
    sides_count = 2 if product.default_sides == "DUPLEX" else 1
    for opt in product.product_finishing_options.select_related("finishing_rate").all():
        rate = opt.finishing_rate
        if not rate.is_active:
            continue
        price = opt.price_adjustment if opt.price_adjustment is not None else rate.price
        if rate.charge_unit == "PER_SHEET":
            total += Decimal(str(sheets_needed)) * price
        elif rate.charge_unit == "PER_SIDE_PER_SHEET":
            total += Decimal(str(sheets_needed)) * Decimal(str(sides_count)) * price
        elif rate.charge_unit == "PER_PIECE":
            total += Decimal(str(pieces)) * price
        elif rate.charge_unit == "PER_SQM":
            total += Decimal(str(pieces)) * price
        elif rate.charge_unit == "FLAT":
            total += price
        if rate.setup_fee:
            total += rate.setup_fee
    return float(total)
