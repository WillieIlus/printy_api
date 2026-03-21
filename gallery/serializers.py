"""Gallery API serializers — use catalog.Product and catalog.ProductCategory."""
from rest_framework import serializers

from shops.models import Shop

from catalog.models import Product, ProductCategory


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
    """CRUD for gallery product (catalog.Product with gallery fields)."""

    title = serializers.CharField(source="name", read_only=True)
    preview_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "category",
            "shop",
            "title",
            "name",
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
        read_only_fields = ["shop", "slug"]

    def get_preview_image(self, obj):
        img = obj.get_primary_image()
        return img.image.url if img and img.image else None


class ProductGallerySerializer(serializers.ModelSerializer):
    """Public gallery product (read-only, for grouped display)."""

    shop = ShopMinimalSerializer(read_only=True)
    title = serializers.CharField(source="name", read_only=True)
    preview_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "title",
            "name",
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

    def get_preview_image(self, obj):
        img = obj.get_primary_image()
        return img.image.url if img and img.image else None
