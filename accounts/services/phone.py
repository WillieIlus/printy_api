"""Phone helper: normalisation and persistence for phone-first accounts."""
from __future__ import annotations

from django.utils import timezone
from rest_framework import serializers

from payments.services import normalize_mpesa_phone


def normalize_phone(value) -> str:
    """Normalise a Kenyan phone number to 2547XXXXXXXX.

    Raises ``rest_framework.serializers.ValidationError`` with a friendly
    message when the value is not a usable Kenyan MSISDN.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return normalize_mpesa_phone(raw)
    except Exception:
        raise serializers.ValidationError(
            "Enter a valid Kenyan phone number, for example 2547XXXXXXXX."
        )


def phone_is_linked_elsewhere(normalized: str, *, exclude_user=None) -> bool:
    """True when a verified account already owns this normalised phone."""
    if not normalized:
        return False
    from accounts.models import UserProfile

    queryset = UserProfile.objects.filter(
        phone_normalized=normalized,
        phone_verified_at__isnull=False,
    )
    if exclude_user is not None:
        queryset = queryset.exclude(user=exclude_user)
    return queryset.exists()


def set_profile_phone(user, value, *, verified: bool = False):
    """Store the raw phone plus its normalised form on the user profile."""
    from accounts.models import UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=user)
    raw = str(value or "").strip()
    normalized = normalize_phone(raw) if raw else ""
    profile.phone = raw
    profile.phone_normalized = normalized
    if verified and normalized:
        profile.phone_verified_at = profile.phone_verified_at or timezone.now()
    profile.save(update_fields=["phone", "phone_normalized", "phone_verified_at", "updated_at"])
    return profile
