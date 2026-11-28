"""
Django settings for printy_API project.
Prepared for printy.ke launch and frontend connection.
"""
import os
import sys
import logging
import importlib.util
from pathlib import Path
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)

logger = logging.getLogger(__name__)

DEBUG = os.environ.get("DEBUG", "false").lower() in ("1", "true", "yes")


def _get_env(name, *, fallback_names=(), default=None, required=False):
    for candidate in (name, *fallback_names):
        value = os.environ.get(candidate)
        if value is not None:
            return value
    if required:
        raise ImproperlyConfigured(f"Set the {name} environment variable.")
    return default


def _get_env_list(name, *, fallback_names=(), default=""):
    value = _get_env(name, fallback_names=fallback_names, default=default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_env_int(name, *, fallback_names=(), default=None):
    value = _get_env(name, fallback_names=fallback_names, default=default)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(f"{name} must be an integer.") from exc


def _env_debug_enabled():
    return os.environ.get("ENV_DEBUG", "false").lower() in ("1", "true", "yes")


def _log_env_presence(*names):
    if not _env_debug_enabled():
        return
    presence = ", ".join(
        f"{name}={'set' if os.environ.get(name) else 'missing'}" for name in names
    )
    logger.warning("Environment variable presence: %s", presence)


LOCAL_HOST_TOKENS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "testserver"}


def _is_placeholder_secret(value):
    if not value:
        return True
    lowered = value.strip().lower()
    return lowered.startswith("replace-with") or lowered in {
        "changeme",
        "change-me",
        "secret",
        "your-secret-key",
    }


def _contains_production_host(values):
    return any(item.lower() not in LOCAL_HOST_TOKENS for item in values)


SECRET_KEY = _get_env(
    "SECRET_KEY",
    fallback_names=("DJANGO_SECRET_KEY",),
    required=True,
)

# Audit #10b: no permissive default outside DEBUG. A deployment must name its
# hosts explicitly (see docs/env_vars.md); the DEBUG=False guard below refuses
# to boot when ALLOWED_HOSTS is unset, empty, "*" or only local names.
_ALLOWED_HOSTS_DEV_DEFAULT = "localhost,127.0.0.1,[::1],testserver"
ALLOWED_HOSTS_FROM_ENV = os.environ.get("ALLOWED_HOSTS") is not None
ALLOWED_HOSTS = _get_env_list(
    "ALLOWED_HOSTS",
    default=_ALLOWED_HOSTS_DEV_DEFAULT if DEBUG else "",
)
# =============================================================================
# Apps
# =============================================================================

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "common",
    "core",
    "accounts",
    "shops",
    "inventory",
    "pricing",
    "catalog",
    "quotes",
    "notifications",
    "clients",
    "api",
    "jobs",
    "production",
    "payments",
    "mpesa_payments",
    "contact",
    "workflow",
]

for provider_app in (
    "allauth.socialaccount.providers.google",
):
    if importlib.util.find_spec(provider_app):
        INSTALLED_APPS.append(provider_app)

AUTH_USER_MODEL = "accounts.User"
SITE_ID = 1

# =============================================================================
# REST Framework
# =============================================================================

# JWT-first (Bearer token) for the SPA (printy.ke); SessionAuthentication is
# added so the DRF browsable interface's Log in / Log out works. CSRF is only
# enforced for requests authenticated via session, never for JWT.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "EXCEPTION_HANDLER": "api.exception_handlers.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": [
        # Audit #10b: keyed by common.client_ip.resolve_client_ip, never by a
        # raw X-Forwarded-For (DRF's stock get_ident / NUM_PROXIES).
        "common.throttling.TrustedAnonRateThrottle",
        "common.throttling.TrustedUserRateThrottle",
        "common.throttling.TrustedScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "1000/hour",
        "user": "1000/hour",
        "mpesa_stk_push": "10/min",
        "contact_submit": "20/hour",
        # Law 7.7: signed-out tracking lookups are limited per client IP and
        # per looked-up code so the endpoint cannot be used to enumerate codes.
        "track_lookup_ip": "60/min",
        "track_lookup_code": "30/min",
        # Law 10.4/10.6: the public client-link surface is rate-limited.
        "client_link_view": "60/hour",
        "client_otp_request": "5/hour",
        "client_otp_verify": "20/hour",
        # Public recommendation preview: payload-keyed and cheap, but still
        # capped so it cannot be used to scrape the manager roster.
        "recommended_managers": "120/hour",
        # Audit #4: credential and account-creation endpoints. Keyed per client
        # IP (trusted-proxy aware, accounts.throttling). Override per scope with
        # THROTTLE_RATE_<SCOPE> env vars, e.g. THROTTLE_RATE_AUTH_LOGIN=20/min.
        "auth_login": os.environ.get("THROTTLE_RATE_AUTH_LOGIN", "10/min"),
        "auth_register": os.environ.get("THROTTLE_RATE_AUTH_REGISTER", "10/hour"),
        "auth_password_reset": os.environ.get("THROTTLE_RATE_AUTH_PASSWORD_RESET", "5/hour"),
        "auth_password_reset_confirm": os.environ.get("THROTTLE_RATE_AUTH_PASSWORD_RESET_CONFIRM", "10/hour"),
        "auth_email_resend": os.environ.get("THROTTLE_RATE_AUTH_EMAIL_RESEND", "5/hour"),
        "auth_google_login": os.environ.get("THROTTLE_RATE_AUTH_GOOGLE_LOGIN", "20/min"),
        "auth_invite_claim": os.environ.get("THROTTLE_RATE_AUTH_INVITE_CLAIM", "10/hour"),
        # Audit #9: signed-out calculator artwork uploads (up to 50MB each).
        "guest_artwork_upload": os.environ.get("THROTTLE_RATE_GUEST_ARTWORK_UPLOAD", "30/hour"),
    },
}

