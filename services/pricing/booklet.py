from __future__ import annotations

from decimal import Decimal
from math import ceil
from typing import Any

from inventory.models import Machine, Paper
from pricing.models import FinishingRate, PrintingRate
from quotes.turnaround import estimate_turnaround, humanize_working_hours
from services.pricing.engine import _format_money, _resolve_vat_summary
from services.pricing.finishings import compute_finishing_line
from services.pricing.result_contract import build_calculation_result


BOOKLET_BINDING_CHOICES = {
    "saddle_stitch": "Saddle stitch",
    "perfect_bind": "Perfect bind",
    "wire_o": "Wire-O",
}

SUPPORTED_BOOKLET_SIZES = {
    "A4": ((210, 297), (297, 210)),
    "A5": ((148, 210), (210, 148)),
}


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def _binding_display_name(binding_type: str) -> str:
    return BOOKLET_BINDING_CHOICES.get(binding_type, "Binding")


def normalize_pages(total_pages: int | None) -> int:
    safe_total = max(int(total_pages or 0), 0)
    remainder = safe_total % 4
    return safe_total if remainder == 0 else safe_total + (4 - remainder)


def split_cover_inserts(total_pages: int | None) -> tuple[int, int, int]:
    normalized_pages = normalize_pages(total_pages)
    if normalized_pages <= 4:
        return normalized_pages, max(normalized_pages, 0), 0
    return normalized_pages, 4, max(normalized_pages - 4, 0)


def _infer_finished_size(width_mm: int, height_mm: int) -> str:
    dims = (int(width_mm or 0), int(height_mm or 0))
    for label, variants in SUPPORTED_BOOKLET_SIZES.items():
        if dims in variants:
            return label
    return "CUSTOM"


def cover_up_per_sheet(finished_size: str) -> int:
    if finished_size == "A4":
        return 1
    if finished_size == "A5":
        return 2
    return 1


def insert_up_per_sheet(finished_size: str) -> int:
    if finished_size == "A4":
        return 2
    if finished_size == "A5":
        return 4
    return 2


def calculate_sheets(units: int | float, up_per_sheet: int | float) -> int:
    safe_units = max(Decimal(str(units or 0)), Decimal("0"))
    safe_up = Decimal(str(up_per_sheet or 0))
    if safe_up <= 0:
        return 0
    return max(int(ceil(safe_units / safe_up)), 0)


def _resolve_machine(shop, paper: Paper, color_mode: str, sides: str) -> Machine | None:
    queryset = (
        Machine.objects.filter(shop=shop, is_active=True)
        .filter(
            printing_rates__sheet_size=paper.sheet_size,
            printing_rates__color_mode=color_mode,
            printing_rates__is_active=True,
        )
        .distinct()
        .order_by("id")
    )
    best_machine = None
    best_price = None
    for machine in queryset:
        _, resolved_price = PrintingRate.resolve(
            machine,
            paper.sheet_size,
            color_mode,
            sides,
            paper=paper,
        )
        if resolved_price is None:
            continue
        price = Decimal(str(resolved_price))
        if best_price is None or price < best_price:
            best_machine = machine
            best_price = price
    return best_machine


def _resolve_printing_path(shop, paper: Paper, color_mode: str, sides: str) -> tuple[Machine | None, PrintingRate | None, Decimal]:
    machine = _resolve_machine(shop, paper, color_mode, sides)
    if machine is None:
        return None, None, Decimal("0")
    rate, resolved_price = PrintingRate.resolve(
        machine,
        paper.sheet_size,
        color_mode,
        sides,
        paper=paper,
    )
    if rate is None or resolved_price is None:
        return machine, None, Decimal("0")
    return machine, rate, Decimal(str(resolved_price))


def _resolve_lamination_rule(
    shop,
    lamination_mode: str,
    lamination_finishing_rate: FinishingRate | None = None,
) -> FinishingRate | None:
    if lamination_mode == "none":
        return None
    if lamination_finishing_rate is not None:
        return lamination_finishing_rate
    queryset = FinishingRate.objects.filter(shop=shop, is_active=True).select_related("category").order_by("id")
    for finishing in queryset:
        if finishing.is_lamination_rule():
            return finishing
    return None


