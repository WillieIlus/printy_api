"""Canonical finishing slug helpers shared by pricing entry points.

The same finishing is named differently by the three parties involved:

- the buyer calculator sends option keys ("corner-rounding", "saddle_stitch",
  "binding", "matt-lamination");
- the printer rate card stores stable keys ("round_corner_piece",
  "stitching_booklet", "perfect_binding", "wire_o", "gloss_lamination_double");
- persisted ``FinishingRate`` rows carry ``slugify(key)``
  ("round-corner-piece", "stitching-booklet", ...) and a human name
  ("Round Cornering Piece").

Matching only the normalized slug therefore silently dropped every finishing
whose three names differ, and the whole shop disappeared from the price list.
This module owns ONE matcher: exact slug/name equality first, then token
containment, then finishing family (round corners, lamination, stitching,
binding, cutting, folding, creasing). Capability is derived from what verified,
setup-complete shops actually configured, never from a hardcoded list.
"""

from __future__ import annotations

import re
from typing import Any

from django.utils.text import slugify

from pricing.models import FinishingRate

NONE_FINISHING_SLUGS = {"", "none", "no", "no-lamination", "no_lamination"}

LAMINATION_SLUG_MAP = {
    "matt-lamination": "matt-lamination",
    "matte-lamination": "matt-lamination",
    "matt-lamination-single": "matt-lamination",
    "matte-lamination-single": "matt-lamination",
    "matt-lamination-double": "matt-lamination",
    "matte-lamination-double": "matt-lamination",
    "gloss-lamination": "gloss-lamination",
    "glossy-lamination": "gloss-lamination",
    "gloss-lamination-single": "gloss-lamination",
    "glossy-lamination-single": "gloss-lamination",
    "gloss-lamination-double": "gloss-lamination",
    "glossy-lamination-double": "gloss-lamination",
    "soft-touch-lamination": "soft-touch-lamination",
    "softtouch-lamination": "soft-touch-lamination",
    "soft-touch-lamination-double": "soft-touch-lamination",
    # Bare finish words so a plain "matt"/"gloss" selection resolves to the
    # canonical finishing rate instead of reporting "not configured".
    "matt": "matt-lamination",
    "matte": "matt-lamination",
    "gloss": "gloss-lamination",
    "glossy": "gloss-lamination",
    "soft-touch": "soft-touch-lamination",
    "softtouch": "soft-touch-lamination",
}

# Finishing families. A family groups the buyer vocabulary, the rate-card key and
# the human name of one physical service so they always resolve to each other.
# Tokens are matched against a lowercase, separator-folded string; longer (more
# specific) tokens win, which is what keeps "wire-o" from resolving to
# "perfect binding" merely because both are bindings.
FINISHING_FAMILY_TOKENS: dict[str, tuple[str, ...]] = {
    "round-corners": (
        "round-corner",
        "round corner",
        "rounded-corner",
        "rounded corner",
        "corner-rounding",
        "corner rounding",
        "corner-round",
        "cornering",
    ),
    "lamination": (
        "lamination",
        "laminate",
        "soft-touch",
        "soft touch",
        "potch",
        "uv",
        "matt",
        "matte",
        "gloss",
        "glossy",
    ),
    "stitching": (
        "saddle-stitch",
        "saddle stitch",
        "stitching",
        "stitch",
        "stapling",
        "staple",
        "saddle",
    ),
    "binding": (
        "perfect-binding",
        "perfect binding",
        "perfect-bind",
        "perfect bind",
        "wire-o",
        "wire o",
        "wireo",
        "spiral",
        "comb-binding",
        "comb binding",
        "comb-bind",
        "binding",
        "bind",
    ),
    "cutting": ("cutting", "trimming", "trim", "die-cut", "die cut", "kiss-cut", "kiss cut"),
    "folding": ("folding", "fold"),
    "creasing": ("creasing", "crease", "scoring", "score"),
    "perforation": ("perforation", "perforating", "perforate"),
    "drilling": ("drilling", "drill", "hole-punch", "hole punch"),
}

# Lamination tones are mutually exclusive: a gloss request must never resolve to
# the matt row. Rows that carry no tone (a generic "Lamination" row) still match.
_LAMINATION_TONE_KEYWORDS = {
    "gloss": "gloss",
    "glossy": "gloss",
    "matt": "matt",
    "matte": "matt",
    "soft-touch": "soft-touch",
    "softtouch": "soft-touch",
    "soft touch": "soft-touch",
    "uv": "uv",
    "potch": "potch",
}

_SEPARATOR_RE = re.compile(r"[^a-z0-9]+")


def _canonical_text(value: Any) -> str:
    return _SEPARATOR_RE.sub("-", str(value or "").strip().lower()).strip("-")


