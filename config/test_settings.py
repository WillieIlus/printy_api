"""Settings for the test suite (Django runner and pytest).

Audit #10a: this module must import with no ``.env`` file, no environment
variables and ``DEBUG`` unset/False, and must never pick up a real secret.

Everything below the ``os.environ`` block only applies to tests. None of these
values belong in config/settings.py, and none of them is a real credential:
each one is visibly fake ("test-only", "not-a-real-...").
"""
import os

# --- 1. Values that are FORCED, whatever the shell or .env says --------------
# Secrets and payment credentials: a test run must never sign tokens with, or
# call Daraja using, a real key that happens to be exported in the shell or
# present in a developer's .env (load_dotenv uses override=False, so these win).
_FORCED_TEST_ENV = {
    "SECRET_KEY": "test-only-insecure-secret-key-not-for-production-0123456789",
    "MPESA_ENV": "sandbox",
    "MPESA_BASE_URL": "https://sandbox.safaricom.co.ke",
    "MPESA_CONSUMER_KEY": "test-only-fake-consumer-key",
    "MPESA_CONSUMER_SECRET": "test-only-fake-consumer-secret",
    # 174379 is Safaricom's public, documented sandbox shortcode.
    "MPESA_SHORTCODE": "174379",
    "MPESA_SHORTCODE_TYPE": "paybill",
    "MPESA_PASSKEY": "test-only-fake-passkey-not-a-real-daraja-passkey",
    "MPESA_CALLBACK_URL": "https://api.printy.ke/api/payments/mpesa/callback/",
    # >= 32 chars so the DEBUG=False length guard would accept it if it ran.
    "MPESA_CALLBACK_SECRET": "test-only-fake-mpesa-callback-secret-0123456789",
    "MPESA_INITIATOR_NAME": "",
    "MPESA_INITIATOR_PASSWORD": "",
    "MPESA_SECURITY_CREDENTIAL": "",
    # No IP override: the gate falls back to Safaricom's published ranges, as
    # in production. Tests post from payments.testing.DARAJA_TEST_PEER_IP.
    "MPESA_CALLBACK_ALLOWED_IPS": "",
    # Prompt 7 PART 2: automatic payouts are forced OFF and unconfigured for
    # every test run; a test that exercises them opts in with
    # override_settings(**TEST_B2C_SETTINGS) (fake values, defined below).
    "AUTO_PAYOUTS_ENABLED": "",
    "MPESA_B2C_SHORTCODE": "",
    "MPESA_B2C_INITIATOR_NAME": "",
    "MPESA_B2C_SECURITY_CREDENTIAL": "",
    "EMAIL_HOST_USER": "",
    "EMAIL_HOST_PASSWORD": "",
}
for _name, _value in _FORCED_TEST_ENV.items():
    os.environ[_name] = _value
# Derived from MPESA_CALLBACK_URL above unless set; never inherit real ones.
for _name in (
    "DJANGO_SECRET_KEY",
    "MPESA_STK_CALLBACK_URL",
    "MPESA_RESULT_URL",
    "MPESA_TIMEOUT_URL",
    "MPESA_B2C_RESULT_URL",
    "MPESA_B2C_TIMEOUT_URL",
):
    os.environ.pop(_name, None)

# --- 2. Defaults: only used when the variable is not set ---------------------
# Non-secret deployment shape. They are chosen so config/settings.py's
# DEBUG=False guards pass (https origins, a real production host) when nothing
# is configured, but a developer can still override them for one run.
_DEFAULT_TEST_ENV = {
    "APP_ENV": "test",
    "ALLOWED_HOSTS": "api.printy.ke,printy.ke,localhost,127.0.0.1,testserver",
    "CSRF_TRUSTED_ORIGINS": "https://printy.ke,https://www.printy.ke",
    "CORS_ALLOWED_ORIGINS": "https://printy.ke,https://www.printy.ke",
    "FRONTEND_URL": "https://printy.ke",
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    # The test client speaks plain http; with DEBUG=False the base settings
    # would otherwise 301 every request to https.
    "SECURE_SSL_REDIRECT": "false",
}
for _name, _value in _DEFAULT_TEST_ENV.items():
    os.environ.setdefault(_name, _value)

