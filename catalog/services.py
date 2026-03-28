"""
Product price range calculation.
Returns "From KES X" / "Up to KES Y" or structured missing_fields when data is incomplete.
Uses explicit schema: Product fields, Paper, Machine, PrintingRate, Material.
No fuzzy introspection; validation via catalog.validation.
"""
from decimal import Decimal
from math import ceil

from django.db.models import Q

from catalog.choices import PricingMode
from catalog.models import Product
from catalog.validation import validate_product_configuration
from inventory.choices import MachineType, SheetSize, SHEET_SIZE_DIMENSIONS
from inventory.models import Machine, Paper
from pricing.choices import ColorMode, Sides
from pricing.models import Material, PrintingRate
from quotes.diagnostics import build_product_diagnostics
from services.pricing.engine import (
    calculate_large_format_pricing,
    calculate_sheet_pricing,
    select_paper_for_pricing,
)


# Lay-person friendly pricing mode labels and explanations
PRICING_MODE_LABELS = {
    PricingMode.SHEET: "Sheet",
    PricingMode.LARGE_FORMAT: "Large format",
}
PRICING_MODE_EXPLANATIONS = {
    PricingMode.SHEET: "Charged per sheet. Price depends on paper type, single/double-sided printing, and quantity.",
    PricingMode.LARGE_FORMAT: "Charged by area (per sqm). Price depends on material (vinyl, banner, etc.) and dimensions.",
}


def _format_price_display(min_val, max_val, can_calculate) -> str:
    """Human-readable price string for cards."""
    if not can_calculate:
        return "Price on request"
    if min_val is None:
        return "Price on request"
    min_f = float(min_val)
    max_f = float(max_val) if max_val is not None else None
    if max_f is None or abs(max_f - min_f) < 0.01:
        return f"From KES {min_f:,.0f}"
    return f"KES {min_f:,.0f} – {max_f:,.0f}"


def _infer_unit_label(product_name: str) -> str:
    """Infer per-item label from product name (e.g. 'Business Cards' -> 'per card')."""
    if not product_name:
        return "per item"
    name_lower = product_name.lower()
    if "card" in name_lower:
        return "per card"
    if "flyer" in name_lower or "flyers" in name_lower:
        return "per flyer"
    if "poster" in name_lower or "posters" in name_lower:
        return "per poster"
    if "brochure" in name_lower or "brochures" in name_lower:
        return "per brochure"
    if "sticker" in name_lower or "stickers" in name_lower:
        return "per sticker"
    return "per item"


def _get_valid_sheet_papers(product: Product, shop) -> list:
    """Return papers that pass product validation (gsm, allowed_sheet_sizes)."""
    papers_qs = Paper.objects.filter(
        shop=shop,
        is_active=True,
        selling_price__gt=0,
    )
    if product.min_gsm is not None:
        papers_qs = papers_qs.filter(gsm__gte=product.min_gsm)
    if product.max_gsm is not None:
        papers_qs = papers_qs.filter(gsm__lte=product.max_gsm)
    papers = list(papers_qs)
    allowed = product.allowed_sheet_sizes
    if allowed is not None and len(allowed) > 0:
        papers = [p for p in papers if p.sheet_size in allowed]
    return [p for p in papers if validate_product_configuration(product, paper=p)["is_valid"]]


def select_paper_for_imposition(
    product: Product,
    shop,
    machine=None,
    sides=None,
    color_mode=None,
) -> "Paper | None":
    """
    Select paper for imposition/pricing when none is specified.
    Order: 1) default paper (is_default=True), 2) most economical, 3) only available.
    Most economical = lowest total cost for min_quantity (paper + printing per sheet × sheets_needed).
    """
    valid_papers = _get_valid_sheet_papers(product, shop)
    return select_paper_for_pricing(
        product=product,
        shop=shop,
        valid_papers=valid_papers,
        machine=machine,
        sides=sides,
        color_mode=color_mode,
        quantity=(product.min_quantity or 1) if product else 1,
    )


