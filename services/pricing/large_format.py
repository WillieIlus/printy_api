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


def _sqm(mm2: Decimal | float) -> Decimal:
    return (Decimal(str(mm2)) / Decimal("1000000")).quantize(Decimal("0.0001"))


def _roll_area_sqm(width_mm: float, length_mm: float) -> Decimal:
    return _sqm(Decimal(str(width_mm)) * Decimal(str(length_mm)))


def _pricing_method_for_material(material) -> str:
    unit = (getattr(material, "unit", "") or "").strip().upper()
    if unit in {"LM", "LINEAR_METER", "LINEAR_METRE"}:
        return "per_linear_meter"
    return "per_square_meter"


def _build_panel_sizes(total_size_mm: float, max_tile_size_mm: float, overlap_mm: float) -> list[float]:
    if total_size_mm <= 0 or max_tile_size_mm <= 0:
        return []
    if total_size_mm <= max_tile_size_mm:
        return [float(total_size_mm)]

    panels: list[float] = []
    remaining = float(total_size_mm)
    while remaining > max_tile_size_mm:
        panels.append(float(max_tile_size_mm))
        remaining -= max_tile_size_mm - overlap_mm
    panels.append(float(remaining))
    return panels


def _build_tiling_payload(layout_result: dict | None, width_mm: int, height_mm: int) -> tuple[dict, Decimal]:
    if not layout_result:
        return {"required": False, "panels": []}, Decimal("0")

    overlap_mm = float(layout_result.get("overlap_mm") or 0)
    tile_width_limit = float(layout_result.get("printable_width_mm") or width_mm)
    tile_height_limit = float(layout_result.get("tile_height_mm") or height_mm)
    tiles_x = int(layout_result.get("tiles_x") or 1)
    tiles_y = int(layout_result.get("tiles_y") or 1)

    panel_widths = _build_panel_sizes(float(width_mm), tile_width_limit, overlap_mm if tiles_x > 1 else 0)
    panel_heights = _build_panel_sizes(float(height_mm), tile_height_limit, overlap_mm if tiles_y > 1 else 0)
    panels = [
        {
            "width_m": round(panel_width / 1000, 3),
            "height_m": round(panel_height / 1000, 3),
        }
        for panel_width in panel_widths
        for panel_height in panel_heights
    ]

    tiled_panel_area_mm2 = sum(
        Decimal(str(panel["width_m"])) * Decimal("1000") * Decimal(str(panel["height_m"])) * Decimal("1000")
        for panel in panels
    )
    artwork_area_mm2 = Decimal(width_mm) * Decimal(height_mm)
    overlap_area_m2 = _sqm(max(tiled_panel_area_mm2 - artwork_area_mm2, Decimal("0")))

    return (
        {
            "required": bool(layout_result.get("needs_tiling")),
            "overlap_m": round(overlap_mm / 1000, 3) if layout_result.get("needs_tiling") else 0,
            "panel_count": len(panels),
            "panels": panels,
        },
        overlap_area_m2,
    )


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
    printed_area = (area_per_piece * Decimal(quantity)).quantize(Decimal("0.0001"))
    material_rate = _decimal(getattr(material, "selling_price", 0))
    print_rate = _decimal(getattr(material, "print_price_per_sqm", 0))
    layout_result, layout_warnings, layout_assumptions = _build_roll_layout(
        product_subtype=product_subtype,
        width_mm=width_mm,
        height_mm=height_mm,
        quantity=quantity,
        material=material,
    )
    pricing_method = _pricing_method_for_material(material)
    printable_roll_width_mm = Decimal(str(layout_result.get("printable_width_mm"))) if layout_result else Decimal(str(width_mm))
    used_length_mm = Decimal(str(layout_result.get("roll_length_mm"))) if layout_result else Decimal(str(height_mm * quantity))
    charged_area = _roll_area_sqm(float(printable_roll_width_mm), float(used_length_mm)) if layout_result else printed_area
    charged_length_m = (used_length_mm / Decimal("1000")).quantize(Decimal("0.001"))
    pricing_base_area = charged_area if pricing_method == "per_square_meter" else printed_area
    tiling_payload, overlap_area = _build_tiling_payload(layout_result, width_mm, height_mm)
    waste_area = max(charged_area - printed_area - overlap_area, Decimal("0")).quantize(Decimal("0.0001"))
    material_cost = material_rate * (charged_area if pricing_method == "per_square_meter" else charged_length_m)
    print_cost = print_rate * pricing_base_area

    finishing_total, finishing_lines = compute_finishing_total(
        finishing_selections,
        quantity=quantity,
        good_sheets=0,
        area_sqm=charged_area,
    )

    hardware_total = Decimal("0")
    hardware_line = None
    if hardware_finishing_rate:
        hardware_line = compute_finishing_line(
            hardware_finishing_rate,
            quantity=quantity,
            good_sheets=0,
            area_sqm=charged_area,
            selected_side="both",
        ).to_dict()
        hardware_total = _decimal(hardware_line["total"])

    minimum_charge = _decimal(getattr(material, "minimum_charge", None), "0")
    raw_subtotal = material_cost + print_cost + finishing_total + hardware_total
    minimum_charge_applied = bool(minimum_charge and raw_subtotal < minimum_charge)
    subtotal = max(raw_subtotal, minimum_charge) if minimum_charge else raw_subtotal
    vat_summary = _resolve_vat_summary(shop, subtotal)
    grand_total = vat_summary["grand_total"]
    unit_price = grand_total / Decimal(quantity) if quantity else Decimal("0")

    resolved_turnaround_hours = turnaround_hours or int(subtype["default_turnaround_hours"])
    turnaround_estimate = estimate_turnaround(shop=shop, working_hours=resolved_turnaround_hours)

    warnings = list(layout_warnings)
    assumptions = [
        f"Subtype pricing uses the {str(subtype['label']).lower()} profile.",
        "Large-format pricing is calculated in the backend from roll usage, material pricing, and selected charges.",
        *layout_assumptions,
    ]

    explanations = [
        f"Area per piece: {area_per_piece.quantize(Decimal('0.0001'))} sqm.",
        f"Printed area: {printed_area} sqm for {quantity} piece(s).",
        f"Charged area: {charged_area} sqm.",
        f"Used roll length: {charged_length_m} m.",
    ]
    if pricing_method == "per_linear_meter":
        explanations.append(f"Material: {currency} {_format_money(material_rate)} x {charged_length_m} linear metres = {_format_money(material_cost)}.")
    else:
        explanations.append(f"Material: {currency} {_format_money(material_rate)} x {charged_area} sqm = {_format_money(material_cost)}.")
    explanations.append(f"Printing: {currency} {_format_money(print_rate)} x {pricing_base_area} sqm = {_format_money(print_cost)}.")
    if minimum_charge_applied:
        explanations.append(f"Minimum charge applied: {currency} {_format_money(minimum_charge)}.")
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
            "formula": (
                f"{charged_length_m} lm x {_format_money(material_rate)}"
                if pricing_method == "per_linear_meter"
                else f"{charged_area} sqm x {_format_money(material_rate)}"
            ),
            "metadata": {
                "material_id": material.id,
                "material_label": f"{material.material_type} ({material.unit})",
                "unit": material.unit,
                "pricing_method": pricing_method,
            },
        },
        {
            "code": "printing",
            "label": "Printing",
            "amount": _format_money(print_cost),
            "formula": f"{pricing_base_area} sqm x {_format_money(print_rate)}",
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
                "rate_per_unit": _format_money(material_rate),
                "print_price_per_sqm": _format_money(print_rate),
                "unit": material.unit,
                "pricing_method": pricing_method,
            },
            "dimensions": {
                "width_mm": width_mm,
                "height_mm": height_mm,
                "area_per_piece_sqm": str(area_per_piece.quantize(Decimal("0.0001"))),
                "area_sqm": str(printed_area),
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
                "area_sqm": str(printed_area),
            },
            "material": {
                "id": material.id,
                "label": f"{material.material_type} ({material.unit})",
                "unit": material.unit,
                "rate_per_sqm": _format_money(material_rate),
                "rate_per_unit": _format_money(material_rate),
                "total": _format_money(material_cost),
                "pricing_method": pricing_method,
            },
            "printing": {
                "rate_per_sqm": _format_money(print_rate),
                "total": _format_money(print_cost),
                "formula": f"charged_area_sqm x {_format_money(print_rate)}",
                "explanation": f"{pricing_base_area} sqm x {currency} {_format_money(print_rate)}",
            },
            "finishings": finishing_lines,
            "hardware": hardware_line,
            "layout": layout_result,
            "roll_usage": {
                "roll_width_mm": float(printable_roll_width_mm),
                "used_length_mm": float(used_length_mm),
                "charged_area_sqm": str(charged_area),
                "printed_area_sqm": str(printed_area),
                "overlap_area_sqm": str(overlap_area),
                "waste_area_sqm": str(waste_area),
                "items_per_row": layout_result.get("items_across") if layout_result else 1,
                "rows": layout_result.get("total_rows") if layout_result else quantity,
                "orientation": "rotated" if layout_result and layout_result.get("rotated") else "normal",
            },
            "tiling": tiling_payload,
            "pricing": {
                "method": pricing_method,
                "rate": float(material_rate),
                "print_rate": float(print_rate),
                "charged_area_m2": float(charged_area),
                "charged_length_m": float(charged_length_m),
                "minimum_charge": float(minimum_charge) if minimum_charge else None,
                "minimum_charge_applied": minimum_charge_applied,
                "subtotal": float(subtotal),
            },
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
        "items_per_row": layout_result.get("items_across") if layout_result else 1,
        "rows": layout_result.get("total_rows") if layout_result else quantity,
        "orientation": "rotated" if layout_result and layout_result.get("rotated") else "normal",
        "used_length_m": float(charged_length_m),
        "charged_area_m2": float(charged_area),
        "printed_area_m2": float(printed_area),
        "waste_area_m2": float(waste_area),
        "overlap_area_m2": float(overlap_area),
        "tiling": tiling_payload,
        "pricing": {
            "method": pricing_method,
            "rate": float(material_rate),
            "print_rate": float(print_rate),
            "subtotal": float(subtotal),
            "minimum_charge_applied": minimum_charge_applied,
        },
    }
