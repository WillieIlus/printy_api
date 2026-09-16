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

