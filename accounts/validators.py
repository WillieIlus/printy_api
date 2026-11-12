"""Validation for manager public-profile photos.

The public recommendation surface shows a manager photo only when the uploaded
file is real, is one of the allowed web formats, and is large enough to render
cleanly. The same validator runs on the staff admin form and on the manager's
own profile upload, so a file cannot bypass it by using a different entry
point.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MIN_DIMENSION = 400


def manager_photo_limits() -> tuple[int, int]:
    """Return (max bytes, min width/height in pixels) from settings."""
    max_bytes = int(getattr(settings, "PRINTY_MANAGER_PHOTO_MAX_BYTES", DEFAULT_MAX_BYTES))
    min_dimension = int(
        getattr(settings, "PRINTY_MANAGER_PHOTO_MIN_DIMENSION", DEFAULT_MIN_DIMENSION)
    )
    return max_bytes, min_dimension


def validate_manager_photo(value) -> None:
    """Validate an uploaded manager photo in place.

    ``ImageField`` model validators receive a ``FieldFile``/``UploadedFile``
    whose file pointer may be anywhere; every check rewinds it afterwards so
    the storage backend still receives the whole file.
    """
    if value in (None, ""):
        return

    name = str(getattr(value, "name", "") or "")
    extension = os.path.splitext(name)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError("Use a JPEG, PNG or WebP photo.")

    content_type = str(getattr(value, "content_type", "") or "").lower()
    if content_type and content_type not in ALLOWED_CONTENT_TYPES:
        raise ValidationError("Use a JPEG, PNG or WebP photo.")

    max_bytes, min_dimension = manager_photo_limits()
    try:
        size = int(getattr(value, "size", 0) or 0)
    except (TypeError, ValueError):
        size = 0
    if size and size > max_bytes:
        raise ValidationError(f"Photo must be {max_bytes // (1024 * 1024)} MB or smaller.")

    try:
        if hasattr(value, "seek"):
            value.seek(0)
        with Image.open(value) as image:
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, TypeError):
        raise ValidationError("Upload a valid image file.") from None
    finally:
        if hasattr(value, "seek"):
            value.seek(0)

    if width < min_dimension or height < min_dimension:
        raise ValidationError(
            f"Photo must be at least {min_dimension}x{min_dimension} pixels."
        )


__all__ = ["ALLOWED_CONTENT_TYPES", "ALLOWED_EXTENSIONS", "manager_photo_limits", "validate_manager_photo"]
