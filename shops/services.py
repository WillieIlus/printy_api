"""Shop access helpers shared by views and business services."""

from accounts.services.roles import is_platform_staff
from shops.models import Shop, ShopMembership


def get_active_membership(shop: Shop, user):
    if not user or not user.is_authenticated:
        return None
    return ShopMembership.objects.filter(
        shop=shop,
        user=user,
        is_active=True,
    ).first()


def can_manage_shop(shop: Shop, user) -> bool:
    return bool(
        is_platform_staff(user)
        or (user and user.is_authenticated and (shop.owner_id == user.id or get_active_membership(shop, user)))
    )


def can_manage_products(shop: Shop, user) -> bool:
    membership = get_active_membership(shop, user)
    return bool(
        is_platform_staff(user)
        or (user and user.is_authenticated and (shop.owner_id == user.id or (membership and membership.can_manage_products)))
    )


def can_manage_pricing(shop: Shop, user) -> bool:
    membership = get_active_membership(shop, user)
    return bool(
        is_platform_staff(user)
        or (user and user.is_authenticated and (shop.owner_id == user.id or (membership and membership.can_manage_pricing)))
    )


def can_manage_setup(shop: Shop, user) -> bool:
    membership = get_active_membership(shop, user)
    return bool(
        is_platform_staff(user)
        or (user and user.is_authenticated and (shop.owner_id == user.id or (membership and membership.can_manage_setup)))
    )


def can_manage_quotes(shop: Shop, user) -> bool:
    membership = get_active_membership(shop, user)
    return bool(
        is_platform_staff(user)
        or (user and user.is_authenticated and (shop.owner_id == user.id or (membership and membership.can_manage_quotes)))
    )