def normalize_finishing_slug(value: Any) -> str:
    # slugify() preserves underscores, so "matt_lamination" would miss every
    # hyphenated key in LAMINATION_SLUG_MAP. Fold separators so both wire formats
    # resolve to the same canonical slug.
    text = _canonical_text(value)
    return LAMINATION_SLUG_MAP.get(text, text)


def is_empty_finishing(value: Any) -> bool:
    return normalize_finishing_slug(value) in NONE_FINISHING_SLUGS


def finishing_family(value: Any) -> str | None:
    """Return the physical finishing family for any party's vocabulary."""
    text = normalize_finishing_slug(value)
    if not text:
        return None
    for family, tokens in FINISHING_FAMILY_TOKENS.items():
        for token in tokens:
            if token in text:
                return family
    return None


def is_lamination_finishing(value: Any) -> bool:
    return finishing_family(value) == "lamination"


def lamination_tone(value: Any) -> str | None:
    text = normalize_finishing_slug(value)
    for keyword, tone in _LAMINATION_TONE_KEYWORDS.items():
        if keyword in text:
            return tone
    return None


def _requested_tokens(value: Any) -> set[str]:
    text = normalize_finishing_slug(value)
    return {token for token in text.split("-") if token}


def _match_score(requested: str, candidate: Any) -> int:
    """Score how well ``candidate`` (a shop row slug/name) serves ``requested``.

    4 = exact normalized equality
    3 = every requested token appears in the candidate
    2 = same finishing family (and, for lamination, a compatible tone)
    0 = no match
    """
    candidate_slug = normalize_finishing_slug(candidate)
    if not candidate_slug:
        return 0
    if requested == candidate_slug:
        return 4

    requested_tokens = _requested_tokens(requested)
    candidate_tokens = _requested_tokens(candidate_slug)
    if requested_tokens and requested_tokens <= candidate_tokens:
        return 3

    requested_family = finishing_family(requested)
    candidate_family = finishing_family(candidate_slug)
    if not requested_family or requested_family != candidate_family:
        return 0

    # Lamination is tone-specific. Do not let "gloss" price the "matt" row.
    if requested_family == "lamination":
        requested_tone = lamination_tone(requested)
        candidate_tone = lamination_tone(candidate_slug)
        if requested_tone and candidate_tone and requested_tone != candidate_tone:
            return 0

    return 2


def finishing_row_match_score(requested: Any, row: FinishingRate) -> int:
    requested_slug = normalize_finishing_slug(requested)
    if not requested_slug:
        return 0
    return max(
        _match_score(requested_slug, row.slug),
        _match_score(requested_slug, row.name),
    )


def resolve_finishing_rate_for_slug(shop, slug: Any) -> FinishingRate | None:
    """The ONE finishing resolver: buyer vocabulary -> a shop's configured row."""
    requested = normalize_finishing_slug(slug)
    if not requested or is_empty_finishing(requested):
        return None
    best_row = None
    best_score = 0
    for row in FinishingRate.objects.filter(shop=shop, is_active=True).order_by("id"):
        score = finishing_row_match_score(requested, row)
        if score > best_score:
            best_row = row
            best_score = score
            if score == 4:
                break
    return best_row


def configured_shop_ids():
    """Shops whose configured rate-card rows can actually be matched.

    Every active shop participates in matching (public discovery, manager
    sourcing and confirmed-only find-printer all draw from active shops), so a
    finishing configured on any active shop is real platform capability. A
    confirmed/setup-complete shop is the strongest signal, but treating an
    unconfirmed shop's configured row as "not offered" would silently drop the
    finishing from every quote.
    """
    from shops.models import Shop

    return list(Shop.objects.filter(is_active=True).values_list("id", flat=True))


def platform_finishing_families() -> frozenset[str]:
    """Families at least one active shop actually configured.

    Deliberately uncached: a shop can configure a finishing at any moment, and a
    stale capability set would hide newly configured work behind "not offered".
    """
    families: set[str] = set()
    for row in FinishingRate.objects.filter(
        shop_id__in=configured_shop_ids(), is_active=True
    ).only("slug", "name"):
        family = finishing_family(row.slug) or finishing_family(row.name)
        if family:
            families.add(family)
    return frozenset(families)


def finishing_is_offered_anywhere(slug: Any) -> bool:
    """True when a verified shop configured this finishing family.

    This is how resolver capability is derived: from live rate cards, never from
    a hardcoded product list. When no shop offers a finishing, asking for it must
    not exclude every shop from pricing.
    """
    family = finishing_family(slug)
    if not family:
        return False
    return family in platform_finishing_families()
