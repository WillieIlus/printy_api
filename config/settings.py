"""
Django settings for printy_API project.
Prepared for printy.ke launch and frontend connection.
"""
import os
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY", "django-insecure-dev-key-change-in-production"
)

DEBUG = os.environ.get("DEBUG", "true").lower() in ("1", "true", "yes")

ALLOWED_HOSTS = os.environ.get(
    "ALLOWED_HOSTS",
    "localhost,127.0.0.1,testserver,printy.ke,www.printy.ke,amazingace00.pythonanywhere.com,willieilus.pythonanywhere.com",
).split(",")

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
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.github",
    "common",
    "core",
    "accounts",
    "shops",
    "inventory",
    "pricing",
    "catalog",
    "quotes",
    "api",
    "feedback",
    "setup",
]

AUTH_USER_MODEL = "accounts.User"
SITE_ID = 1

# =============================================================================
# REST Framework
# =============================================================================

# JWT-only (no SessionAuth): cross-site SPA (Netlify/printy.ke) ↔ API. No CSRF for API.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
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

REFRESH_TOKEN_DAYS = 30

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
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

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@printy.ke")

FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://printy.ke")
EMAIL_CONFIRMATION_URL = f"{FRONTEND_URL}/auth/confirm-email"
PASSWORD_RESET_URL = f"{FRONTEND_URL}/auth/reset-password"

# =============================================================================
# CSRF & CORS (printy.ke + local dev)
# =============================================================================

# Frontend origins (admin/allauth forms). Prune to only what you use.
CSRF_TRUSTED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://printyke.netlify.app",
    "https://printy.ke",
    "https://www.printy.ke",
    "https://amazingace00.pythonanywhere.com",
    "https://willieilus.pythonanywhere.com",
]

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://printyke.netlify.app",
    "https://printy.ke",
    "https://www.printy.ke",
    "https://willieilus.pythonanywhere.com",
]

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
    SESSION_COOKIE_SAMESITE = "None"
    CSRF_COOKIE_SAMESITE = "None"
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    # PythonAnywhere/reverse proxy: trust X-Forwarded-Proto for HTTPS
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# =============================================================================
# M-Pesa
# =============================================================================

MPESA_CONSUMER_KEY = os.environ.get("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.environ.get("MPESA_CONSUMER_SECRET", "")
MPESA_SHORTCODE = os.environ.get("MPESA_SHORTCODE", "")
MPESA_INITIATOR_NAME = os.environ.get("MPESA_INITIATOR_NAME", "")
MPESA_SECURITY_CREDENTIAL = os.environ.get("MPESA_SECURITY_CREDENTIAL", "")
MPESA_TIMEOUT_URL = os.environ.get(
    "MPESA_TIMEOUT_URL", "https://printy.ke/api/mpesa/timeout/"
)
MPESA_RESULT_URL = os.environ.get(
    "MPESA_RESULT_URL", "https://printy.ke/api/mpesa/result/"
)
MPESA_PASSKEY = os.environ.get("MPESA_PASSKEY", "")
MPESA_STK_CALLBACK_URL = os.environ.get(
    "MPESA_STK_CALLBACK_URL",
    "https://printy.ke/api/payments/mpesa/callback/",
)

# =============================================================================
# Subscription
# =============================================================================

FREE_TRIAL_DAYS = 14
DEFAULT_SUBSCRIPTION_PLAN = "STARTER"

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
        "DIRS": [],
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

_db_engine = os.environ.get("DB_ENGINE", "sqlite")
if _db_engine == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.environ.get("DB_NAME", "printshop"),
            "USER": os.environ.get("DB_USER", "printshop_user"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

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

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
