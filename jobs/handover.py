"""Delivery handover codes (Prompt 7).

When the printer marks a job dispatched it records a handover code (e.g.
``K7-4Q``) that travels with the parcel. The buyer completes the job by
entering the same code ("I received it"). Only then is the job ``completed``,
so ``completed`` always means "the buyer confirmed receipt" -- the shop can no
longer complete its own job.

Codes are compared after normalisation (letters and digits only, upper case),
so ``k7 4q`` matches ``K7-4Q``. Wrong guesses are counted; after
``MAX_FAILED_ATTEMPTS`` the code is locked and Printy support must resolve the
job (dispute resolution).
"""

from __future__ import annotations

import hmac
import re

HANDOVER_CODE_MIN_LENGTH = 4
HANDOVER_CODE_MAX_LENGTH = 12
MAX_FAILED_ATTEMPTS = 5
HANDOVER_COMPLETE_MESSAGE = "Your job is complete."


class HandoverCodeError(ValueError):
    """A handover problem the API reports as 400 with a stable ``code``."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


def normalize_handover_code(raw) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()


def validate_new_handover_code(raw) -> str:
    code = normalize_handover_code(raw)
    if not code:
        raise HandoverCodeError(
            "Add the handover code that goes with the parcel before marking it dispatched.",
            code="handover_code_required",
        )
    if not (HANDOVER_CODE_MIN_LENGTH <= len(code) <= HANDOVER_CODE_MAX_LENGTH):
        raise HandoverCodeError(
            f"The handover code must have {HANDOVER_CODE_MIN_LENGTH} to {HANDOVER_CODE_MAX_LENGTH} letters or digits.",
            code="handover_code_invalid",
        )
    return code


def handover_codes_match(stored: str, raw) -> bool:
    entered = normalize_handover_code(raw)
    if not stored or not entered:
        return False
    return hmac.compare_digest(stored.encode(), entered.encode())
