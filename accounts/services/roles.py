"""Reusable role checks for permissions and services."""

from accounts.models import User


def has_role(user: User, *roles: str) -> bool:
    return bool(user and user.is_authenticated and user.role in roles)


def is_client(user: User) -> bool:
    return has_role(user, User.Role.CLIENT)


def is_shop_owner(user: User) -> bool:
    return has_role(user, User.Role.SHOP_OWNER)


def is_staff_member(user: User) -> bool:
    return has_role(user, User.Role.STAFF)


def is_platform_staff(user: User) -> bool:
    return bool(user and user.is_authenticated and user.is_staff)


def get_assignable_roles():
    return {User.Role.CLIENT, User.Role.SHOP_OWNER, User.Role.STAFF}


def set_account_role(user: User, role: str) -> User:
    if role not in get_assignable_roles():
        raise ValueError(f"Unsupported role: {role}")
    if user.role != role:
        user.role = role
        user.save(update_fields=["role", "updated_at"])
    return user


def promote_to_shop_owner(user: User) -> User:
    return set_account_role(user, User.Role.SHOP_OWNER)
