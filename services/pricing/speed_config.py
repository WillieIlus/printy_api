"""Configuration constants for paid speed options (law 18-19).

Faster is +50% and Fastest is +100% of the job total. Both percentages and the
turnaround factors used to derive rush times from the shop's standard turnaround
live here and can be overridden from Django settings — never hardcoded in views,
templates or the pricing engine.
"""

from __future__ import annotations

from decimal import Decimal
from math import ceil

from django.conf import settings

STANDARD_TIER = "standard"
FASTER_TIER = "faster"
FASTEST_TIER = "fastest"

#: Buyer-facing surcharge percentages (law 18).
FASTER_SURCHARGE_PERCENT = int(getattr(settings, "PRINTY_FASTER_SURCHARGE_PERCENT", 50))
FASTEST_SURCHARGE_PERCENT = int(getattr(settings, "PRINTY_FASTEST_SURCHARGE_PERCENT", 100))

#: Rush turnaround defaults, expressed as a fraction of the shop's standard
#: turnaround (law 19): Faster takes half the standard time, Fastest a quarter.
FASTER_TURNAROUND_FACTOR = Decimal(str(getattr(settings, "PRINTY_FASTER_TURNAROUND_FACTOR", "0.5")))
FASTEST_TURNAROUND_FACTOR = Decimal(str(getattr(settings, "PRINTY_FASTEST_TURNAROUND_FACTOR", "0.25")))

#: Hard floor for any derived rush turnaround, in business hours.
MIN_TURNAROUND_HOURS = int(getattr(settings, "PRINTY_MIN_TURNAROUND_HOURS", 1))

SURCHARGE_PERCENT_BY_TIER: dict[str, int] = {
    STANDARD_TIER: 0,
    FASTER_TIER: FASTER_SURCHARGE_PERCENT,
    FASTEST_TIER: FASTEST_SURCHARGE_PERCENT,
}

TURNAROUND_FACTOR_BY_TIER: dict[str, Decimal] = {
    FASTER_TIER: FASTER_TURNAROUND_FACTOR,
    FASTEST_TIER: FASTEST_TURNAROUND_FACTOR,
}


def multiplier_for_tier(tier_key: str) -> Decimal:
    """The price multiplier implied by the configured surcharge percentage."""
    percent = SURCHARGE_PERCENT_BY_TIER.get(tier_key, 0)
    multiplier = (Decimal("100") + Decimal(percent)) / Decimal("100")
    return multiplier.quantize(Decimal("0.01"))


def surcharge_percent_for_tier(tier_key: str) -> int:
    return SURCHARGE_PERCENT_BY_TIER.get(tier_key, 0)


def derived_turnaround_hours(tier_key: str, standard_hours: int | None) -> int | None:
    """Rush hours derived from the shop's standard turnaround (law 19)."""
    if not standard_hours or standard_hours <= 0:
        return None
    factor = TURNAROUND_FACTOR_BY_TIER.get(tier_key)
    if factor is None:
        return int(standard_hours)
    return max(MIN_TURNAROUND_HOURS, int(ceil(Decimal(standard_hours) * factor)))


__all__ = [
    "STANDARD_TIER",
    "FASTER_TIER",
    "FASTEST_TIER",
    "FASTER_SURCHARGE_PERCENT",
    "FASTEST_SURCHARGE_PERCENT",
    "FASTER_TURNAROUND_FACTOR",
    "FASTEST_TURNAROUND_FACTOR",
    "MIN_TURNAROUND_HOURS",
    "SURCHARGE_PERCENT_BY_TIER",
    "TURNAROUND_FACTOR_BY_TIER",
    "multiplier_for_tier",
    "surcharge_percent_for_tier",
    "derived_turnaround_hours",
]
