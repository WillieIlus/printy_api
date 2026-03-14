"""
Public SEO API serializers — read-only, no auth.
Used for sitemap and dynamic marketplace page data.
"""
from rest_framework import serializers

from catalog.models import ProductCategory
from locations.models import Location
from shops.models import Shop


def _format_date(dt):
    """Return ISO date string for lastmod."""
    if dt:
        return dt.strftime("%Y-%m-%d")
    from django.utils import timezone
    return timezone.now().strftime("%Y-%m-%d")


class SEOLocationSerializer(serializers.ModelSerializer):
    """Minimal location for /api/seo/locations/ list."""

    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = Location
        fields = ["slug", "name", "location_type", "updated_at"]

    def get_updated_at(self, obj):
        return _format_date(obj.updated_at)


class SEOLocationDetailSerializer(serializers.ModelSerializer):
    """Location detail with shops for /api/seo/locations/{slug}/."""

    shops = serializers.SerializerMethodField()

    class Meta:
        model = Location
        fields = [
            "slug",
            "name",
            "location_type",
            "city",
            "county",
            "description",
            "shops",
        ]

    def get_shops(self, obj):
        shops = Shop.objects.filter(
            location=obj,
            is_active=True,
        ).exclude(slug__isnull=True).exclude(slug="").order_by("name")
        return [{"slug": s.slug, "name": s.name} for s in shops]


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
        from catalog.models import Product
        return Product.objects.filter(
            category=obj,
            status="PUBLISHED",
            is_active=True,
            shop__is_active=True,
            shop__pricing_ready=True,
        ).count()


class SEORouteSerializer(serializers.Serializer):
    """Single sitemap route entry for /api/seo/routes/."""

    loc = serializers.CharField()
    lastmod = serializers.CharField()
