"""Automatic payout (Daraja B2C) configuration checks -- Prompt 7 PART 2.

Pure functions with no Django imports, so ``config.settings`` can call them at
import time (production refuses to boot with AUTO_PAYOUTS_ENABLED and missing
B2C settings) and ``jobs.auto_payouts`` can call them again at run time (fail
closed: a misconfigured runner sends nothing).
"""

from __future__ import annotations

TRUTHY = {"1", "true", "yes", "on"}

# Values that are obviously not real credentials.
_PLACEHOLDER_MARKERS = ("change-me", "changeme", "placeholder", "your-", "<", "xxx")


def env_flag(value) -> bool:
    return str(value or "").strip().lower() in TRUTHY


def _missing(value) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def auto_payout_config_errors(values: dict, *, production: bool) -> list[str]:
    """Return what is missing for automatic payouts (empty list = OK).

    ``values`` holds the settings by name. In production every B2C credential,
    both callback URLs (https) and the callback secret are required. Outside
    production the same fields are still checked except the https rule, so a
    sandbox run cannot half-work either.
    """
    errors: list[str] = []
    for name in (
        "MPESA_CONSUMER_KEY",
        "MPESA_CONSUMER_SECRET",
        "MPESA_B2C_SHORTCODE",
        "MPESA_B2C_INITIATOR_NAME",
        "MPESA_B2C_SECURITY_CREDENTIAL",
        "MPESA_B2C_RESULT_URL",
        "MPESA_B2C_TIMEOUT_URL",
    ):
        if _missing(values.get(name)):
            errors.append(f"{name} is required when AUTO_PAYOUTS_ENABLED is on.")
    secret = str(values.get("MPESA_CALLBACK_SECRET") or "").strip()
    if len(secret) < 32:
        errors.append("MPESA_CALLBACK_SECRET (32+ characters) is required when AUTO_PAYOUTS_ENABLED is on.")
    if production:
        for name in ("MPESA_B2C_RESULT_URL", "MPESA_B2C_TIMEOUT_URL"):
            url = str(values.get(name) or "").strip()
            if url and not url.startswith("https://"):
                errors.append(f"{name} must be an https:// URL in production.")
    for name in ("AUTO_PAYOUT_MAX_AMOUNT", "AUTO_PAYOUT_DAILY_CAP"):
        try:
            if float(values.get(name) or 0) <= 0:
                errors.append(f"{name} must be a positive amount.")
        except (TypeError, ValueError):
            errors.append(f"{name} must be a number.")
    return errors