# =============================================================================
# SimpleJWT
# =============================================================================

REFRESH_TOKEN_DAYS = 14

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=REFRESH_TOKEN_DAYS),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "accounts.serializers.CustomTokenObtainPairSerializer",
}

# =============================================================================
# Django Allauth
# =============================================================================

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_EMAIL_VERIFICATION = os.environ.get("ACCOUNT_EMAIL_VERIFICATION", "mandatory")
ACCOUNT_EMAIL_SUBJECT_PREFIX = ""
# Confirmation keys are single-use secrets. A GET (mail-client preview or
# link scanner) must never consume one; the SPA confirms with an explicit API
# POST after the recipient opens the link.
ACCOUNT_CONFIRM_EMAIL_ON_GET = False
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = os.environ.get("ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS", "false").lower() in ("1", "true", "yes")
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "http" if DEBUG else "https"
ACCOUNT_ADAPTER = "accounts.adapters.AccountAdapter"

SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "localhost:8000" if DEBUG else "printy.ke")
SITE_NAME = os.environ.get("SITE_NAME", "Printy")

# OAuth: set GOOGLE_* / GITHUB_* env vars, or use SocialApp in Django admin.
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    },
    "github": {
        "APP": {
            "client_id": os.environ.get("GITHUB_CLIENT_ID", ""),
            "secret": os.environ.get("GITHUB_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["user", "user:email"],
    },
}

# Audit #4: POST /api/auth/social/google/ verifies the ID token's signature,
# issuer and audience with google-auth. GOOGLE_CLIENT_ID (comma-separated for
# several OAuth clients, e.g. web + Android) is the required audience; without
# it the endpoint fails closed with a 503 configuration error.
GOOGLE_CLIENT_IDS = [
    part.strip() for part in os.environ.get("GOOGLE_CLIENT_ID", "").split(",") if part.strip()
]
# Explicit dev/test escape hatch: skip ONLY the audience check when no client id
# is configured. Honoured only while DEBUG is on and APP_ENV is local/dev/test;
# signature, issuer, expiry and email_verified are still enforced.
GOOGLE_OAUTH_DEV_SKIP_AUDIENCE = os.environ.get(
    "GOOGLE_OAUTH_DEV_SKIP_AUDIENCE", "false"
).strip().lower() in ("1", "true", "yes")

# =============================================================================
# Email
# =============================================================================

# Deployment environment label. The default is "production" on purpose: if a
# server boots without an explicit APP_ENV it is treated as production so the
# email-delivery guard below cannot silently fall back to the console backend.
# Local development must set APP_ENV=local (see .env.local.example).
APP_ENV = _get_env("APP_ENV", default="production").strip().lower()

_IS_TESTING_SETTINGS = (
    "test_settings" in os.environ.get("DJANGO_SETTINGS_MODULE", "")
    or sys.argv[1:2] == ["test"]
)
# True only for local dev / test runs. In production this stays False so that a
# console EMAIL_BACKEND is reported as a hard startup error (printy.E001).
EMAIL_IS_LOCAL = DEBUG or _IS_TESTING_SETTINGS or APP_ENV in {"local", "dev", "development"}

# Local default is the console backend so `runserver` prints emails to the
# terminal without needing SMTP credentials. Production MUST override this;
# the guard below fails `manage.py check` if a non-local environment keeps it.
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = 10
# Only used when EMAIL_BACKEND is the filebased backend (local e2e runs).
EMAIL_FILE_PATH = os.environ.get("EMAIL_FILE_PATH", str(BASE_DIR / "sent_emails"))
EMAIL_OUTBOX_AUTO_SEND = os.environ.get("EMAIL_OUTBOX_AUTO_SEND", "true").lower() in ("1", "true", "yes")
# Deferred to a background thread outside local/test, so a slow or blocked SMTP
# host never holds up the request that enqueued the email (e.g. quote accept).
EMAIL_OUTBOX_SEND_ASYNC = os.environ.get(
    "EMAIL_OUTBOX_SEND_ASYNC",
    "false" if EMAIL_IS_LOCAL else "true",
).lower() in ("1", "true", "yes")
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL",
    # Production uses the printy.ke domain (docs/env_vars.md). Override via .env.
    "Printy <hello@printy.ke>",
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL
ADMIN_NOTIFY_EMAIL = os.environ.get("ADMIN_NOTIFY_EMAIL", "hello@printy.ke")


def _evaluate_email_delivery(*, backend, is_local, host, host_user, host_password):
    """Pure evaluation of the email delivery configuration.

    Pure (no settings/DB read) so it is trivially unit-testable and reused by
    the registered system check below. Returns Django check Message objects.
    """
    from django.core.checks import Error, Info

    messages = []
    is_console = backend.endswith("backends.console.EmailBackend")
    is_smtp = "backends.smtp.EmailBackend" in backend

    if is_console:
        if is_local:
            messages.append(
                Info(
                    "Email backend is the console backend; emails are printed to the process "
                    "output and never delivered over SMTP.",
                    hint="Expected for local development. For real delivery set "
                    "EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend.",
                    id="printy.W901",
                )
            )
        else:
            messages.append(
                Error(
                    "EMAIL_BACKEND resolves to the console backend while APP_ENV is not local, "
                    "so emails are printed to the server console and are NEVER delivered. This is "
                    "almost always the default fallback: EMAIL_BACKEND is missing from the "
                    "deployed .env, so SMTP credentials are configured but ignored.",
                    hint="Set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend plus "
                    "EMAIL_HOST, EMAIL_PORT, EMAIL_USE_TLS, EMAIL_HOST_USER and "
                    "EMAIL_HOST_PASSWORD in the deployed .env, then re-run manage.py check. "
                    "See docs/EMAIL_DELIVERY.md.",
                    id="printy.E001",
                )
            )
        return messages

    if is_smtp:
        if not host:
            messages.append(
                Error(
                    "EMAIL_HOST is not set; SMTP delivery cannot work.",
                    hint="Set EMAIL_HOST (e.g. smtp.gmail.com) in .env.",
                    id="printy.E004",
                )
            )
        if not host_user or _is_placeholder_secret(host_user):
            messages.append(
                Error(
                    "EMAIL_HOST_USER is empty or a placeholder; SMTP authentication will fail.",
                    hint="Set a real EMAIL_HOST_USER in .env.",
                    id="printy.E002",
                )
            )
        if not host_password or _is_placeholder_secret(host_password):
            messages.append(
                Error(
                    "EMAIL_HOST_PASSWORD is empty or a placeholder; SMTP authentication will fail.",
                    hint="Set a real EMAIL_HOST_PASSWORD (app password / provider secret) in .env.",
                    id="printy.E003",
                )
            )
    return messages


from django.core import checks as _django_checks


@_django_checks.register(_django_checks.Tags.compatibility)
def check_email_delivery_config(app_configs=None, **kwargs):
    """Fail server startup when emails would silently not be delivered."""
    return _evaluate_email_delivery(
        backend=EMAIL_BACKEND,
        is_local=EMAIL_IS_LOCAL,
        host=EMAIL_HOST,
        host_user=EMAIL_HOST_USER,
        host_password=EMAIL_HOST_PASSWORD,
    )

# Public frontend origin used in every email link (confirmation, password
# reset, client invites). Production is the default so a deployment cannot
# silently email localhost links; local developers may override it in .env.
FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://printy.ke").rstrip("/")

# Lifetime of a manager-created, phone-first client invite (law-neutral helper
# for manager-assisted onboarding; the claim link is single-use).
CLIENT_INVITE_TTL_HOURS = int(os.environ.get("CLIENT_INVITE_TTL_HOURS", "72"))

# Law 10.6: how long a signed single-job client link stays valid.
LINK_EXPIRY_DAYS = int(os.environ.get("LINK_EXPIRY_DAYS", "30"))

# Law 10.4: one-time-code policy. Codes are stored hashed, expire, are limited
# in attempts, and resends are throttled.
OTP_LENGTH = int(os.environ.get("OTP_LENGTH", "6"))
OTP_TTL_MINUTES = int(os.environ.get("OTP_TTL_MINUTES", "10"))
OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", "5"))
OTP_RESEND_COOLDOWN_SECONDS = int(os.environ.get("OTP_RESEND_COOLDOWN_SECONDS", "60"))
# Test seam only — never enable in production: echoes the OTP in the API reply.
CLIENT_OTP_ECHO = os.environ.get("CLIENT_OTP_ECHO", "false").lower() in ("1", "true", "yes")

DIRECT_SHOP_STANDARD_MARKUP_RATE = Decimal(_get_env("DIRECT_SHOP_STANDARD_MARKUP_RATE", default="0.20"))

PASSWORD_RESET_URL = f"{FRONTEND_URL}/auth/reset-password"

# =============================================================================
# CSRF & CORS (printy.ke + local dev)
# =============================================================================

# Frontend origins (admin/allauth forms). Configure deployment hosts via env.
CSRF_TRUSTED_ORIGINS = _get_env_list(
    "CSRF_TRUSTED_ORIGINS",
    default="https://printy.ke,https://www.printy.ke,http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
)

CORS_ALLOWED_ORIGINS = _get_env_list(
    "CORS_ALLOWED_ORIGINS",
    default="https://printy.ke,https://www.printy.ke,http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001,http://localhost:5173,http://127.0.0.1:5173",
)

# JWT in header: no cookies needed for API. Set False for cross-site SPA.
CORS_ALLOW_CREDENTIALS = False

CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "accept-language",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-printy-active-role",
    "x-requested-with",
]

