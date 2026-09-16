"""Copy into settings.py, then mirror the values in .env

Docs: docs/env_vars.md and docs/DARAJA_PRODUCTION_CHECKLIST.md
"""

# ── M-Pesa / Daraja ──────────────────────────────────────────
MPESA_ENV = env("MPESA_ENV", default="sandbox")            # "production" when live
# MPESA_BASE_URL is optional — derived from MPESA_ENV when unset.
MPESA_BASE_URL = env("MPESA_BASE_URL", default="")
MPESA_CONSUMER_KEY = env("MPESA_CONSUMER_KEY", default="")
MPESA_CONSUMER_SECRET = env("MPESA_CONSUMER_SECRET", default="")
MPESA_SHORTCODE = env("MPESA_SHORTCODE", default="")
MPESA_PASSKEY = env("MPESA_PASSKEY", default="")

# Must be publicly reachable over HTTPS in production.
MPESA_CALLBACK_URL = env(
    "MPESA_CALLBACK_URL",
    default="https://api.printy.ke/api/payments/mpesa/callback/",
)

MPESA_TIMEOUT_SECONDS = env.int("MPESA_TIMEOUT_SECONDS", default=30)
MPESA_ACCOUNT_REFERENCE_DEFAULT = env("MPESA_ACCOUNT_REFERENCE_DEFAULT", default="PRINTY")
MPESA_TRANSACTION_DESC_DEFAULT = env("MPESA_TRANSACTION_DESC_DEFAULT", default="Printy payment")

# ── Throttling ───────────────────────────────────────────────
# Add "mpesa_stk_push" to your DEFAULT_THROTTLE_RATES.
#   "DEFAULT_THROTTLE_RATES": {
#       "mpesa_stk_push": "10/min",
#       ...
#   }
