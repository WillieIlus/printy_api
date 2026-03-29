"""Signals for shops app."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from accounts.models import User
from accounts.services.roles import promote_to_shop_owner, set_account_role
from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, Material, PrintingRate
from services.public_matching import recompute_shop_match_readiness

from .models import OpeningHours, Shop, ShopMembership

DEFAULT_HOURS = [
    (1, "08:00", "18:00", False),  # Mon
    (2, "08:00", "18:00", False),  # Tue
    (3, "08:00", "18:00", False),  # Wed
    (4, "08:00", "18:00", False),  # Thu
    (5, "08:00", "18:00", False),  # Fri
    (6, "", "", True),   # Sat closed
    (7, "", "", True),  # Sun closed
]


@receiver(post_save, sender=Shop)
def create_default_opening_hours(sender, instance, created, **kwargs):
    """Create default OpeningHours when a new shop is created."""
    if created:
        for weekday, from_hour, to_hour, is_closed in DEFAULT_HOURS:
            OpeningHours.objects.create(
                shop=instance,
                weekday=weekday,
                from_hour=from_hour,
                to_hour=to_hour,
                is_closed=is_closed,
            )


@receiver(post_save, sender=Shop)
def promote_shop_owner_role(sender, instance, **kwargs):
    """Owning a shop always upgrades the account to shop_owner."""
    promote_to_shop_owner(instance.owner)


@receiver(post_save, sender=Shop)
def recompute_shop_readiness_after_shop_save(sender, instance, **kwargs):
    """Keep denormalized public matching flags current on shop edits."""
    recompute_shop_match_readiness(instance)


@receiver(post_save, sender=ShopMembership)
def promote_shop_membership_role(sender, instance, **kwargs):
    """Active delegated members are represented as staff accounts."""
    if not instance.is_active:
        return
    if instance.shop.owner_id == instance.user_id:
        promote_to_shop_owner(instance.user)
        return
    if instance.user.role == User.Role.CLIENT:
        set_account_role(instance.user, User.Role.STAFF)


def _related_shop_for_instance(instance):
    if isinstance(instance, Shop):
        return instance
    if hasattr(instance, "shop") and getattr(instance, "shop_id", None):
        return instance.shop
    if hasattr(instance, "machine") and getattr(instance, "machine_id", None):
        return instance.machine.shop
    return None


def _recompute_related_shop(instance):
    shop = _related_shop_for_instance(instance)
    if shop:
        recompute_shop_match_readiness(shop)


@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
@receiver(post_save, sender=Machine)
@receiver(post_delete, sender=Machine)
@receiver(post_save, sender=Paper)
@receiver(post_delete, sender=Paper)
@receiver(post_save, sender=Material)
@receiver(post_delete, sender=Material)
@receiver(post_save, sender=PrintingRate)
@receiver(post_delete, sender=PrintingRate)
@receiver(post_save, sender=FinishingRate)
@receiver(post_delete, sender=FinishingRate)
def recompute_shop_readiness_from_related_models(sender, instance, **kwargs):
    """Refresh public calculator readiness when pricing or catalog setup changes."""
    _recompute_related_shop(instance)
