"""Shop access helpers shared by views and business services."""

from accounts.services.roles import is_platform_staff
from shops.models import Shop


def get_active_membership(shop: Shop, user):
    return None


def can_manage_shop(shop: Shop, user) -> bool:
    return bool(is_platform_staff(user) or (user and user.is_authenticated and shop.owner_id == user.id))


def can_manage_products(shop: Shop, user) -> bool:
    return can_manage_shop(shop, user)


def can_manage_pricing(shop: Shop, user) -> bool:
    return can_manage_shop(shop, user)


def can_manage_setup(shop: Shop, user) -> bool:
    return can_manage_shop(shop, user)


def can_manage_quotes(shop: Shop, user) -> bool:
    return can_manage_shop(shop, user)
