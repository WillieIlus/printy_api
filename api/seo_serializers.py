"""
Public SEO API serializers — read-only, no auth.
Used for sitemap and dynamic marketplace page data.
"""
from rest_framework import serializers

from catalog.models import ProductCategory


def _format_date(dt):
    """Return ISO date string for lastmod."""
    if dt:
        return dt.strftime("%Y-%m-%d")
    from django.utils import timezone
    return timezone.now().strftime("%Y-%m-%d")


class SEOProductSerializer(serializers.ModelSerializer):
    """Minimal product category for /api/seo/products/ list."""

    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = ProductCategory
        fields = ["slug", "name", "updated_at"]

    def get_updated_at(self, obj):
        return _format_date(getattr(obj, "updated_at", None))


class SEOProductDetailSerializer(serializers.ModelSerializer):
    """Product category detail for /api/seo/products/{slug}/."""

    product_count = serializers.SerializerMethodField()

    class Meta:
        model = ProductCategory
        fields = ["slug", "name", "description", "product_count"]

    def get_product_count(self, obj):
        from catalog.services import public_products_queryset

        return public_products_queryset().filter(
            category=obj,
        ).count()


class SEORouteSerializer(serializers.Serializer):
    """Single sitemap route entry for /api/seo/routes/."""

    loc = serializers.CharField()
    lastmod = serializers.CharField()
