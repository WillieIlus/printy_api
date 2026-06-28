from __future__ import annotations

from decimal import Decimal
from typing import Any

from accounts.models import UserProfile
from pricing.services.platform_fee_policy import get_active_platform_fee_policy
from shops.models import Shop
from services.pricing.mvp_rate_card import (
    DEFAULT_PAPER_DEFINITIONS,
    _build_sample_job_preview,
    _canonical_paper_rows_for_shop,
    _decimal_stats,
    _enrich_paper_row,
    _is_sticker_row,
)

SAMPLE_QUANTITY = 100


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return None


def _product_type_for_row(row: dict[str, Any]) -> tuple[str, str]:
    if _is_sticker_row(row):
        return "label_sheet", "Label Sheets"
    gsm = int(row.get("gsm") or 0)
    if gsm >= 250:
        return "business_card", "Business Cards"
    return "flyer", "Flyers"


def _sample_totals(row: dict[str, Any]) -> dict[str, Any]:
    preview = (_build_sample_job_preview(row, []) or [{}])[0]
    single_total = _to_decimal(preview.get("single_sided_production"))
    double_total = _to_decimal(preview.get("double_sided_production"))
    return {
        "label": preview.get("label") or f"Sample {SAMPLE_QUANTITY} pieces",
        "pieces_per_sheet": int(preview.get("pieces_per_sheet") or 0),
        "sheets_needed": int(preview.get("sheets_needed") or 0),
        "single_total": single_total,
        "double_total": double_total,
        "single_per_unit": (single_total / Decimal(SAMPLE_QUANTITY)).quantize(Decimal("0.01")) if single_total is not None else None,
        "double_per_unit": (double_total / Decimal(SAMPLE_QUANTITY)).quantize(Decimal("0.01")) if double_total is not None else None,
    }


def _quality_labels(sample_count: int) -> tuple[str, str]:
    if sample_count >= 3:
        return "good", "high"
    if sample_count >= 1:
        return "limited", "medium" if sample_count >= 2 else "low"
    return "estimated", "insufficient_data"


def _market_side(values_total: list[Decimal], values_unit: list[Decimal], *, fallback_total: Decimal | None, fallback_unit: Decimal | None) -> tuple[dict[str, str | None], int]:
    stats_total = _decimal_stats(values_total)
    stats_unit = _decimal_stats(values_unit)
    sample_count = int(stats_total["sample_count"] or 0)
    if sample_count:
        return (
            {
                "median_per_unit": stats_unit["median"],
                "mean_per_unit": stats_unit["mean"],
                "min_per_unit": stats_unit["min"],
                "max_per_unit": stats_unit["max"],
                "median_total_100": stats_total["median"],
                "mean_total_100": stats_total["mean"],
                "min_total_100": stats_total["min"],
                "max_total_100": stats_total["max"],
            },
            sample_count,
        )
    return (
        {
            "median_per_unit": str(fallback_unit) if fallback_unit is not None else None,
            "mean_per_unit": str(fallback_unit) if fallback_unit is not None else None,
            "min_per_unit": str(fallback_unit) if fallback_unit is not None else None,
            "max_per_unit": str(fallback_unit) if fallback_unit is not None else None,
            "median_total_100": str(fallback_total) if fallback_total is not None else None,
            "mean_total_100": str(fallback_total) if fallback_total is not None else None,
            "min_total_100": str(fallback_total) if fallback_total is not None else None,
            "max_total_100": str(fallback_total) if fallback_total is not None else None,
        },
        0,
    )


def _active_shop_rows() -> dict[str, list[dict[str, Any]]]:
    rows_by_key: dict[str, list[dict[str, Any]]] = {definition["key"]: [] for definition in DEFAULT_PAPER_DEFINITIONS}
    shops = (
        Shop.objects.filter(is_active=True)
        .filter(papers__is_active=True, machines__printing_rates__is_active=True)
        .distinct()
        .prefetch_related("papers", "machines__printing_rates")
    )
    for shop in shops:
        seen_keys: set[str] = set()
        for raw_row in _canonical_paper_rows_for_shop(shop):
            key = str(raw_row.get("key") or "").strip()
            if not key or key in seen_keys or not raw_row.get("active"):
                continue
            seen_keys.add(key)
            rows_by_key.setdefault(key, []).append(_enrich_paper_row(raw_row))
    return rows_by_key


def build_partner_market_rate_payload(*, user) -> dict[str, Any]:
    rows_by_key = _active_shop_rows()
    profile, _ = UserProfile.objects.get_or_create(user=user)
    # TODO(batch-6): preview-only partner guidance, not authoritative split.
    default_markup_rate = get_active_platform_fee_policy().broker_margin_fee_rate
    results: list[dict[str, Any]] = []

    for definition in DEFAULT_PAPER_DEFINITIONS:
        key = definition["key"]
        baseline_row = _enrich_paper_row(definition, definition=definition)
        baseline_sample = _sample_totals(baseline_row)
        live_rows = rows_by_key.get(key) or []

        single_totals: list[Decimal] = []
        single_units: list[Decimal] = []
        double_totals: list[Decimal] = []
        double_units: list[Decimal] = []
        for row in live_rows:
            sample = _sample_totals(row)
            if sample["single_total"] is not None:
                single_totals.append(sample["single_total"])
                single_units.append(sample["single_per_unit"])
            if sample["double_total"] is not None:
                double_totals.append(sample["double_total"])
                double_units.append(sample["double_per_unit"])

        market_single, single_count = _market_side(
            single_totals,
            single_units,
            fallback_total=baseline_sample["single_total"],
            fallback_unit=baseline_sample["single_per_unit"],
        )
        market_double, double_count = _market_side(
            double_totals,
            double_units,
            fallback_total=baseline_sample["double_total"],
            fallback_unit=baseline_sample["double_per_unit"],
        )

        shops_count = len(live_rows)
        data_quality, confidence_label = _quality_labels(shops_count)
        product_type, product_label = _product_type_for_row(baseline_row)
        results.append(
            {
                "key": key,
                "product_type": product_type,
                "product_label": product_label,
                "paper_name": baseline_row.get("label"),
                "gsm": baseline_row.get("gsm"),
                "sample_quantity": SAMPLE_QUANTITY,
                "sample_job_label": baseline_sample["label"],
                "pieces_per_sheet": baseline_sample["pieces_per_sheet"],
                "sheets_needed": baseline_sample["sheets_needed"],
                "shops_count": shops_count,
                "data_quality": data_quality,
                "confidence_label": confidence_label,
                "double_sided_enabled": bool(baseline_row.get("double_sided_enabled")),
                "market_single": market_single,
                "market_double": market_double if baseline_row.get("double_sided_enabled") else None,
                "market_median_production_cost": market_single["median_total_100"],
                "market_low_high_band": {
                    "low": market_single["min_total_100"],
                    "high": market_single["max_total_100"],
                },
                "suggested_markup_percent": str(default_markup_rate * Decimal("100")),
                "explanation": (
                    f"Based on {shops_count} active shop rate card(s)."
                    if shops_count
                    else "Not enough live market data yet; showing the platform baseline."
                ),
            }
        )

    return {
        "role": "partner",
        "default_markup_rate": str(default_markup_rate),
        "results": results,
    }