CORS_EXPOSE_HEADERS = ["authorization", "content-type"]

if not DEBUG:
    if _is_placeholder_secret(SECRET_KEY):
        raise ImproperlyConfigured(
            "SECRET_KEY must be replaced with a real secret when DEBUG=False."
        )
    if not ALLOWED_HOSTS_FROM_ENV or not ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS must be set explicitly when DEBUG=False "
            "(comma-separated, e.g. api.printy.ke). There is no default."
        )
    if any(host.strip() in {"*", ".*"} for host in ALLOWED_HOSTS):
        raise ImproperlyConfigured("ALLOWED_HOSTS must not contain a wildcard '*' when DEBUG=False.")
    if not _contains_production_host(ALLOWED_HOSTS):
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS must include the real production hosts when DEBUG=False."
        )
    if not CSRF_TRUSTED_ORIGINS or not all(
        origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS
    ):
        raise ImproperlyConfigured(
            "CSRF_TRUSTED_ORIGINS must contain HTTPS origins when DEBUG=False."
        )
    if not CORS_ALLOWED_ORIGINS or not all(
        origin.startswith("https://") for origin in CORS_ALLOWED_ORIGINS
    ):
        raise ImproperlyConfigured(
            "CORS_ALLOWED_ORIGINS must contain HTTPS origins when DEBUG=False."
        )
    parsed_frontend_url = FRONTEND_URL.lower()
    if not parsed_frontend_url or "localhost" in parsed_frontend_url or "127.0.0.1" in parsed_frontend_url:
        raise ImproperlyConfigured(
            "FRONTEND_URL must point to the real frontend domain when DEBUG=False."
        )
    if not parsed_frontend_url.startswith("https://"):
        raise ImproperlyConfigured(
            "FRONTEND_URL must use HTTPS when DEBUG=False."
        )

