"""One phone normalisation utility for the client flow (law 10.4).

The project already normalises every phone to a single Kenyan MSISDN in
``accounts.services.phone``. This module re-exports that utility under the names
the client flow uses, so there is exactly one implementation and no second,
drifting copy.
"""
from __future__ import annotations

from accounts.services.phone import (
    normalize_phone as normalize_client_phone,
    phone_is_linked_elsewhere,
)

__all__ = [
    "normalize_client_phone",
    "phone_is_linked_elsewhere",
]
