"""The one specification fingerprint used by every law 10.2 dataset.

Law 10.2 requires one fingerprint of
``(product, size, quantity band, paper, gsm, sides, colours, finishing)`` and
law 10.3 requires public ranges to be pooled by that same key. Everything that
stores or compares a spec (quote versions, transactions, completion actuals,
public ranges) funnels through :func:`spec_fingerprint` here -- there is no
second fingerprint implementation.

The fingerprint is a SHA-256 of a canonical JSON payload. It is deliberately
built from comparable paper/finishing attributes (type, category, sheet size,
name) rather than shop-owned row ids, so the same physical spec quoted by two
different shops lands in the same public-range bucket.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

#: Quantity is banded so small differences (100 vs 120 copies) pool together.
#: The bands and their labels live here only.
QUANTITY_BANDS: tuple[tuple[int, int | None, str], ...] = (
    (1, 99, "1-99"),
    (100, 499, "100-499"),
    (500, 999, "500-999"),
    (1_000, 4_999, "1000-4999"),
    (5_000, 9_999, "5000-9999"),
    (10_000, None, "10000+"),
)


def quantity_band(quantity: Any) -> str:
    """Return the stable quantity-band label for a quantity."""
    try:
        value = int(quantity)
    except (TypeError, ValueError):
        return _normalise(quantity)
    if value <= 0:
        return "under-1"
    for low, high, label in QUANTITY_BANDS:
        if value >= low and (high is None or value <= high):
            return label
    return "10000+"


def _normalise(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).strip().lower().split())


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _hash(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def spec_fingerprint(
    *,
    product: Any,
    size: Any,
    quantity_band: Any,
    paper: Any,
    gsm: Any,
    sides: Any,
    colours: Any,
    finishing: Iterable[Any],
) -> str:
    """Fingerprint exactly the eight law 10.2 spec dimensions.

    ``finishing`` is an iterable of finishing labels; order is irrelevant.
    """
    payload = {
        "product": _normalise(product),
        "size": _normalise(size),
        "quantity_band": _normalise(quantity_band),
        "paper": _normalise(paper),
        "gsm": _normalise(gsm),
        "sides": _normalise(sides),
        "colours": _normalise(colours),
        "finishing": sorted(_normalise(item) for item in finishing if _normalise(item)),
    }
    return _hash(payload)[:32]


def _snapshot_lookup(item, *keys: str) -> Any:
    """Best-effort lookup of a spec value in a frozen item snapshot."""
    snapshot = getattr(item, "item_spec_snapshot", None)
    if not isinstance(snapshot, dict):
        return None
    sources = [snapshot]
    for nested_key in ("calculator_inputs", "request_details", "custom_product_snapshot"):
        nested = snapshot.get(nested_key)
        if isinstance(nested, dict):
            sources.append(nested)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    return None


def product_label(item) -> str:
    """Human/stable product label for one item (used by accuracy groupings)."""
    product = getattr(item, "product", None)
    if product is not None:
        return getattr(product, "name", "") or getattr(product, "slug", "")
    return (
        getattr(item, "title", "")
        or _snapshot_lookup(item, "product_name", "product_type", "product")
        or getattr(item, "item_type", "")
    )


def _product_label(item) -> str:
    # Product identity is part of the fingerprint; keep it comparable across
    # shops by preferring the shared catalog slug.
    product = getattr(item, "product", None)
    if product is not None:
        return getattr(product, "slug", "") or getattr(product, "name", "")
    return product_label(item)


def _size_label(item) -> str:
    width = getattr(item, "chosen_width_mm", None)
    height = getattr(item, "chosen_height_mm", None)
    if width and height:
        return f"{int(width)}x{int(height)}"
    product = getattr(item, "product", None)
    if product is not None:
        width = getattr(product, "default_finished_width_mm", None)
        height = getattr(product, "default_finished_height_mm", None)
        if width and height:
            return f"{int(width)}x{int(height)}"
    return _snapshot_lookup(item, "size", "finished_size", "dimensions_label") or ""


def _paper_label(item) -> str:
    paper = getattr(item, "paper", None)
    if paper is not None:
        parts = (
            getattr(paper, "paper_type", ""),
            getattr(paper, "category", ""),
            getattr(paper, "sheet_size", ""),
        )
        label = "|".join(part for part in (_normalise(part) for part in parts) if part)
        if label:
            return label
    return (
        _snapshot_lookup(item, "paper_type", "paper", "paper_name", "material")
        or ""
    )


def _gsm_value(item) -> Any:
    paper = getattr(item, "paper", None)
    if paper is not None and getattr(paper, "gsm", None) is not None:
        return paper.gsm
    return _snapshot_lookup(item, "paper_gsm", "gsm", "requested_gsm")


def _finishing_labels(item) -> list[str]:
    labels: list[str] = []
    if getattr(item, "pk", None):
        try:
            rows = item.finishings.select_related("finishing_rate").all()
        except (AttributeError, ValueError):
            rows = []
        for row in rows:
            rate = getattr(row, "finishing_rate", None)
            if rate is None:
                continue
            labels.append(
                getattr(rate, "slug", "") or getattr(rate, "name", "")
            )
    if labels:
        return labels

    snapshot = getattr(item, "item_spec_snapshot", None)
    if isinstance(snapshot, dict):
        for row in snapshot.get("finishings") or []:
            if isinstance(row, dict):
                labels.append(
                    row.get("finishing_rate_id")
                    or row.get("slug")
                    or row.get("name")
                    or ""
                )
            else:
                labels.append(row)
    return [label for label in labels if label not in (None, "")]


def item_spec_components(item) -> dict[str, Any]:
    """Return the eight law 10.2 dimensions for one quote item."""
    return {
        "product": _product_label(item),
        "size": _size_label(item),
        "quantity_band": quantity_band(getattr(item, "quantity", None)),
        "paper": _paper_label(item),
        "gsm": _gsm_value(item),
        "sides": getattr(item, "sides", ""),
        "colours": getattr(item, "color_mode", ""),
        "finishing": _finishing_labels(item),
    }


def item_spec_fingerprint(item) -> str:
    """Fingerprint one quote item through the one canonical function."""
    components = item_spec_components(item)
    return spec_fingerprint(
        product=components["product"],
        size=components["size"],
        quantity_band=components["quantity_band"],
        paper=components["paper"],
        gsm=components["gsm"],
        sides=components["sides"],
        colours=components["colours"],
        finishing=components["finishing"],
    )


def _request_spec(quote_request) -> Any:
    snapshot = getattr(quote_request, "request_snapshot", None)
    if not isinstance(snapshot, dict):
        return None
    for key in ("matched_specs", "specification", "spec"):
        value = snapshot.get(key)
        if value:
            return value
    return None


def compute_spec_fingerprint(quote_request) -> str:
    """Fingerprint a whole request (one or more items).

    The request-level spec summary is included as well so a change to the
    buyer's stated spec is captured even before item rows are created.
    """
    items = []
    if getattr(quote_request, "pk", None):
        for item in quote_request.items.all().order_by("pk"):
            items.append(item_spec_components(item))
    payload = {
        "items": sorted(items, key=_canonical),
        "request_spec": _request_spec(quote_request),
    }
    return _hash(payload)[:32]