if not DEBUG:
    SESSION_COOKIE_SAMESITE = "None"
    CSRF_COOKIE_SAMESITE = "None"
    # These default to True in production but can be set to False in .env
    # during initial IP-based testing before SSL is configured.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "true").lower() in ("1", "true", "yes")
    CSRF_COOKIE_SECURE = os.environ.get("CSRF_COOKIE_SECURE", "true").lower() in ("1", "true", "yes")
    SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "true").lower() in ("1", "true", "yes")
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get("SECURE_HSTS_INCLUDE_SUBDOMAINS", "true").lower() in ("1", "true", "yes")
    SECURE_HSTS_PRELOAD = os.environ.get("SECURE_HSTS_PRELOAD", "false").lower() in ("1", "true", "yes")
    # Trust X-Forwarded-Proto from nginx/reverse proxy
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# =============================================================================
# M-Pesa / Daraja
# =============================================================================

MPESA_ENV = (_get_env("MPESA_ENV", default="sandbox") or "sandbox").lower()
if MPESA_ENV not in {"sandbox", "production"}:
    raise ImproperlyConfigured("MPESA_ENV must be either 'sandbox' or 'production'.")
MPESA_ENVIRONMENT = MPESA_ENV

MPESA_BASE_URL = _get_env(
    "MPESA_BASE_URL",
    default=("https://api.safaricom.co.ke" if MPESA_ENV == "production" else "https://sandbox.safaricom.co.ke"),
).rstrip("/")
MPESA_CONSUMER_KEY = _get_env("MPESA_CONSUMER_KEY", default="")
MPESA_CONSUMER_SECRET = _get_env("MPESA_CONSUMER_SECRET", default="")
MPESA_SHORTCODE = _get_env("MPESA_SHORTCODE", default="")
MPESA_SHORTCODE_TYPE = _get_env("MPESA_SHORTCODE_TYPE", default="paybill")
MPESA_PASSKEY = _get_env("MPESA_PASSKEY", default="")
MPESA_INITIATOR_NAME = _get_env("MPESA_INITIATOR_NAME", default="")
MPESA_INITIATOR_PASSWORD = _get_env("MPESA_INITIATOR_PASSWORD", default="")
MPESA_SECURITY_CREDENTIAL = _get_env("MPESA_SECURITY_CREDENTIAL", default="")
MPESA_TIMEOUT_SECONDS = int(_get_env("MPESA_TIMEOUT_SECONDS", default="30"))

