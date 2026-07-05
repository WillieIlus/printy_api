"""
Django settings for printy_API project.
Prepared for printy.ke launch and frontend connection.
"""
import os
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

ALLOWED_HOSTS = _get_env_list(
    "ALLOWED_HOSTS",
    default="api.printy.ke,printy.ke,www.printy.ke,178.128.206.240,localhost,127.0.0.1,testserver",
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
    "api",
    "jobs",
    "production",
    "payments",
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

# JWT-only (no SessionAuth): cross-site SPA (printy.ke) to API. No CSRF for API.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "EXCEPTION_HANDLER": "api.exception_handlers.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "1000/hour",
        "user": "1000/hour",
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
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
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

# =============================================================================
# Email
# =============================================================================

EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    # Local default only. Production must set SMTP credentials in .env.
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get(
    "DEFAULT_FROM_EMAIL", "Printy <hello.printyke@gmail.com>"
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL
ADMIN_NOTIFY_EMAIL = os.environ.get("ADMIN_NOTIFY_EMAIL", "hello.printyke@gmail.com")

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000").rstrip("/")

DIRECT_SHOP_STANDARD_MARKUP_RATE = Decimal(_get_env("DIRECT_SHOP_STANDARD_MARKUP_RATE", default="0.20"))

EMAIL_CONFIRMATION_URL = f"{FRONTEND_URL}/auth/confirm-email"
PASSWORD_RESET_URL = f"{FRONTEND_URL}/auth/reset-password"

# =============================================================================
# CSRF & CORS (printy.ke + local dev)
# =============================================================================

# Frontend origins (admin/allauth forms). Configure deployment hosts via env.
CSRF_TRUSTED_ORIGINS = _get_env_list(
    "CSRF_TRUSTED_ORIGINS",
    default="https://printy.ke,https://www.printy.ke,http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000",
)

CORS_ALLOWED_ORIGINS = _get_env_list(
    "CORS_ALLOWED_ORIGINS",
    default="https://printy.ke,https://www.printy.ke,http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173",
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
    "x-requested-with",
]

CORS_EXPOSE_HEADERS = ["authorization", "content-type"]

if not DEBUG:
    if _is_placeholder_secret(SECRET_KEY):
        raise ImproperlyConfigured(
            "SECRET_KEY must be replaced with a real secret when DEBUG=False."
        )
    if not ALLOWED_HOSTS or not _contains_production_host(ALLOWED_HOSTS):
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
MPESA_CALLBACK_URL = _get_env(
    "MPESA_CALLBACK_URL",
    fallback_names=("MPESA_STK_CALLBACK_URL",),
    default="",
)
MPESA_STK_CALLBACK_URL = MPESA_CALLBACK_URL
MPESA_TIMEOUT_URL = _get_env("MPESA_TIMEOUT_URL", default="")
MPESA_RESULT_URL = _get_env("MPESA_RESULT_URL", default="")
MPESA_ACCOUNT_REFERENCE_DEFAULT = _get_env(
    "MPESA_ACCOUNT_REFERENCE_DEFAULT",
    fallback_names=("MPESA_ACCOUNT_REFERENCE",),
    default="PRINTY",
)
MPESA_TRANSACTION_DESC_DEFAULT = _get_env(
    "MPESA_TRANSACTION_DESC_DEFAULT",
    fallback_names=("MPESA_TRANSACTION_DESC",),
    default="Printy payment",
)
QUOTE_EXPIRY_HOURS = int(_get_env("QUOTE_EXPIRY_HOURS", default="48"))
PARTNER_MARKUP_WARNING = Decimal(str(_get_env("PARTNER_MARKUP_WARNING", default="1.00")))
PRINTY_MANAGER_USER_ID = _get_env_int("PRINTY_MANAGER_USER_ID")

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
        "NAME": _get_env("DB_NAME", default="printy_db"),
        "USER": _get_env("DB_USER", default="printy_user"),
        "PASSWORD": _get_env("DB_PASSWORD", default=""),
        "HOST": _get_env("DB_HOST", default="127.0.0.1"),
        "PORT": _get_env("DB_PORT", default="5432"),
    }
}

_log_env_presence("SECRET_KEY", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT")
 



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
