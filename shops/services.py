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


def shop_rate_card_is_usable(shop: Shop) -> bool:
    """Return whether a shop's live pricing data is usable for matching.

    Read-only companion to :func:`refresh_shop_pricing_ready` (same criteria,
    no write). A printer is "usable" when it has priced paper stock AND an
    active printing rate on an active machine. This is the single definition
    of a complete/usable rate card reused by the confirmation backfill.
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
    return has_paper_stock and has_print_rates


def rate_card_missing_steps(shop: Shop) -> list[dict[str, str]]:
    """Structured list of what a shop still needs before its card is usable.

    Mirrors the exact same live-data criteria as :func:`shop_rate_card_is_usable`
    so owners get told precisely what to add. Empty list means ready to confirm.
    Codes are stable machine strings; titles/hints are plain English.
    """
    from inventory.models import Machine, Paper
    from pricing.models import PrintingRate

    missing: list[dict[str, str]] = []
    if not Machine.objects.filter(shop=shop, is_active=True).exists():
        missing.append(
            {
                "code": "machine",
                "title": "Add your first machine",
                "hint": "Tell us which press you print on so we can price jobs.",
            }
        )
    if not Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0).exists():
        missing.append(
            {
                "code": "paper",
                "title": "Add priced paper stock",
                "hint": "Add at least one paper with a selling price so we can quote jobs.",
            }
        )
    if not PrintingRate.objects.filter(
        machine__shop=shop,
        machine__is_active=True,
        is_active=True,
    ).exists():
        missing.append(
            {
                "code": "printing_rate",
                "title": "Set a printing rate",
                "hint": "Set your per-sheet price so jobs can be matched to you.",
            }
        )
    return missing


def refresh_shop_pricing_ready(shop: Shop) -> bool:
    """Recompute the deprecated `pricing_ready` flag from live canonical data.

    A printer is only really "print ready" when it has priced paper stock AND
    an active printing rate on an active machine. The flag goes stale after
    direct data edits (seed scripts, canonical tables, catalog deactivation),
    so this recompute keeps readouts honest. Matching itself is always
    data-driven regardless of this flag.
    """
    ready = shop_rate_card_is_usable(shop)
    Shop.objects.filter(pk=shop.pk).update(pricing_ready=ready)
    return ready
