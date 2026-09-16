"""Canonical finishing slug helpers shared by pricing entry points."""

from __future__ import annotations

from typing import Any

from django.utils.text import slugify

from pricing.models import FinishingRate
from shops.models import Shop


NONE_FINISHING_SLUGS = {"", "none", "no", "no-lamination", "no_lamination"}

LAMINATION_SLUG_MAP = {
    "matt-lamination": "matt-lamination",
    "matte-lamination": "matt-lamination",
    "matt-lamination-double": "matt-lamination",
    "matte-lamination-double": "matt-lamination",
    "gloss-lamination": "gloss-lamination",
    "glossy-lamination": "gloss-lamination",
    "gloss-lamination-double": "gloss-lamination",
    "glossy-lamination-double": "gloss-lamination",
    "soft-touch-lamination": "soft-touch-lamination",
    "softtouch-lamination": "soft-touch-lamination",
    "soft-touch-lamination-double": "soft-touch-lamination",
}


def normalize_finishing_slug(value: Any) -> str:
    slug = slugify(str(value or "").strip()).lower()
    return LAMINATION_SLUG_MAP.get(slug, slug)


def is_empty_finishing(value: Any) -> bool:
    return normalize_finishing_slug(value) in NONE_FINISHING_SLUGS


def resolve_finishing_rate_for_slug(shop: Shop, slug: Any) -> FinishingRate | None:
    requested = normalize_finishing_slug(slug)
    if not requested:
        return None
    for row in FinishingRate.objects.filter(shop=shop, is_active=True).order_by("id"):
        if normalize_finishing_slug(row.slug) == requested:
            return row
    return None