def get_product_starting_price(product: Product) -> dict:
    """
    Compute a real starting price from valid defaults.
    No silent zero fallbacks; returns clear validation errors when data is missing.

    Returns:
        {
            "price": Decimal | None,
            "is_valid": bool,
            "errors": list[str],
            "warnings": list[str],
            "assumptions": dict,
        }
    """
    errors: list[str] = []
    warnings: list[str] = []
    assumptions: dict = {}

    if product.pricing_mode not in (PricingMode.SHEET, PricingMode.LARGE_FORMAT):
        return {
            "price": None,
            "is_valid": False,
            "errors": [f"Unknown pricing mode: {product.pricing_mode}"],
            "warnings": [],
            "assumptions": {},
        }

    shop = product.shop
    min_qty = product.min_quantity or 1

    if product.pricing_mode == PricingMode.SHEET:
        if not product.default_finished_width_mm or not product.default_finished_height_mm:
            errors.append("Product requires default_finished_width_mm and default_finished_height_mm for pricing.")
            return {"price": None, "is_valid": False, "errors": errors, "warnings": warnings, "assumptions": {}}

        v = validate_product_configuration(
            product,
            width_mm=product.default_finished_width_mm,
            height_mm=product.default_finished_height_mm,
        )
        if not v["is_valid"]:
            errors.extend(v["errors"])
            return {"price": None, "is_valid": False, "errors": errors, "warnings": warnings, "assumptions": {}}

        valid_papers = _get_valid_sheet_papers(product, shop)
        if not valid_papers:
            errors.append("No paper matches product rules (gsm range, allowed sheet sizes). Add paper or adjust product rules.")
            return {"price": None, "is_valid": False, "errors": errors, "warnings": warnings, "assumptions": {}}

        # Prefer machine that has a default printing rate (for consistent pricing)
        machine = (
            Machine.objects.filter(shop=shop, is_active=True)
            .filter(printing_rates__is_default=True, printing_rates__is_active=True)
            .distinct()
            .first()
        ) or Machine.objects.filter(shop=shop, is_active=True).first()
        if not machine:
            errors.append("No active machine for shop.")
            return {"price": None, "is_valid": False, "errors": errors, "warnings": warnings, "assumptions": {}}

        paper = select_paper_for_imposition(product, shop, machine=machine)
        sides = product.default_sides or Sides.SIMPLEX
        if sides == Sides.DUPLEX and not product.allow_duplex:
            sides = Sides.SIMPLEX
        if sides == Sides.SIMPLEX and not product.allow_simplex:
            sides = Sides.DUPLEX if product.allow_duplex else Sides.SIMPLEX

        rate, print_price = PrintingRate.resolve(machine, paper.sheet_size, ColorMode.COLOR, sides)
        if not rate or print_price is None:
            errors.append(f"No printing rate for {machine.name} / {paper.sheet_size} / color.")
            return {"price": None, "is_valid": False, "errors": errors, "warnings": warnings, "assumptions": {}}

        pricing = calculate_sheet_pricing(
            product=product,
            quantity=min_qty,
            paper=paper,
            machine=machine,
            color_mode=ColorMode.COLOR,
            sides=sides,
        ).to_dict()
        total = Decimal(pricing["totals"]["grand_total"])

        assumptions = {
            "quantity": min_qty,
            "sheet_size": paper.sheet_size,
            "paper_label": f"{paper.sheet_size} {paper.gsm}gsm",
            "paper_id": paper.id,
            "machine_id": machine.id,
            "sides": sides,
            "sheets_used": pricing["breakdown"]["imposition"]["good_sheets"],
            "copies_per_sheet": pricing["breakdown"]["imposition"]["copies_per_sheet"],
        }
        return {
            "price": total,
            "is_valid": True,
            "errors": [],
            "warnings": warnings,
            "assumptions": assumptions,
        }

    # LARGE_FORMAT
    w_mm = product.min_width_mm or product.default_finished_width_mm
    h_mm = product.min_height_mm or product.default_finished_height_mm
    if not w_mm or not h_mm:
        errors.append("Product requires dimensions (min_width_mm/min_height_mm or default_finished) for LARGE_FORMAT.")
        return {"price": None, "is_valid": False, "errors": errors, "warnings": warnings, "assumptions": {}}

    v = validate_product_configuration(product, width_mm=w_mm, height_mm=h_mm)
    if not v["is_valid"]:
        errors.extend(v["errors"])
        return {"price": None, "is_valid": False, "errors": errors, "warnings": warnings, "assumptions": {}}

    materials = list(Material.objects.filter(shop=shop, is_active=True, selling_price__gt=0))
    if not materials:
        errors.append("No active material with selling_price for LARGE_FORMAT.")
        return {"price": None, "is_valid": False, "errors": errors, "warnings": warnings, "assumptions": {}}

    mat = materials[0]
    pricing = calculate_large_format_pricing(
        product=product,
        quantity=min_qty,
        material=mat,
        width_mm=w_mm,
        height_mm=h_mm,
    ).to_dict()
    total = Decimal(pricing["totals"]["grand_total"])

    assumptions = {
        "quantity": min_qty,
        "width_mm": w_mm,
        "height_mm": h_mm,
        "area_sqm": float(Decimal(pricing["breakdown"]["dimensions"]["area_sqm"])),
        "material_id": mat.id,
        "material_type": mat.material_type,
    }
    return {
        "price": total,
        "is_valid": True,
        "errors": [],
        "warnings": warnings,
        "assumptions": assumptions,
    }


