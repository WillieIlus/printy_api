"""Gallery API serializers."""
from rest_framework import serializers

from shops.models import Shop

from .models import Product, ProductCategory


class ShopMinimalSerializer(serializers.ModelSerializer):
    """Minimal shop for gallery product (needed for calculate-price)."""

    class Meta:
        model = Shop
        fields = ["id", "name", "slug"]


class ProductCategorySerializer(serializers.ModelSerializer):
    """CRUD for ProductCategory."""

    class Meta:
        model = ProductCategory
        fields = ["id", "shop", "name", "slug", "icon_svg_path", "description"]
        read_only_fields = ["slug"]


class ProductCategoryListSerializer(serializers.ModelSerializer):
    """Minimal for nested display."""

    class Meta:
        model = ProductCategory
        fields = ["id", "name", "slug", "icon_svg_path", "description"]


class ProductSerializer(serializers.ModelSerializer):
    """CRUD for Product."""

    class Meta:
        model = Product
        fields = [
            "id",
            "category",
            "shop",
            "title",
            "slug",
            "description",
            "preview_image",
            "dimensions_label",
            "weight_label",
            "is_popular",
            "is_best_value",
            "is_new",
            "is_active",
        ]
        read_only_fields = ["slug"]


class ProductGallerySerializer(serializers.ModelSerializer):
    """Public gallery product (read-only, for grouped display)."""

    shop = ShopMinimalSerializer(read_only=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "title",
            "slug",
            "description",
            "preview_image",
            "dimensions_label",
            "weight_label",
            "is_popular",
            "is_best_value",
            "is_new",
            "shop",
        ]
