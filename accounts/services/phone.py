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


def set_origin_manager(user, manager):
    """Stamp the manager who created ``user`` as their client origin (LAW 1-3).

    First touch wins: a profile that already has an origin manager is left
    untouched, and a client whose account predates this call can never be
    claimed, because the caller only reaches here for accounts it has just
    created from that manager's invite. Returns the profile.
    """
    from accounts.models import UserProfile

    profile, _ = UserProfile.objects.get_or_create(user=user)
    manager_id = getattr(manager, "pk", None)
    if manager_id is None or profile.origin_manager_id is not None:
        return profile
    if manager_id == getattr(user, "pk", None):
        return profile
    profile.origin_manager_id = manager_id
    profile.save(update_fields=["origin_manager", "updated_at"])
    return profile


def origin_manager_for_phone(normalized: str):
    """The origin manager id recorded for a normalised phone, or None."""
    if not normalized:
        return None
    from accounts.models import UserProfile

    return (
        UserProfile.objects.filter(phone_normalized=normalized, origin_manager__isnull=False)
        .order_by("id")
        .values_list("origin_manager_id", flat=True)
        .first()
    )


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