def product_price_hint(product: Product) -> dict:
    """
    Compute price hint for product list display.
    Returns a clean structure: price_display (human-readable), pricing_mode_label, pricing_mode_explanation,
    total_low/high, per_unit_low/high, unit_label, and only non-empty diagnostic fields when can_calculate is False.
    """
    result = get_product_price_range(product)
    min_val = result["lowest_price"]
    max_val = result["highest_price"]
    missing = result["missing_fields"]
    can_calculate = result["can_calculate"]
    diag = build_product_diagnostics(product, missing)
    min_qty = product.min_quantity or 1

    out = {
        "can_calculate": can_calculate,
        "min_price": float(min_val) if min_val is not None else None,
        "max_price": float(max_val) if max_val is not None else None,
        "price_display": _format_price_display(min_val, max_val, can_calculate),
        "pricing_mode_label": PRICING_MODE_LABELS.get(product.pricing_mode, product.pricing_mode or "—"),
        "pricing_mode_explanation": PRICING_MODE_EXPLANATIONS.get(
            product.pricing_mode,
            "Price depends on your choices (paper, quantity, finishing).",
        ),
        "quantity_used": min_qty,
        "total_low": float(min_val) if min_val is not None else None,
        "total_high": float(max_val) if max_val is not None else None,
        "per_unit_low": round(float(min_val) / min_qty, 2) if min_val is not None and min_qty else None,
        "per_unit_high": round(float(max_val) / min_qty, 2) if max_val is not None and min_qty else None,
        "unit_label": _infer_unit_label(product.name or ""),
    }
    # Only include diagnostic fields when we cannot calculate
    if not can_calculate:
        out["reason"] = diag["reason"] or "More details needed to calculate price."
        if missing:
            out["missing_fields"] = missing
        if diag["suggestions"]:
            out["suggestions"] = diag["suggestions"]
    return out


