"""Normalisation helpers for print specification values.

The calculator and the manager (partner) dashboard speak two spaces for the
same concepts:

- the canonical code space used by the pricing engine and storage
  (``print_sides``: ``SIMPLEX``/``DUPLEX``, ``color_mode``: ``BW``/``COLOR``),
- the human-friendly label space a client-facing calculator uses
  (``single``/``double`` sides, ``black_only``/``full_color`` colour).

API serializers and the production-matching pipeline normalise to the
canonical code space so that whichever space a caller uses, the workflow
prices correctly instead of failing validation or silently finding no rate.

The alias vocabulary lives on the pricing models' choice enums
(``pricing.choices.ColorMode`` and ``pricing.choices.Sides``) so the model
layer itself owns the marriage between friendly names and rate-card codes.
"""

from __future__ import annotations

from typing import Any

from pricing.choices import ColorMode, Sides


def normalize_print_sides(value: Any) -> str | None:
    """Return the canonical ``SIMPLEX``/``DUPLEX`` code, or ``None`` if empty/unknown."""
    return Sides.from_friendly(value)


def normalize_color_mode(value: Any) -> str | None:
    """Return the canonical ``BW``/``COLOR`` code, or ``None`` if empty/unknown."""
    return ColorMode.from_friendly(value)