def _resolve_binding_rule(
    shop,
    binding_type: str,
    binding_finishing_rate: FinishingRate | None = None,
) -> FinishingRate | None:
    if binding_finishing_rate is not None:
        return binding_finishing_rate
    tokens = {
        "saddle_stitch": ("saddle", "stitch"),
        "perfect_bind": ("perfect", "bind"),
        "wire_o": ("wire", "wire-o", "wireo"),
    }.get(binding_type, ())
    queryset = FinishingRate.objects.filter(shop=shop, is_active=True).select_related("category").order_by("id")
    for finishing in queryset:
        haystacks = (
            (finishing.name or "").strip().lower(),
            (finishing.slug or "").strip().lower(),
            (getattr(finishing.category, "name", "") or "").strip().lower(),
        )
        if any(any(token in haystack for token in tokens) for haystack in haystacks):
            return finishing
    return None


def _paper_label(paper: Paper) -> str:
    return paper.marketplace_label


def _build_missing_payload(
    *,
    shop,
    quantity: int,
    width_mm: int | None,
    height_mm: int | None,
    missing_fields: list[str],
    warnings: list[str],
    assumptions: list[str],
    message: str,
) -> dict[str, Any]:
    currency = getattr(shop, "currency", "KES") or "KES"
    finished_size = _infer_finished_size(int(width_mm or 0), int(height_mm or 0))
    response = {
        "quote_type": "booklet",
        "product_type": "booklet",
        "pricing_mode": "BOOKLET",
        "price_mode": "estimate",
        "quantity": quantity,
        "currency": currency,
        "finished_size": finished_size,
        "warnings": warnings,
        "assumptions": assumptions,
        "missing_fields": missing_fields,
        "message": message,
        "can_calculate": False,
        "reason": message,
        "breakdown": {},
        "totals": {},
    }
    response["calculation_result"] = build_calculation_result(
        quote_type="booklet",
        pricing_mode="BOOKLET",
        billing_type="per_booklet",
        size_summary=f"{width_mm or 0} x {height_mm or 0} mm",
        quantity=quantity,
        currency=currency,
        line_items=[],
        explanation_blocks=[{"title": "Calculation", "text": message}],
        metadata={"missing_fields": missing_fields},
        warnings=warnings,
        assumptions=assumptions,
        can_calculate=False,
        reason=message,
    )
    return response


def _booklet_failure_payload(
    *,
    shop,
    quantity: int,
    width_mm: int,
    height_mm: int,
    warnings: list[str],
    assumptions: list[str],
    reason: str,
    missing_fields: list[str] | None = None,
) -> dict[str, Any]:
    currency = getattr(shop, "currency", "KES") or "KES"
    finished_size = _infer_finished_size(width_mm, height_mm)
    message = reason
    return {
        "quote_type": "booklet",
        "product_type": "booklet",
        "pricing_mode": "BOOKLET",
        "price_mode": "estimate",
        "quantity": quantity,
        "currency": currency,
        "finished_size": finished_size,
        "warnings": warnings,
        "assumptions": assumptions,
        "missing_fields": missing_fields or [],
        "message": message,
        "can_calculate": False,
        "reason": reason,
        "explanations": [reason],
        "totals": {},
        "breakdown": {},
        "calculation_result": build_calculation_result(
            quote_type="booklet",
            pricing_mode="BOOKLET",
            billing_type="per_booklet",
            size_summary=f"{width_mm} x {height_mm} mm",
            quantity=quantity,
            currency=currency,
            line_items=[],
            explanation_blocks=[{"title": "Calculation", "text": reason}],
            metadata={"missing_fields": missing_fields or []},
            warnings=warnings,
            assumptions=assumptions,
            can_calculate=False,
            reason=reason,
        ),
    }


