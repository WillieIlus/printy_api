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


def refresh_shop_pricing_ready(shop: Shop) -> bool:
    """Recompute the deprecated `pricing_ready` flag from live canonical data.

    A printer is only really "print ready" when it has priced paper stock AND
    an active printing rate on an active machine. The flag goes stale after
    direct data edits (seed scripts, canonical tables, catalog deactivation),
    so this recompute keeps readouts honest. Matching itself is always
    data-driven regardless of this flag.
    """
    from inventory.models import Paper
    from pricing.models import PrintingRate

    has_paper_stock = Paper.objects.filter(
        shop=shop,
        is_active=True,
        selling_price__gt=0,
    ).exists()
    has_print_rates = PrintingRate.objects.filter(
        machine__shop=shop,
        machine__is_active=True,
        is_active=True,
    ).exists()
    ready = has_paper_stock and has_print_rates
    Shop.objects.filter(pk=shop.pk).update(pricing_ready=ready)
    return ready
