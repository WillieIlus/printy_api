"""
Shop consistency validators.
Ensures all referenced resources belong to the same shop.
"""
from rest_framework import serializers


def validate_shop_consistency(
    shop,
    *,
    product=None,
    paper=None,
    material=None,
    machine=None,
    finishing_rate=None,
    field_name=None,
):
    """Validate that all given resources belong to the given shop."""
    if not shop:
        return

    checks = [
        (product, "product"),
        (paper, "paper"),
        (material, "material"),
        (machine, "machine"),
        (finishing_rate, "finishing_rate"),
    ]

    for obj, name in checks:
        if obj is None:
            continue
        obj_shop = getattr(obj, "shop", None)
        if obj_shop is None and hasattr(obj, "machine"):
            # PrintingRate: shop via machine
            obj_shop = obj.machine.shop if obj.machine else None
        if obj_shop and obj_shop.id != shop.id:
            raise serializers.ValidationError(
                {field_name or name: f"{name.replace('_', ' ').title()} must belong to the same shop."}
            )
