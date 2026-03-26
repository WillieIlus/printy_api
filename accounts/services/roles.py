"""Reusable role checks for permissions and services."""

from accounts.models import User


def has_role(user: User, *roles: str) -> bool:
    return bool(user and user.is_authenticated and user.role in roles)


def is_client(user: User) -> bool:
    return has_role(user, User.Role.CLIENT, User.Role.CUSTOMER_LEGACY)


def is_shop_owner(user: User) -> bool:
    return has_role(user, User.Role.SHOP_OWNER, User.Role.PRINTER_LEGACY)


def is_staff_member(user: User) -> bool:
    return has_role(user, User.Role.STAFF)


def promote_to_shop_owner(user: User) -> User:
    if user.role != User.Role.SHOP_OWNER:
        user.role = User.Role.SHOP_OWNER
        user.save(update_fields=["role", "updated_at"])
    return user
