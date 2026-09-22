import os

from .settings import *  # noqa: F401,F403

SQLITE_DATABASE_NAME = os.environ.get("SQLITE_DATABASE_NAME", "printy_test.sqlite3")


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / SQLITE_DATABASE_NAME,  # noqa: F405
        "TEST": {
            "NAME": BASE_DIR / SQLITE_DATABASE_NAME,  # noqa: F405
        },
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Tests must never run against live Daraja. Override whatever .env says so the
# M-Pesa production guard (printy.E01x) stays quiet and no payment test can be
# misread as moving real money.
MPESA_ENV = "sandbox"

