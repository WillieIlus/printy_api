from __future__ import annotations

from django.conf import settings

from accounts.models import User, UserProfile


HOUSE_BROKER_EMAIL = "house-broker@printy.ke"
HOUSE_BROKER_NAME = "Printy House Broker"


def is_system_account(user: User | None) -> bool:
    if user is None:
        return False
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return False
    return bool(profile.is_system_account)


def get_printy_manager_user() -> User | None:
    configured_id = getattr(settings, "PRINTY_MANAGER_USER_ID", None)
    if configured_id:
        user = User.objects.filter(pk=configured_id, is_active=True).select_related("profile").first()
        if user is not None:
            return user
    return (
        User.objects.filter(is_active=True, profile__is_system_account=True)
        .select_related("profile")
        .order_by("id")
        .first()
    )


def get_house_broker_user() -> User | None:
    configured_id = getattr(settings, "PRINTY_HOUSE_BROKER_USER_ID", None)
    if configured_id:
        user = User.objects.filter(pk=configured_id, is_active=True).select_related("profile").first()
        if user is not None:
            return user
    return (
        User.objects.filter(
            email__iexact=HOUSE_BROKER_EMAIL,
            is_active=True,
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        .select_related("profile")
        .first()
    )


def ensure_printy_manager_user(*, email: str = "ops@printy.ke") -> tuple[User, UserProfile, bool]:
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "name": "Printy",
            "role": User.Role.PARTNER,
            "is_staff": True,
            "is_active": True,
            "partner_profile_enabled": True,
        },
    )
    updated_fields: list[str] = []
    if not user.name:
        user.name = "Printy"
        updated_fields.append("name")
    if user.role != User.Role.PARTNER:
        user.role = User.Role.PARTNER
        updated_fields.append("role")
    if not user.is_staff:
        user.is_staff = True
        updated_fields.append("is_staff")
    if not user.is_active:
        user.is_active = True
        updated_fields.append("is_active")
    if not user.partner_profile_enabled:
        user.partner_profile_enabled = True
        updated_fields.append("partner_profile_enabled")
    if created:
        user.set_unusable_password()
        updated_fields.append("password")
    if updated_fields:
        user.save(update_fields=[*updated_fields, "updated_at"])

    profile, _ = UserProfile.objects.get_or_create(
        user=user,
        defaults={
            "is_system_account": True,
        },
    )
    profile_updates: list[str] = []
    if not profile.is_system_account:
        profile.is_system_account = True
        profile_updates.append("is_system_account")
    if profile_updates:
        profile.save(update_fields=[*profile_updates, "updated_at"])

    return user, profile, created


def ensure_house_broker_user(*, email: str = HOUSE_BROKER_EMAIL) -> tuple[User, UserProfile, bool]:
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "name": HOUSE_BROKER_NAME,
            "role": User.Role.PARTNER,
            "is_active": True,
            "partner_profile_enabled": True,
        },
    )
    updated_fields: list[str] = []
    if user.name != HOUSE_BROKER_NAME:
        user.name = HOUSE_BROKER_NAME
        updated_fields.append("name")
    if user.role != User.Role.PARTNER:
        user.role = User.Role.PARTNER
        updated_fields.append("role")
    if user.is_staff:
        user.is_staff = False
        updated_fields.append("is_staff")
    if user.is_superuser:
        user.is_superuser = False
        updated_fields.append("is_superuser")
    if not user.is_active:
        user.is_active = True
        updated_fields.append("is_active")
    if not user.partner_profile_enabled:
        user.partner_profile_enabled = True
        updated_fields.append("partner_profile_enabled")
    if created:
        user.set_unusable_password()
        updated_fields.append("password")
    if updated_fields:
        user.save(update_fields=[*updated_fields, "updated_at"])

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile_updates: list[str] = []
    if not profile.is_system_account:
        profile.is_system_account = True
        profile_updates.append("is_system_account")
    if not profile.broker_profile_active:
        profile.broker_profile_active = True
        profile_updates.append("broker_profile_active")
    if profile_updates:
        profile.save(update_fields=[*profile_updates, "updated_at"])

    return user, profile, created
