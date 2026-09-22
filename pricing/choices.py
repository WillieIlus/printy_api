"""Choice enums for pricing app."""

from __future__ import annotations

from typing import Any

from django.db import models


COLOR_MODE_ALIASES = {
    # black & white space
    "bw": "BW",
    "b_w": "BW",
    "b&w": "BW",
    "b-w": "BW",
    "black": "BW",
    "black_only": "BW",
    "black_white": "BW",
    "black_and_white": "BW",
    "black-white": "BW",
    "blackwhite": "BW",
    "black&white": "BW",
    "mono": "BW",
    "monochrome": "BW",
    "grayscale": "BW",
    "greyscale": "BW",
    "grey_scale": "BW",
    "single_color": "BW",
    "single_colour": "BW",
    "singlecolor": "BW",
    "singlecolour": "BW",
    "one_color": "BW",
    "one_colour": "BW",
    "1_color": "BW",
    "1color": "BW",
    # full colour space
    "color": "COLOR",
    "colour": "COLOR",
    "full_color": "COLOR",
    "full_colour": "COLOR",
    "fullcolor": "COLOR",
    "fullcolour": "COLOR",
    "full": "COLOR",
    "cmyk": "COLOR",
    "4c": "COLOR",
    "4_color": "COLOR",
    "4color": "COLOR",
    "four_color": "COLOR",
}

SIDES_ALIASES = {
    # simplex / single-sided space
    "simplex": "SIMPLEX",
    "single": "SIMPLEX",
    "single_side": "SIMPLEX",
    "single_sided": "SIMPLEX",
    "single-sided": "SIMPLEX",
    "one_sided": "SIMPLEX",
    "one_side": "SIMPLEX",
    "onesided": "SIMPLEX",
    "1": "SIMPLEX",
    "1_sided": "SIMPLEX",
    "1sided": "SIMPLEX",
    "one": "SIMPLEX",
    # duplex / double-sided space
    "duplex": "DUPLEX",
    "double": "DUPLEX",
    "double_side": "DUPLEX",
    "double_sided": "DUPLEX",
    "double-sided": "DUPLEX",
    "two_sided": "DUPLEX",
    "two_side": "DUPLEX",
    "two-sided": "DUPLEX",
    "both": "DUPLEX",
    "both_sides": "DUPLEX",
    "2": "DUPLEX",
    "2_sided": "DUPLEX",
    "2sided": "DUPLEX",
    "two": "DUPLEX",
}


class ColorMode(models.TextChoices):
    BW = "BW", "Black & White"
    COLOR = "COLOR", "Color"

    @classmethod
    def from_friendly(cls, value: Any) -> str | None:
        """Resolve any accepted spelling (``full_color``, ``black_only``, codes) to ``BW``/``COLOR``."""
        key = _normalize_key(value)
        if key is None:
            return None
        if key in COLOR_MODE_ALIASES:
            return COLOR_MODE_ALIASES[key]
        if any(key == member.value.lower() for member in cls):
            return key.upper()
        return None


class Sides(models.TextChoices):
    SIMPLEX = "SIMPLEX", "Simplex (1-sided)"
    DUPLEX = "DUPLEX", "Duplex (2-sided)"

    @classmethod
    def from_friendly(cls, value: Any) -> str | None:
        """Resolve any accepted spelling (``double``/``single``, codes) to ``SIMPLEX``/``DUPLEX``."""
        key = _normalize_key(value)
        if key is None:
            return None
        if key in SIDES_ALIASES:
            return SIDES_ALIASES[key]
        if any(key == member.value.lower() for member in cls):
            return key.upper()
        return None


def _normalize_key(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("mode") or value.get("sides") or value.get("value")
    if not isinstance(value, str):
        return None
    raw = value.strip().lower().replace(" ", "_").replace("-", "_")
    if not raw:
        return None
    return raw


class ChargeUnit(models.TextChoices):
    PER_PIECE = "PER_PIECE", "Per Piece"
    PER_SIDE = "PER_SIDE", "Per Side"
    PER_SHEET = "PER_SHEET", "Per Sheet"
    PER_SIDE_PER_SHEET = "PER_SIDE_PER_SHEET", "Per Side Per Sheet (Legacy)"
    PER_SQM = "PER_SQM", "Per Square Meter"
    FLAT = "FLAT", "Flat"


class FinishingBillingBasis(models.TextChoices):
    PER_SHEET = "per_sheet", "Per Sheet"
    PER_PIECE = "per_piece", "Per Piece"
    FLAT_PER_JOB = "flat_per_job", "Flat Per Job"
    FLAT_PER_GROUP = "flat_per_group", "Flat Per Group"
    FLAT_PER_LINE = "flat_per_line", "Flat Per Line"


class FinishingSideMode(models.TextChoices):
    IGNORE_SIDES = "ignore_sides", "Ignore Sides"
    PER_SELECTED_SIDE = "per_selected_side", "Per Selected Side"


class FinishingSides(models.TextChoices):
    """Whether finishing applies to one side or both sides."""

    SINGLE = "SINGLE", "Single-sided"
    DOUBLE = "DOUBLE", "Double-sided"
    BOTH = "BOTH", "Both (follows print sides)"


class ServicePricingType(models.TextChoices):
    """How a service charge is calculated."""

    FIXED = "FIXED", "Fixed price"
    TIERED_DISTANCE = "TIERED_DISTANCE", "Distance-based tiers"


class ServiceCode(models.TextChoices):
    """Standard service codes."""

    DESIGN = "DESIGN", "Design"
    DELIVERY = "DELIVERY", "Delivery"
    RUSH = "RUSH", "Rush / Urgent"
    SETUP = "SETUP", "Setup"