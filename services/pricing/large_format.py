from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from catalog.choices import PricingMode
from quotes.turnaround import estimate_turnaround, humanize_working_hours
from services.engine.integration import build_media_spec_from_material
from services.engine.schemas.inputs import JobSpec
from services.engine.services.roll_layout_imposer import RollLayoutImposer
from services.pricing.engine import _format_money, _resolve_vat_summary
from services.pricing.finishings import compute_finishing_line, compute_finishing_total
from services.pricing.result_contract import build_calculation_result


LARGE_FORMAT_SUBTYPES: dict[str, dict[str, object]] = {
    "banner": {"label": "Banner", "default_turnaround_hours": 24, "allow_rotation": False, "gap_mm": 0},
    "sticker": {"label": "Sticker", "default_turnaround_hours": 12, "allow_rotation": True, "gap_mm": 5},
    "roll_up_banner": {"label": "Roll-up Banner", "default_turnaround_hours": 24, "allow_rotation": False, "gap_mm": 0},
    "poster": {"label": "Poster", "default_turnaround_hours": 18, "allow_rotation": True, "gap_mm": 5},
    "mounted_board": {"label": "Mounted Board", "default_turnaround_hours": 48, "allow_rotation": False, "gap_mm": 0},
}


def _decimal(value, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def _area_per_piece_sqm(width_mm: int, height_mm: int) -> Decimal:
    return (Decimal(width_mm) / Decimal("1000")) * (Decimal(height_mm) / Decimal("1000"))


def _resolve_subtype(product_subtype: str) -> dict[str, object]:
    return LARGE_FORMAT_SUBTYPES.get(product_subtype, LARGE_FORMAT_SUBTYPES["banner"])


def _build_roll_layout(*, product_subtype: str, width_mm: int, height_mm: int, quantity: int, material):
    media = build_media_spec_from_material(material)
    if not media:
        return None, ["No production roll width is configured for this material; pricing uses area only."], []

    subtype = _resolve_subtype(product_subtype)
    job = JobSpec(
        product_type=product_subtype,
        finished_width_mm=float(width_mm),
        finished_height_mm=float(height_mm),
        quantity=quantity,
        gap_mm=float(subtype.get("gap_mm", 0) or 0),
        allow_rotation=bool(subtype.get("allow_rotation", True)),
        roll_overlap_mm=20,
        tile_max_length_mm=2500 if product_subtype in {"banner", "roll_up_banner", "mounted_board"} else 0,
    )
    layout = RollLayoutImposer().impose(job, media)
    warnings: list[str] = []
    assumptions: list[str] = []
    if layout.needs_tiling:
        warnings.append(
            f"This {str(subtype['label']).lower()} exceeds the configured roll width and will be tiled into {layout.tiles_x} x {layout.tiles_y} panels."
        )
    else:
        assumptions.append(f"Roll layout uses {layout.items_across} item(s) across on {layout.media_name}.")
    assumptions.extend(layout.notes or [])
    return asdict(layout), warnings, assumptions


def calculate_large_format_preview(
    *,
    shop,
    product_subtype: str,
    quantity: int,
    width_mm: int,
    height_mm: int,
    material,
    finishing_selections: list[dict] | None = None,
    hardware_finishing_rate=None,
    turnaround_hours: int | None = None,
) -> dict:
    subtype = _resolve_subtype(product_subtype)
    currency = getattr(shop, "currency", "KES") or "KES"
    area_per_piece = _area_per_piece_sqm(width_mm, height_mm)
    total_area = (area_per_piece * Decimal(quantity)).quantize(Decimal("0.0001"))
    material_rate = _decimal(getattr(material, "selling_price", 0))
    print_rate = _decimal(getattr(material, "print_price_per_sqm", 0))
    material_cost = material_rate * total_area
    print_cost = print_rate * total_area

    finishing_total, finishing_lines = compute_finishing_total(
        finishing_selections,
        quantity=quantity,
        good_sheets=0,
        area_sqm=total_area,
    )

    hardware_total = Decimal("0")
    hardware_line = None
    if hardware_finishing_rate:
        hardware_line = compute_finishing_line(
            hardware_finishing_rate,
            quantity=quantity,
            good_sheets=0,
            area_sqm=total_area,
            selected_side="both",
        ).to_dict()
        hardware_total = _decimal(hardware_line["total"])

    subtotal = material_cost + print_cost + finishing_total + hardware_total
    vat_summary = _resolve_vat_summary(shop, subtotal)
    grand_total = vat_summary["grand_total"]
    unit_price = grand_total / Decimal(quantity) if quantity else Decimal("0")

    layout_result, layout_warnings, layout_assumptions = _build_roll_layout(
        product_subtype=product_subtype,
        width_mm=width_mm,
        height_mm=height_mm,
        quantity=quantity,
        material=material,
    )

    resolved_turnaround_hours = turnaround_hours or int(subtype["default_turnaround_hours"])
    turnaround_estimate = estimate_turnaround(shop=shop, working_hours=resolved_turnaround_hours)

    warnings = list(layout_warnings)
    assumptions = [
        f"Subtype pricing uses the {str(subtype['label']).lower()} profile.",
        "Large-format pricing is area-based: material sqm + print sqm + selected charges.",
        *layout_assumptions,
    ]

    explanations = [
        f"Area per piece: {area_per_piece.quantize(Decimal('0.0001'))} sqm.",
        f"Total area: {total_area} sqm for {quantity} piece(s).",
        f"Material: {currency} {_format_money(material_rate)} x {total_area} sqm = {_format_money(material_cost)}.",
        f"Printing: {currency} {_format_money(print_rate)} x {total_area} sqm = {_format_money(print_cost)}.",
    ]
    explanations.extend(line.get("explanation") or "" for line in finishing_lines if line.get("explanation"))
    if hardware_line and hardware_line.get("explanation"):
        explanations.append(str(hardware_line["explanation"]))
    explanations.append(f"VAT: {_format_money(vat_summary['vat_amount'])} ({vat_summary['vat']['mode']}).")

    turnaround_breakdown = {
        "turnaround_hours": resolved_turnaround_hours,
        "turnaround_text": humanize_working_hours(resolved_turnaround_hours),
        "estimated_ready_at": turnaround_estimate.ready_at if turnaround_estimate else None,
        "human_ready_text": turnaround_estimate.human_ready_text if turnaround_estimate else "Ready time on request",
        "turnaround_label": turnaround_estimate.label if turnaround_estimate else "On request",
    }

    line_items = [
        {
            "code": "material",
            "label": "Material",
            "amount": _format_money(material_cost),
            "formula": f"{total_area} sqm x {_format_money(material_rate)}",
            "metadata": {
                "material_id": material.id,
                "material_label": f"{material.material_type} ({material.unit})",
                "unit": material.unit,
            },
        },
        {
            "code": "printing",
            "label": "Printing",
            "amount": _format_money(print_cost),
            "formula": f"{total_area} sqm x {_format_money(print_rate)}",
            "metadata": {
                "print_price_per_sqm": _format_money(print_rate),
            },
        },
    ]
    for index, line in enumerate(finishing_lines):
        line_items.append(
            {
                "code": f"finishing_{index}",
                "label": line.get("name") or "Finishing",
                "amount": line.get("total"),
                "formula": line.get("formula"),
                "metadata": {
                    "billing_basis": line.get("billing_basis"),
                    "selected_side": line.get("selected_side"),
                },
            }
        )
    if hardware_line:
        line_items.append(
            {
                "code": "hardware",
                "label": hardware_line.get("name") or "Hardware",
                "amount": hardware_line.get("total"),
                "formula": hardware_line.get("formula"),
                "metadata": {
                    "billing_basis": hardware_line.get("billing_basis"),
                    "selected_side": hardware_line.get("selected_side"),
                },
            }
        )
    if vat_summary["vat_amount"] not in (None, Decimal("0")):
        line_items.append(
            {
                "code": "vat",
                "label": "VAT",
                "amount": _format_money(vat_summary["vat_amount"]),
                "formula": vat_summary["vat"]["label"],
                "metadata": vat_summary["vat"],
            }
        )

    calculation_result = build_calculation_result(
        quote_type="large_format",
        pricing_mode=PricingMode.LARGE_FORMAT,
        billing_type="per_area",
        size_summary=f"{width_mm}x{height_mm}mm",
        quantity=quantity,
        currency=currency,
        line_items=line_items,
        explanation_blocks=[{"title": "Calculation", "text": text} for text in explanations if text],
        metadata={
            "subtype": {"key": product_subtype, "label": subtype["label"]},
            "material": {
                "id": material.id,
                "label": f"{material.material_type} ({material.unit})",
                "rate_per_sqm": _format_money(material_rate),
                "print_price_per_sqm": _format_money(print_rate),
                "unit": material.unit,
            },
            "dimensions": {
                "width_mm": width_mm,
                "height_mm": height_mm,
                "area_per_piece_sqm": str(area_per_piece.quantize(Decimal("0.0001"))),
                "area_sqm": str(total_area),
            },
            "layout_result": layout_result,
            "turnaround": turnaround_breakdown,
        },
        subtotal=vat_summary["subtotal"],
        finishing_total=finishing_total + hardware_total,
        grand_total=grand_total,
        unit_price=unit_price,
        warnings=warnings,
        assumptions=assumptions,
    )

    return {
        "quote_type": "large_format",
        "pricing_mode": PricingMode.LARGE_FORMAT,
        "quantity": quantity,
        "currency": currency,
        "warnings": warnings,
        "assumptions": assumptions,
        "can_calculate": True,
        "reason": "",
        "totals": {
            "subtotal": _format_money(vat_summary["subtotal"]),
            "material_cost": _format_money(material_cost),
            "print_cost": _format_money(print_cost),
            "finishing_total": _format_money(finishing_total + hardware_total),
            "vat_amount": _format_money(vat_summary["vat_amount"]),
            "vat": _format_money(vat_summary["vat_amount"]),
            "grand_total": _format_money(grand_total),
            "unit_price": _format_money(unit_price),
            "total_per_piece": _format_money(unit_price),
        },
        "breakdown": {
            "subtype": {
                "key": product_subtype,
                "label": subtype["label"],
            },
            "dimensions": {
                "width_mm": width_mm,
                "height_mm": height_mm,
                "area_per_piece_sqm": str(area_per_piece.quantize(Decimal("0.0001"))),
                "area_sqm": str(total_area),
            },
            "material": {
                "id": material.id,
                "label": f"{material.material_type} ({material.unit})",
                "unit": material.unit,
                "rate_per_sqm": _format_money(material_rate),
                "total": _format_money(material_cost),
            },
            "printing": {
                "rate_per_sqm": _format_money(print_rate),
                "total": _format_money(print_cost),
                "formula": f"area_sqm x {_format_money(print_rate)}",
                "explanation": f"{total_area} sqm x {currency} {_format_money(print_rate)}",
            },
            "finishings": finishing_lines,
            "hardware": hardware_line,
            "layout": layout_result,
            "turnaround": turnaround_breakdown,
            "vat": vat_summary["vat"],
        },
        "vat": vat_summary["vat"],
        "explanations": explanations,
        "explanation_lines": explanations,
        "turnaround_hours": resolved_turnaround_hours,
        "estimated_working_hours": turnaround_estimate.working_hours if turnaround_estimate else resolved_turnaround_hours,
        "estimated_ready_at": turnaround_estimate.ready_at if turnaround_estimate else None,
        "human_ready_text": turnaround_estimate.human_ready_text if turnaround_estimate else "Ready time on request",
        "turnaround_label": turnaround_estimate.label if turnaround_estimate else "On request",
        "turnaround_text": humanize_working_hours(resolved_turnaround_hours),
        "calculation_result": calculation_result,
        "roll_width_mm": layout_result.get("printable_width_mm") if layout_result else None,
        "roll_length_mm": layout_result.get("roll_length_mm") if layout_result else None,
        "tiles_x": layout_result.get("tiles_x") if layout_result else None,
        "tiles_y": layout_result.get("tiles_y") if layout_result else None,
        "total_tiles": layout_result.get("total_tiles") if layout_result else None,
    }
