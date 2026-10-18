"""Pooled public price ranges (law 10.3).

A range is published only when the pooled data has at least
``MIN_SAMPLE_FOR_PUBLIC_RANGE`` records from at least
``MIN_SHOPS_FOR_PUBLIC_RANGE`` distinct shops. Below either threshold the
service returns ``None`` -- callers must show nothing, never a placeholder.

Outliers are removed by exactly one rule, :func:`trim_outliers` (Tukey's 1.5 x
IQR fences), and that is the only trimming implementation in the codebase.
Aggregate output never contains a shop id, shop name, printer identity, or a
per-shop/per-job price.
"""

from __future__ import annotations

from decimal import Decimal
from math import ceil, floor
from typing import Iterable

from django.conf import settings

from quotes.models import QuoteVersion, QuoteVersionOutcome

DEFAULT_MIN_SAMPLE = 5
DEFAULT_MIN_SHOPS = 3


def min_sample_for_public_range() -> int:
    return int(getattr(settings, "MIN_SAMPLE_FOR_PUBLIC_RANGE", DEFAULT_MIN_SAMPLE))


def min_shops_for_public_range() -> int:
    return int(getattr(settings, "MIN_SHOPS_FOR_PUBLIC_RANGE", DEFAULT_MIN_SHOPS))


def _percentile(sorted_values: list[Decimal], fraction: float) -> Decimal:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower = floor(position)
    upper = ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = Decimal(str(position - lower))
    return sorted_values[lower] * (Decimal("1") - weight) + sorted_values[upper] * weight


def trim_outliers(values: Iterable[Decimal]) -> list[Decimal]:
    """Tukey's rule: drop values outside 1.5 x IQR. The one trimming rule."""
    ordered = sorted(values)
    if len(ordered) < 4:
        return ordered
    q1 = _percentile(ordered, 0.25)
    q3 = _percentile(ordered, 0.75)
    iqr = q3 - q1
    lower_fence = q1 - Decimal("1.5") * iqr
    upper_fence = q3 + Decimal("1.5") * iqr
    return [value for value in ordered if lower_fence <= value <= upper_fence]


def _quantise(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _eligible_versions(spec_fingerprint: str):
    return (
        QuoteVersion.objects.filter(
            spec_fingerprint=spec_fingerprint,
            buyer_price__isnull=False,
            printer__isnull=False,
        )
        .exclude(outcome=QuoteVersionOutcome.SUPERSEDED)
        .select_related("printer")
        .order_by("quote_request_id", "quote_id", "-version_number", "-id")
    )


def _latest_per_offer(versions) -> list[QuoteVersion]:
    """Keep one current version per (request, shop) offer.

    A superseded version is history, not a separate market observation; the
    latest non-superseded version is the offer's current price.
    """
    seen: set[tuple[int, int | None]] = set()
    rows: list[QuoteVersion] = []
    for version in versions:
        key = (version.quote_request_id, version.quote_id)
        if key in seen:
            continue
        seen.add(key)
        rows.append(version)
    return rows


def build_public_price_range(
    spec_fingerprint: str,
    *,
    versions: Iterable[QuoteVersion] | None = None,
) -> dict | None:
    """Return a pooled price range, or ``None`` when the sample is too thin.

    The returned mapping has no shop identity and no individual price: only
    the aggregate fences and counts.
    """
    spec_fingerprint = (spec_fingerprint or "").strip()
    if not spec_fingerprint:
        return None

    rows = _latest_per_offer(
        versions if versions is not None else _eligible_versions(spec_fingerprint)
    )
    if not rows:
        return None

    priced = [
        (version.buyer_price, version.printer_id)
        for version in rows
        if version.buyer_price is not None and version.printer_id is not None
    ]
    values = [value for value, _shop_id in priced]
    shop_ids = {shop_id for _value, shop_id in priced}

    required_records = min_sample_for_public_range()
    required_shops = max(2, min_shops_for_public_range())
    if len(values) < required_records or len(shop_ids) < required_shops:
        return None

    trimmed = trim_outliers(values)
    if len(trimmed) < 2:
        return None

    # Trimming must not collapse the sample onto one shop: an aggregate built
    # from a single shop's rows would expose that shop's price level.
    trimmed_set = set(trimmed)
    trimmed_shop_ids = {shop_id for value, shop_id in priced if value in trimmed_set}
    if len(trimmed_shop_ids) < required_shops:
        return None

    return {
        "spec_fingerprint": spec_fingerprint,
        "low": _quantise(trimmed[0]),
        "high": _quantise(trimmed[-1]),
        "median": _quantise(_percentile(trimmed, 0.5)),
        "sample_count": len(values),
        "shop_count": len(shop_ids),
        "trimmed_count": len(trimmed),
        "trimmed_shop_count": len(trimmed_shop_ids),
        "currency": rows[0].snapshot.get("currency", "KES")
        if isinstance(rows[0].snapshot, dict)
        else "KES",
    }


def public_price_range_for_request(quote_request) -> dict | None:
    """Range for the request's own spec (convenience wrapper, same service)."""
    from quotes.spec_fingerprint import compute_spec_fingerprint

    return build_public_price_range(compute_spec_fingerprint(quote_request))
