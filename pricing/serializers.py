"""Read serializers for pricing policy models (staff-only API view access)."""

from rest_framework import serializers

from .models import (
    PlatformFeePolicy,
    QuantityPricingTier,
    SetupCostPolicy,
    ShopRateCardSetup,
    WastePolicy,
)


class PlatformFeePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformFeePolicy
        fields = "__all__"


class WastePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = WastePolicy
        fields = "__all__"


class SetupCostPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SetupCostPolicy
        fields = "__all__"


class QuantityPricingTierSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuantityPricingTier
        fields = "__all__"


class ShopRateCardSetupSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopRateCardSetup
        fields = "__all__"