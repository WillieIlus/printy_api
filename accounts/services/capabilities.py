"""Capability resolution helpers for additive account evolution."""

from __future__ import annotations

from typing import Iterable

from accounts.models import User
from accounts.services.roles import (
    CANONICAL_PARTNER_ROLE,
    CANONICAL_PRODUCTION_ROLE,
    resolve_user_roles,
)

CAPABILITY_KEYS = (
    "can_manage_clients",
    "can_source_jobs",
    "can_receive_assignments",
    "can_manage_production",
    "can_receive_payouts",
)


def _coerce_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def base_capabilities_for_user(user: User) -> dict[str, bool]:
    """Resolve additive capabilities from the current legacy role shape."""
    if not user or not getattr(user, "is_authenticated", False):
        return {key: False for key in CAPABILITY_KEYS}

    roles = set(resolve_user_roles(user))
    is_partner = CANONICAL_PARTNER_ROLE in roles
    is_production = CANONICAL_PRODUCTION_ROLE in roles
    platform_staff = bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False))

    can_receive_assignments = is_production or platform_staff
    can_manage_production = is_production or platform_staff
    can_receive_payouts = is_partner or is_production or platform_staff
    can_source_jobs = is_partner or is_production or platform_staff
    can_manage_clients = is_partner or is_production or platform_staff

    return {
        "can_manage_clients": can_manage_clients,
        "can_source_jobs": can_source_jobs,
        "can_receive_assignments": can_receive_assignments,
        "can_manage_production": can_manage_production,
        "can_receive_payouts": can_receive_payouts,
    }


def resolve_capabilities(user: User) -> dict[str, bool]:
    """Resolve effective capabilities from legacy role shape plus explicit overrides."""
    capabilities = base_capabilities_for_user(user)
    overrides = getattr(user, "capability_overrides", {}) or {}
    for key in CAPABILITY_KEYS:
        override = _coerce_bool(overrides.get(key))
        if override is not None:
            capabilities[key] = override
    return capabilities


def get_account_capabilities(user: User) -> dict[str, bool]:
    return resolve_capabilities(user)


def has_capability(user: User, capability: str) -> bool:
    return bool(resolve_capabilities(user).get(capability, False))


def enabled_capabilities(user: User) -> list[str]:
    return [key for key, value in resolve_capabilities(user).items() if value]


def normalize_capability_overrides(payload: dict | None) -> dict[str, bool]:
    normalized: dict[str, bool] = {}
    for key in CAPABILITY_KEYS:
        if not payload or key not in payload:
            continue
        value = _coerce_bool(payload.get(key))
        if value is not None:
            normalized[key] = value
    return normalized


def update_capability_overrides(user: User, payload: dict | None) -> User:
    normalized = normalize_capability_overrides(payload)
    if user.capability_overrides != normalized:
        user.capability_overrides = normalized
        user.save(update_fields=["capability_overrides", "updated_at"])
    return user


def capability_keys() -> Iterable[str]:
    return CAPABILITY_KEYS