# Stub mode: simulate the outbound Daraja STK call in-process so end-to-end runs
# can reach job completion and payouts without a human authorizing the prompt.
# The flag used to be read in payments.services._is_stub_mode() but was never
# published as a setting, so MPESA_FORCE_STUB=1 silently did nothing. It is
# wired here, and hard-gated: stub mode can never be active with DEBUG=False,
# because that would let a deployment fake real payments.
MPESA_FORCE_STUB = str(_get_env("MPESA_FORCE_STUB", default="") or "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
if MPESA_FORCE_STUB and not DEBUG:
    raise ImproperlyConfigured(
        "MPESA_FORCE_STUB cannot be enabled when DEBUG=False. Stub mode simulates "
        "M-Pesa payments, which would let a production deployment mark real orders "
        "as paid without Daraja. Remove MPESA_FORCE_STUB from the production "
        "environment."
    )
MPESA_CALLBACK_URL = _get_env(
    "MPESA_CALLBACK_URL",
    fallback_names=("MPESA_STK_CALLBACK_URL",),
    default="",
)
MPESA_STK_CALLBACK_URL = MPESA_CALLBACK_URL
# Where Daraja POSTs Transaction Status Query verdicts. Default to the canonical
# callback: the view dispatches on body shape, so one public URL serves both and
# a blank ResultURL would be rejected by Daraja outright.
MPESA_TIMEOUT_URL = _get_env("MPESA_TIMEOUT_URL", default=MPESA_CALLBACK_URL)
MPESA_RESULT_URL = _get_env("MPESA_RESULT_URL", default=MPESA_CALLBACK_URL)

# --- Callback authenticity (audit #1) ----------------------------------------
# Daraja does not sign callbacks. The defence is a high-entropy secret carried
# in the callback URL we register with Daraja (appended as a final path
# segment by payments.callback_security.signed_callback_url), a Safaricom
# source-IP allowlist, and matching every callback to a persisted pending
# transaction. See docs/env_vars.md.
MPESA_CALLBACK_SECRET = (_get_env("MPESA_CALLBACK_SECRET", default="") or "").strip()
# Production (DEBUG=False) fails closed: the secret is mandatory there, both at
# import time (below) and at request time (a missing secret rejects every
# callback with 403 instead of accepting it unauthenticated). Local/test runs
# (DEBUG=True, which MPESA_FORCE_STUB already requires) may omit it.
MPESA_CALLBACK_SECRET_REQUIRED = not DEBUG
# Comma-separated IPs/CIDRs allowed to deliver callbacks. When unset in
# production the published Safaricom Daraja ranges are enforced
# (payments.callback_security.SAFARICOM_CALLBACK_IPS).
MPESA_CALLBACK_ALLOWED_IPS = _get_env_list("MPESA_CALLBACK_ALLOWED_IPS", default="")
# Reverse proxies whose X-Forwarded-For we trust when resolving the callback
# source IP. A raw X-Forwarded-For from any other peer is ignored. Loopback is
# the local nginx in the documented deployment.
MPESA_TRUSTED_PROXIES = _get_env_list("MPESA_TRUSTED_PROXIES", default="127.0.0.1,::1")
# How long an unanswered STK prompt counts as "in flight" and blocks a second
# prompt for the same payment (payments.services.stk_in_flight_window). Older
# prompts with no callback are marked EXPIRED. Audit tier3: this used to be
# read only with getattr(settings, ...), so the environment variable that the
# docs name had no effect. The handset prompt itself lives about 60 seconds,
# so a shorter window would allow two live prompts at once.
_MPESA_STK_IN_FLIGHT_MIN_SECONDS = 60
try:
    MPESA_STK_IN_FLIGHT_SECONDS = int(str(_get_env("MPESA_STK_IN_FLIGHT_SECONDS", default="180")).strip())
except ValueError:
    raise ImproperlyConfigured("MPESA_STK_IN_FLIGHT_SECONDS must be a whole number of seconds.") from None
if MPESA_STK_IN_FLIGHT_SECONDS < _MPESA_STK_IN_FLIGHT_MIN_SECONDS:
    raise ImproperlyConfigured(
        f"MPESA_STK_IN_FLIGHT_SECONDS must be at least {_MPESA_STK_IN_FLIGHT_MIN_SECONDS} "
        "(the handset STK prompt stays open about a minute)."
    )

# Audit #1c: lost-confirmation recovery (payments.recovery, the
# `mpesa_recover_payments` command). Rate limits on Daraja STK Query:
# a pause between consecutive queries, a per-prompt cooldown so frequent cron
# runs never re-query the same CheckoutRequestID, and a lookback after which a
# prompt is no longer queried (the super-admin can still look at it by hand).
def _recovery_number(name, default, cast, minimum):
    raw = str(_get_env(name, default=str(default))).strip()
    try:
        value = cast(raw)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be a number.") from None
    if value < minimum:
        raise ImproperlyConfigured(f"{name} must be at least {minimum}.")
    return value


MPESA_RECOVERY_QUERY_INTERVAL_SECONDS = _recovery_number("MPESA_RECOVERY_QUERY_INTERVAL_SECONDS", 1.0, float, 0)
MPESA_RECOVERY_QUERY_COOLDOWN_SECONDS = _recovery_number("MPESA_RECOVERY_QUERY_COOLDOWN_SECONDS", 60, int, 0)
MPESA_RECOVERY_MAX_AGE_HOURS = _recovery_number("MPESA_RECOVERY_MAX_AGE_HOURS", 24, int, 1)

_MPESA_CALLBACK_SECRET_MIN_LENGTH = 32
if MPESA_CALLBACK_SECRET_REQUIRED and not _IS_TESTING_SETTINGS:
    if _is_placeholder_secret(MPESA_CALLBACK_SECRET) or len(MPESA_CALLBACK_SECRET) < _MPESA_CALLBACK_SECRET_MIN_LENGTH:
        raise ImproperlyConfigured(
            "MPESA_CALLBACK_SECRET must be set to a random value of at least "
            f"{_MPESA_CALLBACK_SECRET_MIN_LENGTH} URL-safe characters when DEBUG=False. "
            "Daraja callbacks are unsigned; without the secret anyone could post a "
            "forged payment confirmation. Generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
        )
# --- Automatic payouts via Daraja B2C (Prompt 7 PART 2) ---------------------
# OFF by default and fail closed. When off, nothing is ever sent to Daraja B2C
# and every payout stays manual (jobs.payout_services). See
# docs/DARAJA_PRODUCTION_CHECKLIST.md "Automatic payouts (B2C)" before enabling.
from config.payout_config import auto_payout_config_errors as _auto_payout_config_errors  # noqa: E402
from config.payout_config import env_flag as _env_flag  # noqa: E402

AUTO_PAYOUTS_ENABLED = _env_flag(_get_env("AUTO_PAYOUTS_ENABLED", default=""))
MPESA_B2C_SHORTCODE = _get_env("MPESA_B2C_SHORTCODE", default="")
MPESA_B2C_INITIATOR_NAME = _get_env("MPESA_B2C_INITIATOR_NAME", default="") or MPESA_INITIATOR_NAME
MPESA_B2C_SECURITY_CREDENTIAL = _get_env("MPESA_B2C_SECURITY_CREDENTIAL", default="") or MPESA_SECURITY_CREDENTIAL
MPESA_B2C_COMMAND_ID = _get_env("MPESA_B2C_COMMAND_ID", default="BusinessPayment")


def _b2c_default_url(leaf):
    base = str(MPESA_CALLBACK_URL or "").strip()
    if base.rstrip("/").endswith("/callback"):
        return base.rstrip("/")[: -len("callback")] + f"b2c/{leaf}/"
    return ""


# Both URLs get MPESA_CALLBACK_SECRET appended as the last path segment when
# handed to Daraja (payments.callback_security.signed_callback_url).
MPESA_B2C_RESULT_URL = _get_env("MPESA_B2C_RESULT_URL", default="") or _b2c_default_url("result")
MPESA_B2C_TIMEOUT_URL = _get_env("MPESA_B2C_TIMEOUT_URL", default="") or _b2c_default_url("timeout")
# Safety limits (whole KES). Above the per-payout max, or past the daily total,
# a payout goes to needs_review instead of being sent.
AUTO_PAYOUT_MAX_AMOUNT = _recovery_number("AUTO_PAYOUT_MAX_AMOUNT", 50000, int, 1)
AUTO_PAYOUT_DAILY_CAP = _recovery_number("AUTO_PAYOUT_DAILY_CAP", 200000, int, 1)
# Bounded retries for definite B2C rejections: attempt N waits
# AUTO_PAYOUT_RETRY_BASE_SECONDS * 2**(N-1); after AUTO_PAYOUT_MAX_ATTEMPTS the
# payout goes to needs_review. A request whose outcome is unknown is never
# retried automatically.
AUTO_PAYOUT_MAX_ATTEMPTS = _recovery_number("AUTO_PAYOUT_MAX_ATTEMPTS", 3, int, 1)
AUTO_PAYOUT_RETRY_BASE_SECONDS = _recovery_number("AUTO_PAYOUT_RETRY_BASE_SECONDS", 300, int, 0)
# A B2C request with no result callback after this long goes to needs_review.
AUTO_PAYOUT_RESULT_TIMEOUT_MINUTES = _recovery_number("AUTO_PAYOUT_RESULT_TIMEOUT_MINUTES", 60, int, 1)


def auto_payout_settings_snapshot():
    return {
        name: globals().get(name)
        for name in (
            "MPESA_CONSUMER_KEY",
            "MPESA_CONSUMER_SECRET",
            "MPESA_B2C_SHORTCODE",
            "MPESA_B2C_INITIATOR_NAME",
            "MPESA_B2C_SECURITY_CREDENTIAL",
            "MPESA_B2C_RESULT_URL",
            "MPESA_B2C_TIMEOUT_URL",
            "MPESA_CALLBACK_SECRET",
            "AUTO_PAYOUT_MAX_AMOUNT",
            "AUTO_PAYOUT_DAILY_CAP",
        )
    }


if AUTO_PAYOUTS_ENABLED and not DEBUG and not _IS_TESTING_SETTINGS:
    _auto_payout_errors = _auto_payout_config_errors(auto_payout_settings_snapshot(), production=True)
    if _auto_payout_errors:
        raise ImproperlyConfigured(
            "AUTO_PAYOUTS_ENABLED is on but automatic payouts are not configured: "
            + " ".join(_auto_payout_errors)
            + " Leave AUTO_PAYOUTS_ENABLED off until every B2C setting is in place."
        )

MPESA_ACCOUNT_REFERENCE_DEFAULT = _get_env(
    "MPESA_ACCOUNT_REFERENCE_DEFAULT",
    fallback_names=("MPESA_ACCOUNT_REFERENCE",),
    default="PRINTY",
)
MPESA_TRANSACTION_DESC_DEFAULT = _get_env(
    "MPESA_TRANSACTION_DESC_DEFAULT",
    fallback_names=("MPESA_TRANSACTION_DESC",),
    # Daraja rejects the entire STK Push request when TransactionDesc is longer
    # than 13 characters, so the default has to fit on its own; the clients
    # clamp it regardless.
    default="Printy Order",
)
QUOTE_EXPIRY_HOURS = int(_get_env("QUOTE_EXPIRY_HOURS", default="48"))
# Law 10.2(a): an open quote version older than this many days without
# acceptance is marked EXPIRED by the expire_quotes task (kept, never deleted).
QUOTE_VALIDITY_DAYS = int(_get_env("QUOTE_VALIDITY_DAYS", default="14"))
# Law 10.3: a public range appears only when both thresholds are met; below
# them the service returns nothing (not a placeholder).
MIN_SAMPLE_FOR_PUBLIC_RANGE = int(
    _get_env("MIN_SAMPLE_FOR_PUBLIC_RANGE", default="5")
)
MIN_SHOPS_FOR_PUBLIC_RANGE = int(
    _get_env("MIN_SHOPS_FOR_PUBLIC_RANGE", default="3")
)
# Minimum completed/decided records before an internal production metric is
# exposed as a value (law: never present weak samples as reliable).
PRODUCTION_ANALYTICS_MIN_SAMPLE = int(_get_env("PRODUCTION_ANALYTICS_MIN_SAMPLE", default="5"))
PARTNER_MARKUP_WARNING = Decimal(str(_get_env("PARTNER_MARKUP_WARNING", default="1.00")))
PRINTY_MANAGER_USER_ID = _get_env_int("PRINTY_MANAGER_USER_ID")
# Public recommendation surface. On-time scores stay hidden until this many
# completed jobs exist, so a new manager never inherits a misleading figure.
PRINTY_MANAGER_ON_TIME_MIN_JOBS = int(
    _get_env("PRINTY_MANAGER_ON_TIME_MIN_JOBS", default="10")
)
# Upload limits for the manager portrait. Enforced by accounts.validators on
# both the staff admin form and the manager's own profile upload.
PRINTY_MANAGER_PHOTO_MAX_BYTES = int(
    _get_env("PRINTY_MANAGER_PHOTO_MAX_BYTES", default=str(5 * 1024 * 1024))
)
PRINTY_MANAGER_PHOTO_MIN_DIMENSION = int(
    _get_env("PRINTY_MANAGER_PHOTO_MIN_DIMENSION", default="400")
)
# The public recommendation response is anonymous and payload-keyed; a short
# cache keeps the homepage snappy without hiding a manager who just went free.
PRINTY_RECOMMENDED_MANAGERS_CACHE_SECONDS = int(
    _get_env("PRINTY_RECOMMENDED_MANAGERS_CACHE_SECONDS", default="20")
)

if MPESA_ENV == "production":
    parsed_callback = MPESA_CALLBACK_URL.lower()
    if not parsed_callback:
        raise ImproperlyConfigured(
            "MPESA_CALLBACK_URL must be set when MPESA_ENV='production'."
        )
    if not parsed_callback.startswith("https://"):
        raise ImproperlyConfigured(
            "MPESA_CALLBACK_URL must use HTTPS when MPESA_ENV='production'."
        )
    if "localhost" in parsed_callback or "127.0.0.1" in parsed_callback:
        raise ImproperlyConfigured(
            "MPESA_CALLBACK_URL cannot point to localhost when MPESA_ENV='production'."
        )


def _evaluate_mpesa_production_config(*, env, consumer_key, consumer_secret, shortcode, passkey, callback_url, product="paybill"):
    """Pure evaluation of Daraja production readiness (unit-testable).

    Mirrors the email guard: a server that boots with MPESA_ENV=production but
    missing/placeholder Daraja credentials fails the system check loudly instead
    of only erroring at the first payment.

    `product` is the Daraja short code type. BuyGoodsOnline apps are authorised
    by the OAuth token alone, so a short code and pass key are not only
    unnecessary there but rejected by Daraja if sent.
    """
    from django.core.checks import Error, Info

    messages = []
    env = (env or "").strip().lower()
    is_production = env == "production"
    product = (product or "paybill").strip().lower()
    credential_free_product = product in {"buygoodsonline", "buy goods online", "buygoods"}

    if not is_production:
        messages.append(
            Info(
                "M-Pesa is configured for the sandbox Daraja environment; no live "
                "money moves.",
                hint="Flip MPESA_ENV=production and set real Daraja credentials to go live.",
                id="printy.W902",
            )
        )

    def _missing_or_placeholder(value):
        return (not value) or _is_placeholder_secret(value)

    if is_production:
        if _missing_or_placeholder(consumer_key):
            messages.append(
                Error(
                    "MPESA_CONSUMER_KEY is missing or a placeholder while "
                    "MPESA_ENV=production.",
                    hint="Set the real production Daraja consumer key (Daraja portal → "
                    "production app) in the deployed .env.",
                    id="printy.E013",
                )
            )
        if _missing_or_placeholder(consumer_secret):
            messages.append(
                Error(
                    "MPESA_CONSUMER_SECRET is missing or a placeholder while "
                    "MPESA_ENV=production.",
                    hint="Set the real production Daraja consumer secret in the deployed .env.",
                    id="printy.E014",
                )
            )
        digits = "".join(ch for ch in str(shortcode or "") if ch.isdigit())
        if credential_free_product:
            if not _missing_or_placeholder(shortcode) or not _missing_or_placeholder(passkey):
                messages.append(
                    Error(
                        f"MPESA_SHORTCODE_TYPE={product} is authenticated by the OAuth token "
                        "alone, but MPESA_SHORTCODE/MPESA_PASSKEY are set. Daraja rejects the "
                        "request when a BuyGoodsOnline app sends a short code or password.",
                        hint="Clear MPESA_SHORTCODE and MPESA_PASSKEY in the deployed .env.",
                        id="printy.E015",
                    )
                )
        else:
            if _missing_or_placeholder(shortcode) or not (5 <= len(digits) <= 11):
                messages.append(
                    Error(
                        "MPESA_SHORTCODE is missing, a placeholder, or not a plausible "
                        "paybill/till number while MPESA_ENV=production.",
                        hint="Set the production paybill (or till) Number registered in "
                        "Daraja, e.g. 174379.",
                        id="printy.E015",
                    )
                )
            if _missing_or_placeholder(passkey):
                messages.append(
                    Error(
                        "MPESA_PASSKEY is missing or a placeholder while MPESA_ENV=production.",
                        hint="Set the production Lipa na M-Pesa Online passkey from the "
                        "Daraja portal in the deployed .env.",
                        id="printy.E016",
                    )
                )
        callback = (callback_url or "").lower()
        if not callback:
            messages.append(
                Error(
                    "MPESA_CALLBACK_URL is not set while MPESA_ENV=production.",
                    hint="Set https://api.printy.ke/api/payments/mpesa/callback/ in .env.",
                    id="printy.E010",
                )
            )
        elif "localhost" in callback or "127.0.0.1" in callback:
            messages.append(
                Error(
                    "MPESA_CALLBACK_URL cannot point to localhost while MPESA_ENV=production.",
                    hint="Safaricom must reach the callback over the public HTTPS endpoint.",
                    id="printy.E012",
                )
            )
        elif not callback.startswith("https://"):
            messages.append(
                Error(
                    "MPESA_CALLBACK_URL must use HTTPS while MPESA_ENV=production.",
                    hint="Set the canonical https://api.printy.ke/api/payments/mpesa/callback/ "
                    "callback in .env.",
                    id="printy.E011",
                )
            )
    return messages


@_django_checks.register(_django_checks.Tags.compatibility)
def check_mpesa_production_config(app_configs=None, **kwargs):
    """Fail server startup when Daraja is aimed at production without real creds."""
    from django.conf import settings as _running_settings

    return _evaluate_mpesa_production_config(
        env=getattr(_running_settings, "MPESA_ENV", ""),
        consumer_key=getattr(_running_settings, "MPESA_CONSUMER_KEY", ""),
        consumer_secret=getattr(_running_settings, "MPESA_CONSUMER_SECRET", ""),
        shortcode=getattr(_running_settings, "MPESA_SHORTCODE", ""),
        passkey=getattr(_running_settings, "MPESA_PASSKEY", ""),
        callback_url=getattr(_running_settings, "MPESA_CALLBACK_URL", ""),
        product=getattr(_running_settings, "MPESA_SHORTCODE_TYPE", "paybill"),
    )


def _evaluate_mpesa_callback_security(*, required, secret, allowed_ips=()):
    """Pure evaluation of the callback-authenticity settings (unit-testable).

    printy.E017: production (secret required) without a strong secret.
    printy.E018: the secret contains characters that are not URL-safe, so it
    cannot be carried in the callback URL path.
    printy.E019: an MPESA_CALLBACK_ALLOWED_IPS entry is not an IP/CIDR.
    """
    import ipaddress
    import re

    from django.core.checks import Error

    messages = []
    secret = (secret or "").strip()
    if required and (_is_placeholder_secret(secret) or len(secret) < _MPESA_CALLBACK_SECRET_MIN_LENGTH):
        messages.append(
            Error(
                "MPESA_CALLBACK_SECRET is missing, a placeholder, or shorter than "
                f"{_MPESA_CALLBACK_SECRET_MIN_LENGTH} characters while DEBUG=False.",
                hint="Set a random URL-safe secret (secrets.token_urlsafe(48)) in the deployed .env.",
                id="printy.E017",
            )
        )
    if secret and not re.fullmatch(r"[A-Za-z0-9_\-]+", secret):
        messages.append(
            Error(
                "MPESA_CALLBACK_SECRET may only contain URL-safe characters [A-Za-z0-9_-].",
                hint="Regenerate it with secrets.token_urlsafe(48).",
                id="printy.E018",
            )
        )
    for entry in allowed_ips or ():
        try:
            ipaddress.ip_network(str(entry).strip(), strict=False)
        except ValueError:
            messages.append(
                Error(
                    f"MPESA_CALLBACK_ALLOWED_IPS entry {entry!r} is not a valid IP address or CIDR.",
                    id="printy.E019",
                )
            )
    return messages


@_django_checks.register(_django_checks.Tags.security)
def check_mpesa_callback_security(app_configs=None, **kwargs):
    """Fail startup when callbacks would be accepted without authentication."""
    from django.conf import settings as _running_settings

    return _evaluate_mpesa_callback_security(
        required=getattr(_running_settings, "MPESA_CALLBACK_SECRET_REQUIRED", False),
        secret=getattr(_running_settings, "MPESA_CALLBACK_SECRET", ""),
        allowed_ips=getattr(_running_settings, "MPESA_CALLBACK_ALLOWED_IPS", ()),
    )

# =============================================================================
# Middleware
# =============================================================================

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "common.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# =============================================================================
# URLs & Templates
# =============================================================================

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# =============================================================================
# Database
# =============================================================================


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _get_env("DB_NAME", default="printy_dev"),
        "USER": _get_env("DB_USER", default="postgres"),
        "PASSWORD": _get_env("DB_PASSWORD", default=""),
        "HOST": _get_env("DB_HOST", default="127.0.0.1"),
        "PORT": _get_env("DB_PORT", default="5432"),
    }
}

