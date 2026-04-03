from __future__ import annotations

from decimal import Decimal
from math import ceil
from typing import Any

from inventory.models import Machine, Paper
from pricing.models import FinishingRate, PrintingRate
from quotes.turnaround import estimate_turnaround, humanize_working_hours
from services.engine.schemas.inputs import JobSpec
from services.engine.services.booklet_imposer import BookletImposer
from services.pricing.engine import (
    PricingEngineResult,
    _format_money,
    _resolve_vat_summary,
    calculate_sheet_pricing,
)
from services.pricing.finishings import compute_finishing_line
from services.pricing.result_contract import build_calculation_result


BOOKLET_BINDING_CHOICES = {
    "saddle_stitch": "Saddle stitch",
    "perfect_bind": "Perfect bind",
    "wire_o": "Wire-O",
}


def _decimal(value: Any, default: str = "0") -> Decimal:
    if value is None:
        return Decimal(default)
    return Decimal(str(value))


def _binding_display_name(binding_type: str) -> str:
    return BOOKLET_BINDING_CHOICES.get(binding_type, "Binding")


def _normalize_booklet_pages(total_pages: int, binding_type: str) -> tuple[int, list[str], list[str]]:
    warnings: list[str] = []
    assumptions: list[str] = []
    safe_total = max(int(total_pages or 0), 0)
    normalized = safe_total
    remainder = safe_total % 4
    if remainder:
        normalized = safe_total + (4 - remainder)
        warnings.append(f"Page count normalized from {safe_total} to {normalized} to fit folded booklet imposition.")
    if binding_type in {"perfect_bind", "wire_o"}:
        assumptions.append(
            f"{_binding_display_name(binding_type)} is currently quoted using folded booklet spread assumptions normalized to multiples of 4."
        )
    return normalized, warnings, assumptions


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


def _resolve_lamination_rule(shop, lamination_mode: str, lamination_finishing_rate: FinishingRate | None = None) -> FinishingRate | None:
    if lamination_mode == "none":
        return None
    if lamination_finishing_rate is not None:
        return lamination_finishing_rate
    queryset = FinishingRate.objects.filter(shop=shop, is_active=True).select_related("category").order_by("id")
    for finishing in queryset:
        if finishing.is_lamination_rule():
            return finishing
    return None


def _resolve_binding_rule(shop, binding_type: str, binding_finishing_rate: FinishingRate | None = None) -> FinishingRate | None:
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


def _spread_width_mm(width_mm: int) -> int:
    return max(1, int(width_mm or 0) * 2)


def _build_section_result(
    *,
    shop,
    quantity: int,
    width_mm: int,
    height_mm: int,
    paper: Paper,
    color_mode: str,
    sides: str,
    lamination_rule: FinishingRate | None = None,
    lamination_mode: str = "none",
) -> PricingEngineResult:
    machine = _resolve_machine(shop, paper, color_mode, sides)
    if not machine:
        return PricingEngineResult(
            pricing_mode="BOOKLET",
            quantity=quantity,
            currency=getattr(shop, "currency", "KES") or "KES",
            totals={},
            breakdown={
                "paper": {
                    "id": paper.id,
                    "label": f"{paper.sheet_size} {paper.gsm}gsm {paper.get_paper_type_display()}",
                    "sheet_size": paper.sheet_size,
                },
            },
            explanations=["No active machine/rate path matches this booklet section."],
            can_calculate=False,
            reason="No active machine/rate path matches this booklet section.",
        )

    finishing_selections = []
    if lamination_rule and lamination_mode != "none":
        finishing_selections.append(
            {
                "rule": lamination_rule,
                "selected_side": "front" if lamination_mode == "front" else "both",
            }
        )

    return calculate_sheet_pricing(
        shop=shop,
        product=None,
        quantity=quantity,
        paper=paper,
        machine=machine,
        color_mode=color_mode,
        sides=sides,
        finishing_selections=finishing_selections,
        width_mm=_spread_width_mm(width_mm),
        height_mm=height_mm,
    )


