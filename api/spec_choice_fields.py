"""DRF utility fields that marry the calculator's two vocabularies.

The client-facing calculator speaks the human-friendly label space
(``double``/``single`` sides, ``full_color``/``black_only`` colour) while
the printer rate cards and pricing engine store the canonical technical
codes (``DUPLEX``/``SIMPLEX``, ``COLOR``/``BW``).

These fields accept BOTH spaces on input and normalize to the canonical
codes, so a calculator is always harmonized with the backend rate cards.
Genuinely unknown values still fail with the standard "not a valid choice"
error.

CONTRACT (read before adding any print-sides / colour-mode validation):
* Canonical codes live in ``pricing.choices`` (``Sides``/``ColorMode``),
  which also own the friendly alias maps and ``from_friendly`` resolution.
* Every INBOUND path must run through ``ColorsModeField``/``PrintSidesField``
  (or the enums' ``from_friendly``) — the friendly label space must never be
  rejected as "not a valid choice".
* Model layer (``QuoteItem.sides``/``.color_mode``, ``PrintingRate.color_mode``)
  ONLY ever stores canonical codes. Services that build model rows from raw
  calculator snapshots must canonicalize (see ``_canonical_sides`` /
  ``_canonical_color_mode`` in ``quotes.services_workflow``; regression tests
  in ``quotes/test_quote_item_label_normalization.py``).
* Never introduce a fresh ``serializers.ChoiceField`` for print sides or
  colour mode; use these fields instead.
"""

from rest_framework import serializers

from pricing.choices import ColorMode, Sides
from services.pricing.spec_normalization import normalize_color_mode, normalize_print_sides


class _KeywordChoiceField(serializers.ChoiceField):
    """ChoiceField that normalizes incoming values through a mapper first."""

    def __init__(self, *, choices, to_canonical, **kwargs):
        self.to_canonical = to_canonical
        super().__init__(choices, **kwargs)

    def to_internal_value(self, data):
        canonical = self.to_canonical(data)
        if canonical is not None:
            data = canonical
        return super().to_internal_value(data)


class PrintSidesField(_KeywordChoiceField):
    """Accept ``SIMPLEX``/``DUPLEX`` or ``single``/``double``, store canonical."""

    def __init__(self, **kwargs):
        super().__init__(choices=Sides.choices, to_canonical=normalize_print_sides, **kwargs)


class ColorModeField(_KeywordChoiceField):
    """Accept ``BW``/``COLOR`` or ``black_only``/``full_color``, store canonical."""

    def __init__(self, **kwargs):
        super().__init__(choices=ColorMode.choices, to_canonical=normalize_color_mode, **kwargs)