_log_env_presence("SECRET_KEY", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT")

# =============================================================================
# Cache (audit #10b)
# =============================================================================
# Throttle counters (every DRF throttle, incl. the client-portal OTP request /
# verify limits), the Daraja OAuth token and cached manager listings live in
# the default cache. With a per-process LocMemCache each gunicorn worker keeps
# its own counters, so every limit is silently multiplied by the worker count
# and a restart resets them. Production therefore needs the shared Redis
# cache; LocMem is only allowed in DEBUG and test runs. (OTP codes, attempt
# counters and action tokens are database rows, already shared.)
REDIS_URL = (_get_env("REDIS_URL", default="") or "").strip()
SHARED_CACHE_REQUIRED = not DEBUG and not _IS_TESTING_SETTINGS
_SHARED_CACHE_BACKENDS = {"django.core.cache.backends.redis.RedisCache"}
if REDIS_URL:
    if not REDIS_URL.startswith(("redis://", "rediss://", "unix://")):
        raise ImproperlyConfigured("REDIS_URL must start with redis://, rediss:// or unix://.")
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": _get_env("CACHE_KEY_PREFIX", default="printy"),
            "TIMEOUT": 300,
        }
    }
elif not SHARED_CACHE_REQUIRED:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "printy-local",
        }
    }