def compute_product_price_range_est(product: Product) -> dict:
    """
    Compute price range estimate for SHEET products.
    Lowest = paper that minimizes unit_price_est; Highest = paper that maximizes it.
    Returns structure for price_range_est serializer field.
    """
    shop = product.shop
    min_qty = product.min_quantity or 1
    color_mode = ColorMode.COLOR
    sides = product.default_sides or Sides.SIMPLEX
    sheets_used = 1  # Start with 1 for range display unless dimensions available

    if product.pricing_mode != PricingMode.SHEET:
        return {
            "can_calculate": False,
            "price_display": "Price on request",
            "pricing_mode_label": PRICING_MODE_LABELS.get(product.pricing_mode, str(product.pricing_mode or "—")),
            "pricing_mode_explanation": PRICING_MODE_EXPLANATIONS.get(
                product.pricing_mode,
                "Price depends on your choices (paper, quantity, finishing).",
            ),
            "lowest": _empty_range_payload(),
            "highest": _empty_range_payload(),
            "reason": "Price range applies to sheet products only.",
            "suggestions": [
                {"code": "SET_PRICING_MODE", "message": "Price range est. applies to Sheet products only."},
            ],
        }

    missing = []
    suggestions = []

    # Validate default dimensions against product rules
    if product.default_finished_width_mm and product.default_finished_height_mm:
        v = validate_product_configuration(
            product,
            width_mm=product.default_finished_width_mm,
            height_mm=product.default_finished_height_mm,
        )
        if not v["is_valid"]:
            missing.append("product_rules")
            suggestions.extend([{"code": "PRODUCT_RULES", "message": e} for e in v["errors"]])
            return {
                "can_calculate": False,
                "price_display": "Price on request",
                "pricing_mode_label": PRICING_MODE_LABELS.get(product.pricing_mode, str(product.pricing_mode or "—")),
                "pricing_mode_explanation": PRICING_MODE_EXPLANATIONS.get(
                    product.pricing_mode,
                    "Price depends on your choices (paper, quantity, finishing).",
                ),
                "lowest": _empty_range_payload(),
                "highest": _empty_range_payload(),
                "reason": "; ".join(v["errors"]),
                "missing_fields": missing,
                "suggestions": suggestions,
            }

    # Use only valid papers (pass product rules)
    eligible_papers = _get_valid_sheet_papers(product, shop)
    sheet_size = product.default_sheet_size or SheetSize.SRA3
    if not eligible_papers:
        missing.append("paper")
        suggestions.append({
            "code": "ADD_PAPER",
            "message": "Add paper that matches product rules (gsm range, allowed sheet sizes).",
        })
    else:
        valid_sheet_sizes = [p.sheet_size for p in eligible_papers]
        if product.default_sheet_size and product.default_sheet_size in valid_sheet_sizes:
            sheet_size = product.default_sheet_size
        else:
            # Prefer default printing rate's sheet_size when product has no default
            default_rate = PrintingRate.objects.filter(
                machine__shop=shop,
                machine__is_active=True,
                is_default=True,
                is_active=True,
            ).first()
            default_sheet = default_rate.sheet_size if default_rate else None
            sheet_size = (
                default_sheet if default_sheet and default_sheet in valid_sheet_sizes
                else eligible_papers[0].sheet_size
            )
        eligible_papers = [p for p in eligible_papers if p.sheet_size == sheet_size]

    # Machine: prefer one with default rate for sheet_size, then first that fits
    sheet_dims = SHEET_SIZE_DIMENSIONS.get(sheet_size, (0, 0))
    sw, sh = sheet_dims
    sheet_fits = (Q(max_width_mm__gte=sw) & Q(max_height_mm__gte=sh)) | (Q(max_width_mm__gte=sh) & Q(max_height_mm__gte=sw))
    machine = (
        Machine.objects.filter(shop=shop, is_active=True)
        .filter(sheet_fits, printing_rates__is_default=True, printing_rates__sheet_size=sheet_size, printing_rates__is_active=True)
        .distinct()
        .first()
    )
    if not machine:
        machine = Machine.objects.filter(shop=shop, is_active=True).filter(sheet_fits).first()
    if not machine:
        machine = Machine.objects.filter(shop=shop, is_active=True).first()
    if not machine:
        missing.append("machine")
        suggestions.append({
            "code": "ADD_MACHINE",
            "message": "Add a machine under Shop → Machines.",
        })

    # Printing rate
    rate, print_price = None, None
    if machine:
        rate, print_price = PrintingRate.resolve(machine, sheet_size, color_mode, sides)
    if not rate or print_price is None:
        missing.append("printing_rate")
        machine_name = machine.name if machine else "machine"
        suggestions.append({
            "code": "ADD_PRINTING_RATE",
            "message": f"Set {machine_name} single/double printing rates under Machine → Printing Rates.",
        })

    if missing:
        return {
            "can_calculate": False,
            "price_display": "Price on request",
            "pricing_mode_label": PRICING_MODE_LABELS.get(product.pricing_mode, str(product.pricing_mode or "—")),
            "pricing_mode_explanation": PRICING_MODE_EXPLANATIONS.get(
                product.pricing_mode,
                "Price depends on your choices (paper, quantity, finishing).",
            ),
            "lowest": _empty_range_payload(),
            "highest": _empty_range_payload(),
            "reason": "Shop needs to add paper, machine, or printing rates to show prices.",
            "missing_fields": missing,
            "suggestions": suggestions,
        }

    # Compute total per paper through the central pricing engine.
    paper_costs = []
    for paper in eligible_papers:
        pricing = calculate_sheet_pricing(
            product=product,
            quantity=min_qty,
            paper=paper,
            machine=machine,
            color_mode=color_mode,
            sides=sides,
        ).to_dict()
        total_est = float(pricing["totals"]["grand_total"])
        unit_price_est = float(pricing["totals"]["unit_price"])
        paper_label = f"{paper.sheet_size} {paper.gsm}gsm {paper.get_paper_type_display()}"
        paper_costs.append({
            "paper": paper,
            "unit_price_est": unit_price_est,
            "total_est": total_est,
            "sheets": pricing["breakdown"]["imposition"]["good_sheets"],
            "paper_label": paper_label,
        })

    if not paper_costs:
        return {
            "can_calculate": False,
            "price_display": "Price on request",
            "pricing_mode_label": PRICING_MODE_LABELS.get(product.pricing_mode, str(product.pricing_mode or "—")),
            "pricing_mode_explanation": PRICING_MODE_EXPLANATIONS.get(
                product.pricing_mode,
                "Price depends on your choices (paper, quantity, finishing).",
            ),
            "lowest": _empty_range_payload(),
            "highest": _empty_range_payload(),
            "reason": "No valid paper dimensions for pricing.",
            "missing_fields": missing,
            "suggestions": suggestions,
        }

    paper_costs.sort(key=lambda x: x["total_est"])
    sheets_used = paper_costs[0]["sheets"]
    assumptions = {
        "quantity_used": min_qty,
        "sheet_size": sheet_size,
        "color_mode": color_mode,
        "sides": sides,
        "sheets_used": sheets_used,
    }
    lowest_data = paper_costs[0]
    highest_data = paper_costs[-1]

    currency = getattr(shop, "currency", "KES") or "KES"

    def build_payload(data, prefix):
        p = data["paper"]
        u = data["unit_price_est"]
        t = data["total_est"]
        return {
            "total": t,
            "unit_price": u,
            "paper_id": p.id,
            "paper_label": data["paper_label"],
            "printing_rate_id": rate.id if rate else None,
            "assumptions": assumptions,
            "summary": f"{prefix} based on {data['paper_label']} (KES {p.selling_price:,.0f}) + {machine.name} {'double' if sides == Sides.DUPLEX else 'single'} (KES {print_price:,.0f}) = KES {u:,.0f} per sheet.",
        }

    low_payload = build_payload(lowest_data, "Lowest")
    high_payload = build_payload(highest_data, "Highest")
    low_total = low_payload["total"]
    high_total = high_payload["total"]
    price_display = _format_price_display(low_total, high_total, True) if (low_total and high_total) else "Price on request"

    # Per-unit (per item) for display
    per_unit_low = float(low_total) / min_qty if low_total and min_qty else None
    per_unit_high = float(high_total) / min_qty if high_total and min_qty else None
    unit_label = _infer_unit_label(product.name or "")

    return {
        "can_calculate": True,
        "price_display": price_display,
        "pricing_mode_label": PRICING_MODE_LABELS.get(PricingMode.SHEET, "Sheet"),
        "pricing_mode_explanation": PRICING_MODE_EXPLANATIONS.get(
            PricingMode.SHEET,
            "Charged per sheet. Price depends on paper type, single/double-sided printing, and quantity.",
        ),
        "quantity_used": min_qty,
        "total_low": float(low_total) if low_total else None,
        "total_high": float(high_total) if high_total else None,
        "per_unit_low": round(per_unit_low, 2) if per_unit_low is not None else None,
        "per_unit_high": round(per_unit_high, 2) if per_unit_high is not None else None,
        "unit_label": unit_label,
        "lowest": low_payload,
        "highest": high_payload,
    }


