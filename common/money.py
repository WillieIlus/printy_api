"""The one rounding rule for money in Printy.

Why this module exists
----------------------
M-Pesa STK Push only accepts whole shillings. When a quote total carried cents,
three numbers disagreed: the total shown to the client, the amount actually
charged (silently truncated by ``int()`` at the Daraja call site), and the
amount the callback's mismatch guard expected back. The customer was
under-charged and then the payment hard-failed reconciliation.

The fix is to stop fixing it downstream. Money is rounded to whole KES **once**,
where it is first derived — the canonical split in
``pricing.services.platform_fee_policy.calculate_quote_financials`` — and that
integer is what gets stored, published, displayed, charged and reconciled.

Rules
-----
* ``money()``  — 2 decimal places, ROUND_HALF_UP. For rates, per-unit costs and
  the ``decimal_places=2`` model fields that are not part of the client-facing
  money chain. Never use this for a client total.
* ``whole_kes()`` — whole shillings, ROUND_HALF_UP (2500.50 -> 2501,
  2500.49 -> 2500). This is the rounding point for every client-facing amount.
* ``require_whole_kes()`` — asserts an amount is already whole. Used at the
  Daraja boundary to turn "silently wrong charge" into a loud failure.

Every helper converts through ``str()`` so a float that reached the money path
is quantised from its shortest decimal representation rather than its binary
approximation (``Decimal(0.1)`` is 0.1000000000000000055511151231, whereas
``Decimal(str(0.1))`` is exactly 0.1).

No Django, no models, no imports from other apps: this module is a leaf so any
app (pricing, payments, jobs, quotes, mpesa_payments) can depend on it without
an import cycle.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.0001")
WHOLE_KES_QUANT = Decimal("1")

ROUNDING_MODE_NAME = "ROUND_HALF_UP"


def to_decimal(value: Any) -> Decimal:
    """Coerce anything money-shaped to Decimal without importing binary float error."""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def money(value: Any) -> Decimal:
    """Round to 2dp, ROUND_HALF_UP. Rates and non-client-facing costs only."""
    return to_decimal(value).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def rate(value: Any) -> Decimal:
    """Round to 4dp, ROUND_HALF_UP. Multiples, percentages, commission rates."""
    return to_decimal(value).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)


def whole_kes(value: Any) -> Decimal:
    """Round to whole KES, ROUND_HALF_UP.

    The single rounding point for every client-facing amount. Half-up, never
    truncation and never banker's rounding, so 2500.50 is 2501 for every
    consumer of the value.
    """
    return to_decimal(value).quantize(WHOLE_KES_QUANT, rounding=ROUND_HALF_UP)


def is_whole_kes(value: Any) -> bool:
    """True when the value carries no fractional shilling."""
    return to_decimal(value) == to_decimal(value).to_integral_value()


def require_whole_kes(value: Any, field: str = "amount") -> Decimal:
    """Return the amount as a whole-KES Decimal, or raise if it is not.

    Used at the Daraja boundary. An amount that still has cents at this point
    means the rounding point was bypassed, and sending it would under-charge
    the customer by a silent amount — so fail loudly instead.
    """
    amount = to_decimal(value)
    if not is_whole_kes(amount):
        raise ValueError(
            f"{field} must already be rounded to whole KES before it reaches "
            f"M-Pesa, got {amount}. Round it once at the point it is first "
            f"computed (see pricing.services.platform_fee_policy) — do not "
            f"truncate here."
        )
    return amount.quantize(WHOLE_KES_QUANT, rounding=ROUND_HALF_UP)
