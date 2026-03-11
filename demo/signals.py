"""Clear rate card cache when demo models change."""
from django.core.cache import cache
from django.db.models.signals import post_save, post_delete

from .cache_keys import RATE_CARD_CACHE_KEY
from .models import (
    DemoProduct,
    DemoPaper,
    DemoPrintingRate,
    DemoFinishingRate,
    DemoMaterial,
    DemoProductFinishingOption,
)


def _clear_rate_card_cache(*args, **kwargs):
    cache.delete(RATE_CARD_CACHE_KEY)


for model in (
    DemoProduct,
    DemoPaper,
    DemoPrintingRate,
    DemoFinishingRate,
    DemoMaterial,
    DemoProductFinishingOption,
):
    post_save.connect(_clear_rate_card_cache, sender=model)
    post_delete.connect(_clear_rate_card_cache, sender=model)