def _empty_range_payload():
    return {
        "total": None,
        "unit_price": None,
        "paper_id": None,
        "paper_label": None,
        "printing_rate_id": None,
        "assumptions": {},
        "summary": None,
    }


def get_product_price_range(product: Product) -> dict:
    """
    Compute price range for a product using only valid pricing combinations.
    Validates product configuration; no silent zero fallbacks.

    Returns:
        {
            "lowest_price": Decimal | None,
            "highest_price": Decimal | None,
            "can_calculate": bool,
            "missing_fields": list[str],
        }
    """
    missing: list[str] = []
    shop = product.shop
    min_qty = product.min_quantity or 1

    if product.pricing_mode == PricingMode.SHEET:
        if not product.default_finished_width_mm or not product.default_finished_height_mm:
            missing.append("dimensions")
            return {
                "lowest_price": None,
                "highest_price": None,
                "can_calculate": False,
                "missing_fields": missing,
            }

        v = validate_product_configuration(
            product,
            width_mm=product.default_finished_width_mm,
            height_mm=product.default_finished_height_mm,
        )
        if not v["is_valid"]:
            missing.append("product_rules")
            return {
                "lowest_price": None,
                "highest_price": None,
                "can_calculate": False,
                "missing_fields": missing,
            }

        valid_papers = _get_valid_sheet_papers(product, shop)
        if not valid_papers:
            missing.append("paper")
            return {
                "lowest_price": None,
                "highest_price": None,
                "can_calculate": False,
                "missing_fields": missing,
            }

        machines = list(Machine.objects.filter(shop=shop, is_active=True))
        if not machines:
            missing.append("machine")
            return {
                "lowest_price": None,
                "highest_price": None,
                "can_calculate": False,
                "missing_fields": missing,
            }

        low_total = Decimal("999999")
        high_total = Decimal("0")
        has_valid_combination = False

        for paper in valid_papers:
            w_mm, h_mm = paper.get_dimensions_mm()
            if not w_mm or not h_mm:
                continue
            cps = product.get_copies_per_sheet(paper.sheet_size, w_mm, h_mm)
            if cps <= 0:
                continue
            sheets = ceil(min_qty / cps)

            sides_options = []
            if product.allow_simplex:
                sides_options.append(Sides.SIMPLEX)
            if product.allow_duplex:
                sides_options.append(Sides.DUPLEX)
            if not sides_options:
                sides_options = [product.default_sides or Sides.SIMPLEX]

            for machine in machines:
                for color in [ColorMode.BW, ColorMode.COLOR]:
                    for sides in sides_options:
                        rate, price = PrintingRate.resolve(
                            machine, paper.sheet_size, color, sides
                        )
                        if rate and price is not None:
                            pricing = calculate_sheet_pricing(
                                product=product,
                                quantity=min_qty,
                                paper=paper,
                                machine=machine,
                                color_mode=color,
                                sides=sides,
                            ).to_dict()
                            total = Decimal(pricing["totals"]["grand_total"])
                            low_total = min(low_total, total)
                            high_total = max(high_total, total)
                            has_valid_combination = True

        if not has_valid_combination:
            missing.append("printing_rate")
            return {
                "lowest_price": None,
                "highest_price": None,
                "can_calculate": False,
                "missing_fields": missing,
            }

        return {
            "lowest_price": low_total,
            "highest_price": high_total,
            "can_calculate": True,
            "missing_fields": [],
        }

    if product.pricing_mode == PricingMode.LARGE_FORMAT:
        w_mm = product.min_width_mm or product.default_finished_width_mm
        h_mm = product.min_height_mm or product.default_finished_height_mm
        if not w_mm or not h_mm:
            missing.append("dimensions")
            return {
                "lowest_price": None,
                "highest_price": None,
                "can_calculate": False,
                "missing_fields": missing,
            }

        v = validate_product_configuration(product, width_mm=w_mm, height_mm=h_mm)
        if not v["is_valid"]:
            missing.append("product_rules")
            return {
                "lowest_price": None,
                "highest_price": None,
                "can_calculate": False,
                "missing_fields": missing,
            }

        materials = list(Material.objects.filter(shop=shop, is_active=True, selling_price__gt=0))
        if not materials:
            missing.append("material")
            return {
                "lowest_price": None,
                "highest_price": None,
                "can_calculate": False,
                "missing_fields": missing,
            }

        totals = [
            Decimal(
                calculate_large_format_pricing(
                    product=product,
                    quantity=min_qty,
                    material=m,
                    width_mm=w_mm,
                    height_mm=h_mm,
                ).to_dict()["totals"]["grand_total"]
            )
            for m in materials
        ]
        low_total = min(totals)
        high_total = max(totals)

        return {
            "lowest_price": low_total,
            "highest_price": high_total,
            "can_calculate": True,
            "missing_fields": [],
        }

    return {
        "lowest_price": None,
        "highest_price": None,
        "can_calculate": False,
        "missing_fields": ["pricing_mode"],
    }


def update_product_price_range(product: Product) -> None:
    """Update product.lowest_price and highest_price from calculation."""
    result = get_product_price_range(product)
    if result["can_calculate"]:
        product.lowest_price = result["lowest_price"]
        product.highest_price = result["highest_price"]
        product.save(update_fields=["lowest_price", "highest_price", "updated_at"])
