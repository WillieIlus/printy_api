"""Serializers for shop models."""
from rest_framework import serializers

from .models import (
    Shop, Machine, Paper, PrintingRate, FinishingRate, Material,
    Product, ProductFinishingOption,
)


class ShopSerializer(serializers.ModelSerializer):
    def validate_vat_rate(self, value):
        if value is None:
            return 16
        if value < 0:
            raise serializers.ValidationError("VAT rate must be 0 or greater.")
        if value > 100:
            raise serializers.ValidationError("VAT rate cannot exceed 100.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        is_vat_enabled = attrs.get("is_vat_enabled", getattr(self.instance, "is_vat_enabled", False))
        vat_mode = attrs.get("vat_mode", getattr(self.instance, "vat_mode", None))
        if is_vat_enabled and not vat_mode:
            raise serializers.ValidationError({"vat_mode": "VAT mode is required when VAT is enabled."})
        return attrs

    class Meta:
        model = Shop
        fields = [
            'id',
            'name',
            'owner',
            'is_vat_enabled',
            'vat_rate',
            'vat_mode',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['owner', 'created_at', 'updated_at']


class MachineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Machine
        fields = ['id', 'shop', 'name', 'created_at']


class PaperSerializer(serializers.ModelSerializer):
    class Meta:
        model = Paper
        fields = ['id', 'shop', 'name', 'created_at']


class PrintingRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PrintingRate
        fields = ['id', 'shop', 'name', 'rate', 'created_at']


class FinishingRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FinishingRate
        fields = ['id', 'shop', 'name', 'rate', 'created_at']


class MaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = Material
        fields = ['id', 'shop', 'name', 'unit_price', 'created_at']


class ProductFinishingOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductFinishingOption
        fields = ['id', 'product', 'name', 'created_at']


class ProductSerializer(serializers.ModelSerializer):
    finishing_options = ProductFinishingOptionSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = ['id', 'shop', 'name', 'description', 'finishing_options', 'created_at', 'updated_at']
