from __future__ import annotations

from decimal import Decimal
from typing import Any


def _money(value: Any) -> str | None:
    if value is None:
        return None
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _merge_metadata(base: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(base)
    if extra:
        for key, value in extra.items():
            if value is not None:
                merged[key] = value
    return merged


def build_calculation_result(
    *,
    quote_type: str,
    pricing_mode: str | None,
    billing_type: str | None,
    size_summary: str | None,
    quantity: int,
    currency: str,
    line_items: list[dict[str, Any]],
    explanation_blocks: list[dict[str, Any]],
    metadata: dict[str, Any],
    subtotal: Any = None,
    finishing_total: Any = None,
    turnaround_total: Any = None,
    grand_total: Any = None,
    unit_price: Any = None,
    warnings: list[str] | None = None,
    assumptions: list[str] | None = None,
    can_calculate: bool = True,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "version": 1,
        "quote_type": quote_type,
        "pricing_mode": pricing_mode,
        "billing_type": billing_type,
        "size_summary": size_summary,
        "quantity": quantity,
        "currency": currency,
        "line_items": line_items,
        "subtotal": _money(subtotal),
        "finishing_total": _money(finishing_total),
        "turnaround_total": _money(turnaround_total) if turnaround_total is not None else None,
        "grand_total": _money(grand_total),
        "unit_price": _money(unit_price),
        "explanation_blocks": explanation_blocks,
        "warnings": warnings or ([reason] if reason else []),
        "assumptions": assumptions or [],
        "metadata": metadata,
        "can_calculate": can_calculate,
        "reason": reason,
    }


def build_contract_from_engine_payload(
    payload: dict[str, Any],
    *,
    quote_type: str = "flat",
    metadata_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pricing_mode = payload.get("pricing_mode")
    currency = payload.get("currency") or "KES"
    quantity = int(payload.get("quantity") or 0)
    totals = payload.get("totals") or {}
    breakdown = payload.get("breakdown") or {}
    explanations = payload.get("explanations") or []
    line_items: list[dict[str, Any]] = []
    explanation_blocks = [{"title": "Calculation", "text": text} for text in explanations if text]

    if pricing_mode == "SHEET":
        paper = breakdown.get("paper") or {}
        printing = breakdown.get("printing") or {}
        finishings = breakdown.get("finishings") or []
        per_sheet = breakdown.get("per_sheet_pricing") or {}
        imposition = breakdown.get("imposition") or {}

        line_items.extend(
            [
                {
                    "code": "paper",
                    "label": "Paper",
                    "amount": totals.get("paper_cost"),
                    "formula": f"{imposition.get('good_sheets', 0)} sheets x {paper.get('paper_price_per_sheet') or paper.get('cost_per_sheet') or paper.get('paper_price') or '0.00'}",
                    "metadata": {
                        "paper_id": paper.get("id"),
                        "paper_label": paper.get("label"),
                    },
                },
                {
                    "code": "printing",
                    "label": "Printing",
                    "amount": totals.get("print_cost"),
                    "formula": printing.get("formula"),
                    "metadata": {
                        "machine_id": printing.get("machine_id"),
                        "machine_name": printing.get("machine_name"),
                        "print_price_front": printing.get("print_price_front"),
                        "print_price_back": printing.get("print_price_back"),
                        "duplex_surcharge": printing.get("duplex_surcharge"),
                    },
                },
            ]
        )
        for index, finishing in enumerate(finishings):
            line_items.append(
                {
                    "code": f"finishing_{index}",
                    "label": finishing.get("name") or "Finishing",
                    "amount": finishing.get("total"),
                    "formula": finishing.get("formula"),
                    "metadata": {
                        "selected_side": finishing.get("selected_side"),
                        "billing_basis": finishing.get("billing_basis"),
                        "side_mode": finishing.get("side_mode"),
                    },
                }
            )
        if totals.get("vat_amount") not in (None, "0", "0.00"):
            line_items.append(
                {
                    "code": "vat",
                    "label": "VAT",
                    "amount": totals.get("vat_amount"),
                    "formula": breakdown.get("vat", {}).get("label"),
                    "metadata": breakdown.get("vat") or {},
                }
            )

        metadata = _merge_metadata(
            {
                "paper": paper,
                "printing": printing,
                "imposition": {
                    "copies_per_sheet": imposition.get("copies_per_sheet"),
                    "sheets_required": imposition.get("good_sheets"),
                    "parent_sheets_required": imposition.get("parent_sheets_required"),
                    "orientation": imposition.get("orientation"),
                },
                "size_summary": paper.get("sheet_size"),
                "per_sheet_pricing": {
                    "paper_price": per_sheet.get("paper_price"),
                    "print_price_front": per_sheet.get("print_price_front"),
                    "print_price_back": per_sheet.get("print_price_back"),
                    "duplex_surcharge": per_sheet.get("duplex_surcharge"),
                    "print_total_per_sheet": per_sheet.get("print_total_per_sheet"),
                    "total_per_sheet": per_sheet.get("total_per_sheet"),
                    "formula": per_sheet.get("formula"),
                    "total_job_price": per_sheet.get("total_job_price") or totals.get("total_job_price") or totals.get("grand_total"),
                },
            },
            metadata_overrides,
        )
        return build_calculation_result(
            quote_type=quote_type,
            pricing_mode=pricing_mode,
            billing_type="per_sheet",
            size_summary=paper.get("label") or paper.get("sheet_size"),
            quantity=quantity,
            currency=currency,
            line_items=line_items,
            subtotal=totals.get("subtotal"),
            finishing_total=totals.get("finishing_total"),
            grand_total=totals.get("grand_total"),
            unit_price=totals.get("unit_price"),
            explanation_blocks=explanation_blocks,
            metadata=metadata,
            can_calculate=payload.get("can_calculate", True),
            reason=payload.get("reason", ""),
        )

    material = breakdown.get("material") or {}
    dimensions = breakdown.get("dimensions") or {}
    finishings = breakdown.get("finishings") or []

    line_items.append(
        {
            "code": "material",
            "label": "Material",
            "amount": totals.get("material_cost"),
            "formula": f"{dimensions.get('area_sqm')} sqm x {material.get('rate_per_unit')}",
            "metadata": {
                "material_id": material.get("id"),
                "material_label": material.get("label"),
                "unit": material.get("unit"),
            },
        }
    )
    for index, finishing in enumerate(finishings):
        line_items.append(
            {
                "code": f"finishing_{index}",
                "label": finishing.get("name") or "Finishing",
                "amount": finishing.get("total"),
                "formula": finishing.get("formula"),
                "metadata": {
                    "selected_side": finishing.get("selected_side"),
                    "billing_basis": finishing.get("billing_basis"),
                    "side_mode": finishing.get("side_mode"),
                },
            }
        )
    if totals.get("vat_amount") not in (None, "0", "0.00"):
        line_items.append(
            {
                "code": "vat",
                "label": "VAT",
                "amount": totals.get("vat_amount"),
                "formula": breakdown.get("vat", {}).get("label"),
                "metadata": breakdown.get("vat") or {},
            }
        )

    metadata = _merge_metadata(
        {
            "material": material,
            "dimensions": dimensions,
        },
        metadata_overrides,
    )
    return build_calculation_result(
        quote_type=quote_type,
        pricing_mode=pricing_mode,
        billing_type="per_area",
        size_summary=(
            f"{dimensions.get('width_mm')}x{dimensions.get('height_mm')}mm"
            if dimensions.get("width_mm") and dimensions.get("height_mm")
            else None
        ),
        quantity=quantity,
        currency=currency,
        line_items=line_items,
        subtotal=totals.get("subtotal"),
        finishing_total=totals.get("finishing_total"),
        grand_total=totals.get("grand_total"),
        unit_price=totals.get("unit_price"),
        explanation_blocks=explanation_blocks,
        metadata=metadata,
        can_calculate=payload.get("can_calculate", True),
        reason=payload.get("reason", ""),
    )


def build_quote_request_preview_contract(
    *,
    currency: str,
    quantity: int,
    line_items: list[dict[str, Any]],
    subtotal: Any,
    finishing_total: Any,
    grand_total: Any,
    warnings: list[str] | None,
    metadata: dict[str, Any],
    reason: str = "",
    can_calculate: bool = True,
) -> dict[str, Any]:
    return build_calculation_result(
        quote_type="quote_request_preview",
        pricing_mode="mixed",
        billing_type=None,
        size_summary=None,
        quantity=quantity,
        currency=currency,
        line_items=line_items,
        subtotal=subtotal,
        finishing_total=finishing_total,
        grand_total=grand_total,
        unit_price=None,
        explanation_blocks=[],
        metadata=metadata,
        warnings=warnings,
        can_calculate=can_calculate,
        reason=reason,
    )