from .settings import *  # noqa: E402,F401,F403

SQLITE_DATABASE_NAME = os.environ.get("SQLITE_DATABASE_NAME", "printy_test.sqlite3")

# Audit #10d: SQLite by default (zero setup). TEST_DB_ENGINE=postgresql runs
# the same suite against PostgreSQL - production's engine - so
# select_for_update() really takes row locks (SQLite ignores it). CI runs
# both. Connection values come from the usual DB_* variables; the defaults
# match the CI service container and contain no secret.
TEST_DB_ENGINE = os.environ.get("TEST_DB_ENGINE", "sqlite").strip().lower()

if TEST_DB_ENGINE in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "printy_ci"),
            "USER": os.environ.get("DB_USER", "postgres"),
            "PASSWORD": os.environ.get("DB_PASSWORD", "postgres"),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "TEST": {"NAME": os.environ.get("DB_TEST_NAME", "test_printy_ci")},
        }
    }
elif TEST_DB_ENGINE == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / SQLITE_DATABASE_NAME,  # noqa: F405
            "TEST": {
                "NAME": BASE_DIR / SQLITE_DATABASE_NAME,  # noqa: F405
            },
        }
    }
else:  # pragma: no cover - configuration error
    raise ImproperlyConfigured(f"TEST_DB_ENGINE must be 'sqlite' or 'postgresql', not {TEST_DB_ENGINE!r}.")  # noqa: F405

# Audit #10b: tests always use an in-process cache, even when a developer has
# REDIS_URL exported; production requires Redis (config/settings.py).
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "printy-test",
    }
}
SHARED_CACHE_REQUIRED = False

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# The minimal test environment has no Django common-passwords list installed.
# Validation is a user-input concern; tests create users programmatically.
AUTH_PASSWORD_VALIDATORS = []

# Tests must never run against live Daraja. Override whatever .env says so the
# M-Pesa production guard (printy.E01x) stays quiet and no payment test can be
# misread as moving real money.
MPESA_ENV = "sandbox"

# Audit #10a: run the callback gate in production (fail-closed) mode whatever
# DEBUG is, so results do not depend on the shell. Callbacks must carry the
# (fake) MPESA_CALLBACK_SECRET above and come from a Safaricom address. Tests
# that need the open dev mode, or a missing secret, use override_settings.
MPESA_CALLBACK_SECRET_REQUIRED = True


# Audit #4: the auth throttles share one in-process cache for the whole run, so
# with production rates (e.g. 10 registrations/hour from 127.0.0.1) unrelated
# tests would start getting 429s. Raise ONLY the auth scopes (and the audit #9
# guest artwork upload scope) here; production
# rates live in config/settings.py and accounts/test_audit_roles_oauth_tenancy.py
# patches them back down to prove each endpoint returns 429 after its limit.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {
        **REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"],  # noqa: F405
        **{
            scope: "100000/min"
            for scope in REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]  # noqa: F405
            if scope.startswith("auth_") or scope == "guest_artwork_upload"
        },
    },
}


# Prompt 7 PART 2: a complete, visibly fake B2C configuration for tests that
# switch automatic payouts on. Never a real credential; Daraja is always mocked.
TEST_B2C_SETTINGS = {
    "AUTO_PAYOUTS_ENABLED": True,
    "MPESA_B2C_SHORTCODE": "600000",
    "MPESA_B2C_INITIATOR_NAME": "test-only-fake-initiator",
    "MPESA_B2C_SECURITY_CREDENTIAL": "test-only-fake-b2c-security-credential",
    "MPESA_B2C_RESULT_URL": "https://api.printy.ke/api/payments/mpesa/b2c/result/",
    "MPESA_B2C_TIMEOUT_URL": "https://api.printy.ke/api/payments/mpesa/b2c/timeout/",
    "AUTO_PAYOUT_MAX_AMOUNT": 50000,
    "AUTO_PAYOUT_DAILY_CAP": 200000,
    "AUTO_PAYOUT_MAX_ATTEMPTS": 3,
    "AUTO_PAYOUT_RETRY_BASE_SECONDS": 300,
    "AUTO_PAYOUT_RESULT_TIMEOUT_MINUTES": 60,
}