def calculate_booklet_pricing(
    *,
    shop,
    quantity: int,
    width_mm: int | None,
    height_mm: int | None,
    total_pages: int | None,
    binding_type: str,
    cover_paper: Paper | None,
    insert_paper: Paper | None,
    cover_sides: str,
    insert_sides: str,
    cover_color_mode: str = "COLOR",
    insert_color_mode: str = "COLOR",
    cover_lamination_mode: str = "none",
    cover_lamination_finishing_rate: FinishingRate | None = None,
    finishing_selections: list[dict] | None = None,
    binding_finishing_rate: FinishingRate | None = None,
    turnaround_hours: int | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    assumptions: list[str] = ["Cover priced separately from inserts"]
    missing_fields: list[str] = []

    safe_quantity = max(int(quantity or 0), 0)
    safe_width = int(width_mm or 0)
    safe_height = int(height_mm or 0)
    raw_pages = int(total_pages or 0)

    if raw_pages <= 0:
        missing_fields.append("pages")
    elif raw_pages <= 4:
        missing_fields.append("pages")
    if safe_width <= 0:
        missing_fields.append("width_mm")
    if safe_height <= 0:
        missing_fields.append("height_mm")
    if cover_paper is None:
        missing_fields.append("cover_stock")
    if insert_paper is None:
        missing_fields.append("insert_stock")

    if missing_fields:
        message = "Choose cover stock, insert stock, and total pages to price this booklet."
        if "width_mm" in missing_fields or "height_mm" in missing_fields:
            message = "Choose the booklet size, cover stock, insert stock, and total pages to price this booklet."
        if raw_pages <= 4 and "pages" in missing_fields:
            warnings.append("Booklets with 4 pages or fewer need a valid insert page count before production pricing can be calculated.")
        return _build_missing_payload(
            shop=shop,
            quantity=safe_quantity,
            width_mm=safe_width,
            height_mm=safe_height,
            missing_fields=missing_fields,
            warnings=warnings,
            assumptions=assumptions,
            message=message,
        )

    normalized_pages, cover_pages, insert_pages = split_cover_inserts(raw_pages)
    blank_pages_added = max(normalized_pages - raw_pages, 0)
    if blank_pages_added:
        warnings.append("Booklet pages were rounded up to the next multiple of 4 for production.")
        assumptions.append("Booklet pages rounded up to nearest multiple of 4")
    if binding_type in {"perfect_bind", "wire_o"}:
        assumptions.append(
            f"{_binding_display_name(binding_type)} is currently quoted using folded booklet spread assumptions normalized to multiples of 4."
        )

    finished_size = _infer_finished_size(safe_width, safe_height)
    cover_up = cover_up_per_sheet(finished_size)
    insert_up = insert_up_per_sheet(finished_size)
    if finished_size == "CUSTOM":
        warnings.append("Unsupported booklet size mapped to a safe SRA3 fallback for quoting.")

    cover_sheets = calculate_sheets(safe_quantity, cover_up)
    insert_spreads_per_booklet = ceil(insert_pages / 2) if insert_pages else 0
    insert_sheets = calculate_sheets(insert_spreads_per_booklet * safe_quantity, insert_up)

    cover_machine, cover_print_rate_obj, cover_print_rate = _resolve_printing_path(
        shop, cover_paper, cover_color_mode, cover_sides
    )
    insert_machine, insert_print_rate_obj, insert_print_rate = _resolve_printing_path(
        shop, insert_paper, insert_color_mode, insert_sides
    )

    if cover_machine is None or cover_print_rate_obj is None:
        return _booklet_failure_payload(
            shop=shop,
            quantity=safe_quantity,
            width_mm=safe_width,
            height_mm=safe_height,
            warnings=warnings,
            assumptions=assumptions,
            reason="No active machine/rate path matches the selected cover stock.",
        )
    if insert_machine is None or insert_print_rate_obj is None:
        return _booklet_failure_payload(
            shop=shop,
            quantity=safe_quantity,
            width_mm=safe_width,
            height_mm=safe_height,
            warnings=warnings,
            assumptions=assumptions,
            reason="No active machine/rate path matches the selected insert stock.",
        )

    cover_paper_cost = _decimal(cover_paper.selling_price) * Decimal(cover_sheets)
    insert_paper_cost = _decimal(insert_paper.selling_price) * Decimal(insert_sheets)
    cover_printing_cost = cover_print_rate * Decimal(cover_sheets)
    insert_printing_cost = insert_print_rate * Decimal(insert_sheets)

    lamination_rule = _resolve_lamination_rule(shop, cover_lamination_mode, cover_lamination_finishing_rate)
    if cover_lamination_mode != "none" and lamination_rule is None:
        return _booklet_failure_payload(
            shop=shop,
            quantity=safe_quantity,
            width_mm=safe_width,
            height_mm=safe_height,
            warnings=warnings,
            assumptions=assumptions,
            reason="No active lamination rate is configured for this shop.",
            missing_fields=["cover_lamination_rate"],
        )

    lamination_line = None
    lamination_total = Decimal("0")
    lamination_side_count = 0
    if lamination_rule is not None:
        lamination_selected_side = "front" if cover_lamination_mode == "front" else "both"
        lamination_line = compute_finishing_line(
            lamination_rule,
            quantity=safe_quantity,
            good_sheets=cover_sheets,
            selected_side=lamination_selected_side,
        )
        lamination_total = Decimal(lamination_line.total)
        lamination_side_count = lamination_line.side_count
        assumptions.append("Lamination is applied to cover sheets only.")

    binding_rule = _resolve_binding_rule(shop, binding_type, binding_finishing_rate)
    binding_line = None
    stitching_total = Decimal("0")
    other_binding_total = Decimal("0")
    if binding_rule is not None:
        binding_line = compute_finishing_line(
            binding_rule,
            quantity=safe_quantity,
            good_sheets=0,
            selected_side="both",
        )
        if binding_type == "saddle_stitch":
            stitching_total = Decimal(binding_line.total)
        else:
            other_binding_total = Decimal(binding_line.total)
    else:
        assumptions.append(
            f"{_binding_display_name(binding_type)} binding is not separately priced for this shop. "
            "The quoted total covers paper and printing only. Ask the shop for their binding price."
        )

    cutting_total = Decimal("0")
    other_finishings_total = Decimal("0")
    final_finishing_lines = []
    for selection in finishing_selections or []:
        rule = selection.get("finishing_rate") or selection.get("rule")
        if not rule:
            continue
        if lamination_rule and rule.id == lamination_rule.id:
            continue
        if binding_rule and rule.id == binding_rule.id:
            continue
        line = compute_finishing_line(
            rule,
            quantity=safe_quantity,
            good_sheets=cover_sheets + insert_sheets,
            selected_side=selection.get("selected_side", "both"),
        )
        final_finishing_lines.append(line)
        line_total = Decimal(line.total)
        haystacks = (
            (rule.name or "").strip().lower(),
            (rule.slug or "").strip().lower(),
            (getattr(rule.category, "name", "") or "").strip().lower(),
            (getattr(rule.category, "slug", "") or "").strip().lower(),
        )
        if any("cut" in token for token in haystacks):
            cutting_total += line_total
        else:
            other_finishings_total += line_total

    subtotal = (
        cover_paper_cost
        + insert_paper_cost
        + cover_printing_cost
        + insert_printing_cost
        + lamination_total
        + stitching_total
        + cutting_total
        + other_binding_total
        + other_finishings_total
    )
    vat_summary = _resolve_vat_summary(shop, subtotal)
    grand_total = vat_summary["grand_total"]
    unit_price = grand_total / Decimal(safe_quantity) if safe_quantity else Decimal("0")
    currency = getattr(shop, "currency", "KES") or "KES"
    turnaround_estimate = estimate_turnaround(shop=shop, working_hours=turnaround_hours)

    explanations = [
        f"Booklet size: {safe_width} x {safe_height} mm ({finished_size}).",
        f"Pages: {raw_pages} requested, {normalized_pages} priced.",
        f"Cover: {cover_pages} page(s), {cover_sheets} sheet(s), {_paper_label(cover_paper)}.",
        f"Inserts: {insert_pages} page(s), {insert_sheets} sheet(s), {_paper_label(insert_paper)}.",
    ]
    explanations.extend(warnings)
    explanations.extend(assumptions)
    if turnaround_estimate:
        explanations.append(turnaround_estimate.human_ready_text)

    top_level_breakdown = {
        "cover_paper": _format_money(cover_paper_cost),
        "insert_paper": _format_money(insert_paper_cost),
        "cover_printing": _format_money(cover_printing_cost),
        "insert_printing": _format_money(insert_printing_cost),
        "lamination": _format_money(lamination_total),
        "stitching": _format_money(stitching_total + other_binding_total),
        "cutting": _format_money(cutting_total),
        "subtotal": _format_money(vat_summary["subtotal"]),
        "total": _format_money(grand_total),
    }

    nested_breakdown = {
        "booklet": {
            "binding_type": binding_type,
            "binding_label": _binding_display_name(binding_type),
            "finished_size": finished_size,
            "quantity": safe_quantity,
            "requested_pages": raw_pages,
            "normalized_pages": normalized_pages,
            "blank_pages_added": blank_pages_added,
            "blanks_added": blank_pages_added,
            "cover_pages": cover_pages,
            "insert_pages": insert_pages,
            "cover_up_per_sheet": cover_up,
            "insert_up_per_sheet": insert_up,
            "cover_sheets": cover_sheets,
            "insert_sheets": insert_sheets,
            "insert_spreads_per_booklet": insert_spreads_per_booklet,
            "warnings": warnings,
            "assumptions": assumptions,
        },
        "cover": {
            "sheet_instances": cover_sheets,
            "print_sides": cover_sides,
            "color_mode": cover_color_mode,
            "machine_id": cover_machine.id if cover_machine else None,
            "machine_name": getattr(cover_machine, "name", ""),
            "paper": {
                "id": cover_paper.id,
                "label": _paper_label(cover_paper),
                "sheet_size": cover_paper.sheet_size,
                "paper_price_per_sheet": _format_money(_decimal(cover_paper.selling_price)),
            },
            "printing": {
                "machine_id": cover_machine.id if cover_machine else None,
                "machine_name": getattr(cover_machine, "name", ""),
                "rate_per_sheet": _format_money(cover_print_rate),
                "formula": f"{cover_sheets} sheets x {_format_money(cover_print_rate)}",
            },
            "finishings": [lamination_line.to_dict()] if lamination_line else [],
            "totals": {
                "paper_cost": _format_money(cover_paper_cost),
                "print_cost": _format_money(cover_printing_cost),
                "finishing_total": _format_money(lamination_total),
                "subtotal": _format_money(cover_paper_cost + cover_printing_cost + lamination_total),
            },
        },
        "inserts": {
            "sheet_instances": insert_sheets,
            "print_sides": insert_sides,
            "color_mode": insert_color_mode,
            "machine_id": insert_machine.id if insert_machine else None,
            "machine_name": getattr(insert_machine, "name", ""),
            "paper": {
                "id": insert_paper.id,
                "label": _paper_label(insert_paper),
                "sheet_size": insert_paper.sheet_size,
                "paper_price_per_sheet": _format_money(_decimal(insert_paper.selling_price)),
            },
            "printing": {
                "machine_id": insert_machine.id if insert_machine else None,
                "machine_name": getattr(insert_machine, "name", ""),
                "rate_per_sheet": _format_money(insert_print_rate),
                "formula": f"{insert_sheets} sheets x {_format_money(insert_print_rate)}",
            },
            "totals": {
                "paper_cost": _format_money(insert_paper_cost),
                "print_cost": _format_money(insert_printing_cost),
                "subtotal": _format_money(insert_paper_cost + insert_printing_cost),
            },
        },
        "binding": {
            "binding_type": binding_type,
            "label": binding_rule.name if binding_rule else f"{_binding_display_name(binding_type)} (not separately priced)",
            "formula": binding_line.formula if binding_line else None,
            "rate": binding_line.rate if binding_line else None,
            "units": binding_line.units if binding_line else None,
            "total": _format_money(stitching_total + other_binding_total),
            "line": binding_line.to_dict() if binding_line else {},
        },
        "finishings": [line.to_dict() for line in final_finishing_lines],
        "pricing_breakdown": top_level_breakdown,
        "turnaround": {
            "turnaround_hours": turnaround_hours,
            "turnaround_text": humanize_working_hours(turnaround_hours),
            "estimated_ready_at": turnaround_estimate.ready_at if turnaround_estimate else None,
            "human_ready_text": turnaround_estimate.human_ready_text if turnaround_estimate else "Ready time on request",
            "turnaround_label": turnaround_estimate.label if turnaround_estimate else "On request",
        },
        "vat": vat_summary["vat"],
    }

    totals = {
        "cover_total": _format_money(cover_paper_cost + cover_printing_cost + lamination_total),
        "insert_total": _format_money(insert_paper_cost + insert_printing_cost),
        "binding_total": _format_money(stitching_total + other_binding_total),
        "subtotal": _format_money(vat_summary["subtotal"]),
        "paper_cost": _format_money(cover_paper_cost + insert_paper_cost),
        "print_cost": _format_money(cover_printing_cost + insert_printing_cost),
        "finishing_total": _format_money(lamination_total + stitching_total + cutting_total + other_binding_total + other_finishings_total),
        "vat_amount": _format_money(vat_summary["vat_amount"]),
        "vat": _format_money(vat_summary["vat_amount"]),
        "vat_mode": vat_summary["vat"]["mode"],
        "grand_total": _format_money(grand_total),
        "total_job_price": _format_money(grand_total),
        "unit_price": _format_money(unit_price),
        "total_per_booklet": _format_money(unit_price),
    }

    line_items = [
        {
            "code": "cover_paper",
            "label": "Cover paper",
            "amount": _format_money(cover_paper_cost),
            "formula": f"{cover_sheets} sheets x {_format_money(_decimal(cover_paper.selling_price))}",
            "metadata": nested_breakdown["cover"]["paper"],
        },
        {
            "code": "cover_printing",
            "label": "Cover printing",
            "amount": _format_money(cover_printing_cost),
            "formula": nested_breakdown["cover"]["printing"]["formula"],
            "metadata": nested_breakdown["cover"]["printing"],
        },
        {
            "code": "insert_paper",
            "label": "Insert paper",
            "amount": _format_money(insert_paper_cost),
            "formula": f"{insert_sheets} sheets x {_format_money(_decimal(insert_paper.selling_price))}",
            "metadata": nested_breakdown["inserts"]["paper"],
        },
        {
            "code": "insert_printing",
            "label": "Insert printing",
            "amount": _format_money(insert_printing_cost),
            "formula": nested_breakdown["inserts"]["printing"]["formula"],
            "metadata": nested_breakdown["inserts"]["printing"],
        },
    ]
    if lamination_line is not None:
        line_items.append(
            {
                "code": "lamination",
                "label": lamination_rule.name,
                "amount": _format_money(lamination_total),
                "formula": lamination_line.formula,
                "metadata": lamination_line.to_dict(),
            }
        )
    if binding_line is not None:
        line_items.append(
            {
                "code": "binding",
                "label": binding_rule.name,
                "amount": _format_money(stitching_total + other_binding_total),
                "formula": binding_line.formula,
                "metadata": binding_line.to_dict(),
            }
        )
    for index, line in enumerate(final_finishing_lines):
        line_items.append(
            {
                "code": f"finishing_{index}",
                "label": line.name,
                "amount": _format_money(Decimal(line.total)),
                "formula": line.formula,
                "metadata": line.to_dict(),
            }
        )
    if _decimal(vat_summary["vat_amount"]) > 0:
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
        quote_type="booklet",
        pricing_mode="BOOKLET",
        billing_type="per_booklet",
        size_summary=f"{safe_width} x {safe_height} mm",
        quantity=safe_quantity,
        currency=currency,
        line_items=line_items,
        subtotal=totals["subtotal"],
        finishing_total=totals["finishing_total"],
        grand_total=totals["grand_total"],
        unit_price=totals["unit_price"],
        explanation_blocks=[{"title": "Calculation", "text": text} for text in explanations if text],
        metadata=nested_breakdown,
        warnings=warnings,
        assumptions=assumptions,
        can_calculate=True,
        reason="",
    )

    return {
        "quote_type": "booklet",
        "product_type": "booklet",
        "pricing_mode": "BOOKLET",
        "price_mode": "estimate" if finished_size == "CUSTOM" else "exact",
        "quantity": safe_quantity,
        "currency": currency,
        "finished_size": finished_size,
        "input_pages": raw_pages,
        "normalized_pages": normalized_pages,
        "blank_pages_added": blank_pages_added,
        "cover_pages": cover_pages,
        "insert_pages": insert_pages,
        "cover_sheets": cover_sheets,
        "insert_sheets": insert_sheets,
        "warnings": warnings,
        "assumptions": assumptions,
        "missing_fields": [],
        "message": "",
        "can_calculate": True,
        "reason": "",
        "explanations": explanations,
        "totals": totals,
        "breakdown": {
            **top_level_breakdown,
            **nested_breakdown,
        },
        "turnaround_hours": turnaround_hours,
        "estimated_working_hours": turnaround_hours,
        "estimated_ready_at": turnaround_estimate.ready_at if turnaround_estimate else None,
        "human_ready_text": turnaround_estimate.human_ready_text if turnaround_estimate else "Ready time on request",
        "turnaround_label": turnaround_estimate.label if turnaround_estimate else "On request",
        "turnaround_text": humanize_working_hours(turnaround_hours),
        "calculation_result": calculation_result,
        "cover_up_per_sheet": cover_up,
        "insert_up_per_sheet": insert_up,
        "lamination_side_count": lamination_side_count,
    }