else:
    raise ImproperlyConfigured(
        "REDIS_URL must be set when DEBUG=False: throttles and the Daraja token "
        "cache need a cache shared by every worker (e.g. redis://127.0.0.1:6379/1). "
        "A per-process LocMemCache is only allowed in DEBUG and test runs."
    )


def _evaluate_shared_cache(*, required, caches, num_proxies=None):
    """Pure evaluation of the cache / proxy settings (unit-testable).

    printy.E020: SHARED_CACHE_REQUIRED (DEBUG=False outside tests) with a default cache that is not shared between
    workers (LocMem, dummy, file, database...).
    printy.W903: DRF NUM_PROXIES is set; it is ignored (every throttle uses
    common.client_ip.resolve_client_ip with MPESA_TRUSTED_PROXIES).
    """
    from django.core.checks import Error, Warning

    messages = []
    backend = str(((caches or {}).get("default") or {}).get("BACKEND") or "")
    if required and backend not in _SHARED_CACHE_BACKENDS:
        messages.append(
            Error(
                f"The default cache ({backend or 'unset'}) is not shared between workers while DEBUG=False.",
                hint="Set REDIS_URL so throttles and the Daraja token cache use Redis.",
                id="printy.E020",
            )
        )
    if num_proxies not in (None, ""):
        messages.append(
            Warning(
                "REST_FRAMEWORK NUM_PROXIES / the NUM_PROXIES env var is ignored.",
                hint="Client IPs come from common.client_ip.resolve_client_ip; list your proxies in MPESA_TRUSTED_PROXIES.",
                id="printy.W903",
            )
        )
    return messages


@_django_checks.register(_django_checks.Tags.security)
def check_shared_cache(app_configs=None, **kwargs):
    from django.conf import settings as _running_settings

    return _evaluate_shared_cache(
        required=getattr(_running_settings, "SHARED_CACHE_REQUIRED", False),
        caches=_running_settings.CACHES,
        num_proxies=os.environ.get("NUM_PROXIES") or _running_settings.REST_FRAMEWORK.get("NUM_PROXIES"),
    )
 



# =============================================================================
# Auth & i18n
# =============================================================================

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("en", "English"),
    ("sw", "Kiswahili"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

# =============================================================================
# Static & Media
# =============================================================================

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =============================================================================
# Logging
# =============================================================================

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {asctime} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "loggers": {
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "api": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "payments": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