def calculate_booklet_pricing(
    *,
    shop,
    quantity: int,
    width_mm: int,
    height_mm: int,
    total_pages: int,
    binding_type: str,
    cover_paper: Paper,
    insert_paper: Paper,
    cover_sides: str,
    insert_sides: str,
    cover_color_mode: str = "COLOR",
    insert_color_mode: str = "COLOR",
    cover_lamination_mode: str = "none",
    cover_lamination_finishing_rate: FinishingRate | None = None,
    binding_finishing_rate: FinishingRate | None = None,
    turnaround_hours: int | None = None,
) -> dict[str, Any]:
    normalized_pages, warnings, assumptions = _normalize_booklet_pages(total_pages, binding_type)
    cover_pages = 4
    insert_pages = max(normalized_pages - cover_pages, 0)
    insert_pages_per_sheet = 4 if insert_sides == "DUPLEX" else 2
    insert_sheets_per_booklet = ceil(insert_pages / insert_pages_per_sheet) if insert_pages else 0
    total_cover_sheet_instances = quantity
    total_insert_sheet_instances = insert_sheets_per_booklet * quantity

    booklet_layout = BookletImposer().impose(
        JobSpec(
            product_type="booklet",
            finished_width_mm=width_mm,
            finished_height_mm=height_mm,
            quantity=quantity,
            pages=normalized_pages,
            cover_pages=cover_pages,
        )
    )

    lamination_rule = _resolve_lamination_rule(shop, cover_lamination_mode, cover_lamination_finishing_rate)
    if cover_lamination_mode != "none" and lamination_rule is None:
        reason = "No active lamination rate is configured for this shop."
        return _booklet_failure_payload(
            shop=shop,
            quantity=quantity,
            width_mm=width_mm,
            height_mm=height_mm,
            warnings=warnings,
            assumptions=assumptions,
            reason=reason,
        )

    binding_rule = _resolve_binding_rule(shop, binding_type, binding_finishing_rate)
    if binding_rule is None:
        reason = f"No active {_binding_display_name(binding_type)} rate is configured for this shop."
        return _booklet_failure_payload(
            shop=shop,
            quantity=quantity,
            width_mm=width_mm,
            height_mm=height_mm,
            warnings=warnings,
            assumptions=assumptions,
            reason=reason,
        )

    cover_result = _build_section_result(
        shop=shop,
        quantity=total_cover_sheet_instances,
        width_mm=width_mm,
        height_mm=height_mm,
        paper=cover_paper,
        color_mode=cover_color_mode,
        sides=cover_sides,
        lamination_rule=lamination_rule,
        lamination_mode=cover_lamination_mode,
    )
    if not cover_result.can_calculate:
        return _booklet_failure_payload(
            shop=shop,
            quantity=quantity,
            width_mm=width_mm,
            height_mm=height_mm,
            warnings=warnings,
            assumptions=assumptions,
            reason=cover_result.reason,
        )

    insert_result = _build_section_result(
        shop=shop,
        quantity=total_insert_sheet_instances,
        width_mm=width_mm,
        height_mm=height_mm,
        paper=insert_paper,
        color_mode=insert_color_mode,
        sides=insert_sides,
    )
    if not insert_result.can_calculate:
        return _booklet_failure_payload(
            shop=shop,
            quantity=quantity,
            width_mm=width_mm,
            height_mm=height_mm,
            warnings=warnings,
            assumptions=assumptions,
            reason=insert_result.reason,
        )

    binding_line = compute_finishing_line(
        binding_rule,
        quantity=quantity,
        good_sheets=0,
        selected_side="both",
    )
    binding_total = Decimal(binding_line.total)
    cover_subtotal = _decimal(cover_result.totals.get("subtotal"))
    insert_subtotal = _decimal(insert_result.totals.get("subtotal"))
    subtotal = cover_subtotal + insert_subtotal + binding_total
    vat_summary = _resolve_vat_summary(shop, subtotal)
    grand_total = vat_summary["grand_total"]
    unit_price = grand_total / Decimal(quantity) if quantity else Decimal("0")
    lamination_total = _decimal(cover_result.totals.get("finishing_total"))
    finishing_total = lamination_total + binding_total
    turnaround_estimate = estimate_turnaround(shop=shop, working_hours=turnaround_hours)

    cover_breakdown = cover_result.breakdown
    insert_breakdown = insert_result.breakdown
    currency = getattr(shop, "currency", "KES") or "KES"
    size_summary = f"{width_mm} x {height_mm} mm"
    explanations = [
        f"Booklet size: {size_summary}.",
        f"Pages: {total_pages} requested, {normalized_pages} priced.",
        f"Cover: {quantity} cover sheet(s) with {cover_sides.lower()} printing on {cover_breakdown.get('paper', {}).get('label', '')}.",
        f"Inserts: {insert_pages} inside page(s) -> {insert_sheets_per_booklet} insert sheet(s) per booklet, {total_insert_sheet_instances} insert sheet instance(s) total.",
        f"Binding: {binding_rule.name} for {quantity} booklet(s).",
    ]
    explanations.extend(warnings)
    explanations.extend(assumptions)
    if turnaround_estimate:
        explanations.append(turnaround_estimate.human_ready_text)

    line_items = [
        {
            "code": "cover_paper",
            "label": "Cover paper",
            "amount": cover_result.totals.get("paper_cost"),
            "formula": f"{cover_breakdown.get('imposition', {}).get('good_sheets', 0)} sheets x {cover_breakdown.get('paper', {}).get('paper_price_per_sheet', '0.00')}",
            "metadata": cover_breakdown.get("paper", {}),
        },
        {
            "code": "cover_printing",
            "label": "Cover printing",
            "amount": cover_result.totals.get("print_cost"),
            "formula": cover_breakdown.get("printing", {}).get("formula"),
            "metadata": cover_breakdown.get("printing", {}),
        },
    ]
    for index, finishing in enumerate(cover_breakdown.get("finishings", [])):
        line_items.append(
            {
                "code": f"cover_finishing_{index}",
                "label": f"Cover {finishing.get('name') or 'finishing'}",
                "amount": finishing.get("total"),
                "formula": finishing.get("formula"),
                "metadata": finishing,
            }
        )
    line_items.extend(
        [
            {
                "code": "insert_paper",
                "label": "Insert paper",
                "amount": insert_result.totals.get("paper_cost"),
                "formula": f"{insert_breakdown.get('imposition', {}).get('good_sheets', 0)} sheets x {insert_breakdown.get('paper', {}).get('paper_price_per_sheet', '0.00')}",
                "metadata": insert_breakdown.get("paper", {}),
            },
            {
                "code": "insert_printing",
                "label": "Insert printing",
                "amount": insert_result.totals.get("print_cost"),
                "formula": insert_breakdown.get("printing", {}).get("formula"),
                "metadata": insert_breakdown.get("printing", {}),
            },
            {
                "code": "binding",
                "label": binding_rule.name,
                "amount": _format_money(binding_total),
                "formula": binding_line.formula,
                "metadata": binding_line.to_dict(),
            },
        ]
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

    breakdown = {
        "booklet": {
            "binding_type": binding_type,
            "binding_label": _binding_display_name(binding_type),
            "finished_size": size_summary,
            "quantity": quantity,
            "requested_pages": total_pages,
            "normalized_pages": normalized_pages,
            "cover_pages": cover_pages,
            "insert_pages": insert_pages,
            "insert_sheets_per_booklet": insert_sheets_per_booklet,
            "total_cover_sheet_instances": total_cover_sheet_instances,
            "total_insert_sheet_instances": total_insert_sheet_instances,
            "blanks_added": booklet_layout.blanks_added,
            "warnings": warnings,
            "assumptions": assumptions,
        },
        "cover": {
            "sheet_instances": total_cover_sheet_instances,
            "print_sides": cover_sides,
            "color_mode": cover_color_mode,
            "lamination_mode": cover_lamination_mode,
            "paper": cover_breakdown.get("paper", {}),
            "printing": cover_breakdown.get("printing", {}),
            "imposition": cover_breakdown.get("imposition", {}),
            "finishings": cover_breakdown.get("finishings", []),
            "totals": {
                "paper_cost": cover_result.totals.get("paper_cost"),
                "print_cost": cover_result.totals.get("print_cost"),
                "finishing_total": cover_result.totals.get("finishing_total"),
                "subtotal": cover_result.totals.get("subtotal"),
            },
        },
        "inserts": {
            "sheet_instances": total_insert_sheet_instances,
            "print_sides": insert_sides,
            "color_mode": insert_color_mode,
            "paper": insert_breakdown.get("paper", {}),
            "printing": insert_breakdown.get("printing", {}),
            "imposition": insert_breakdown.get("imposition", {}),
            "totals": {
                "paper_cost": insert_result.totals.get("paper_cost"),
                "print_cost": insert_result.totals.get("print_cost"),
                "subtotal": insert_result.totals.get("subtotal"),
            },
        },
        "binding": {
            "binding_type": binding_type,
            "label": binding_rule.name,
            "formula": binding_line.formula,
            "rate": binding_line.rate,
            "units": binding_line.units,
            "total": _format_money(binding_total),
            "line": binding_line.to_dict(),
        },
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
        "cover_total": _format_money(cover_subtotal),
        "insert_total": _format_money(insert_subtotal),
        "binding_total": _format_money(binding_total),
        "subtotal": _format_money(vat_summary["subtotal"]),
        "paper_cost": _format_money(_decimal(cover_result.totals.get("paper_cost")) + _decimal(insert_result.totals.get("paper_cost"))),
        "print_cost": _format_money(_decimal(cover_result.totals.get("print_cost")) + _decimal(insert_result.totals.get("print_cost"))),
        "finishing_total": _format_money(finishing_total),
        "vat_amount": _format_money(vat_summary["vat_amount"]),
        "vat": _format_money(vat_summary["vat_amount"]),
        "vat_mode": vat_summary["vat"]["mode"],
        "grand_total": _format_money(grand_total),
        "total_job_price": _format_money(grand_total),
        "unit_price": _format_money(unit_price),
        "total_per_booklet": _format_money(unit_price),
    }

    calculation_result = build_calculation_result(
        quote_type="booklet",
        pricing_mode="BOOKLET",
        billing_type="per_booklet",
        size_summary=size_summary,
        quantity=quantity,
        currency=currency,
        line_items=line_items,
        subtotal=totals["subtotal"],
        finishing_total=totals["finishing_total"],
        grand_total=totals["grand_total"],
        unit_price=totals["unit_price"],
        explanation_blocks=[{"title": "Calculation", "text": text} for text in explanations if text],
        metadata=breakdown,
        warnings=warnings,
        assumptions=assumptions,
        can_calculate=True,
        reason="",
    )

    return {
        "quote_type": "booklet",
        "pricing_mode": "BOOKLET",
        "quantity": quantity,
        "currency": currency,
        "warnings": warnings,
        "assumptions": assumptions,
        "can_calculate": True,
        "reason": "",
        "explanations": explanations,
        "totals": totals,
        "breakdown": breakdown,
        "turnaround_hours": turnaround_hours,
        "estimated_working_hours": turnaround_hours,
        "estimated_ready_at": turnaround_estimate.ready_at if turnaround_estimate else None,
        "human_ready_text": turnaround_estimate.human_ready_text if turnaround_estimate else "Ready time on request",
        "turnaround_label": turnaround_estimate.label if turnaround_estimate else "On request",
        "turnaround_text": humanize_working_hours(turnaround_hours),
        "calculation_result": calculation_result,
    }


def _booklet_failure_payload(*, shop, quantity: int, width_mm: int, height_mm: int, warnings: list[str], assumptions: list[str], reason: str) -> dict[str, Any]:
    currency = getattr(shop, "currency", "KES") or "KES"
    return {
        "quote_type": "booklet",
        "pricing_mode": "BOOKLET",
        "quantity": quantity,
        "currency": currency,
        "warnings": warnings,
        "assumptions": assumptions,
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
            metadata={},
            warnings=warnings,
            assumptions=assumptions,
            can_calculate=False,
            reason=reason,
        ),
    }
