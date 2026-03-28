"""Signals for shops app."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from accounts.models import User
from accounts.services.roles import promote_to_shop_owner, set_account_role

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
