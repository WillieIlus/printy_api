"""Signals for shop readiness and owner role synchronization."""
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from accounts.services.roles import promote_to_shop_owner
from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, PrintingRate

from .models import Shop


def recompute_shop_match_readiness(shop):
    return None


@receiver(post_save, sender=Shop)
def create_default_machine(sender, instance, created, **kwargs):
    """Create a default machine so quoting is not blocked by machine management."""
    if created:
        Machine.objects.create(
            shop=instance,
            name="Primary Digital Press",
            is_active=True,
            max_width_mm=330,
            max_height_mm=488,
        )


@receiver(post_save, sender=Shop)
def promote_shop_owner_role(sender, instance, **kwargs):
    """Owning a shop keeps the account role aligned with shop ownership."""
    promote_to_shop_owner(instance.owner)


@receiver(post_save, sender=Shop)
def recompute_shop_readiness_after_shop_save(sender, instance, **kwargs):
    recompute_shop_match_readiness(instance)


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
@receiver(post_save, sender=PrintingRate)
@receiver(post_delete, sender=PrintingRate)
@receiver(post_save, sender=FinishingRate)
@receiver(post_delete, sender=FinishingRate)
def recompute_shop_readiness_from_related_models(sender, instance, **kwargs):
    _recompute_related_shop(instance)
