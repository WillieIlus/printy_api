"""
Public SEO API — read-only endpoints for sitemap and dynamic page generation.
No auth required. Returns canonical URLs and minimal metadata.
"""
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Product, ProductCategory
from locations.models import Location
from shops.models import Shop

from .seo_serializers import (
    SEOLocationDetailSerializer,
    SEOLocationSerializer,
    SEOProductDetailSerializer,
    SEOProductSerializer,
)


def _format_date(dt):
    """Return ISO date string for lastmod."""
    if dt:
        return dt.strftime("%Y-%m-%d")
    return timezone.now().strftime("%Y-%m-%d")


class SEOLocationsView(APIView):
    """GET /api/seo/locations/ — all active locations for sitemap."""

    permission_classes = [AllowAny]

    def get(self, request):
        locations = Location.objects.filter(is_active=True).order_by("slug")
        serializer = SEOLocationSerializer(locations, many=True)
        return Response(serializer.data)


class SEOProductsView(APIView):
    """GET /api/seo/products/ — all active global product categories for sitemap."""

    permission_classes = [AllowAny]

    def get(self, request):
        categories = ProductCategory.objects.filter(
            shop__isnull=True, is_active=True
        ).order_by("slug")
        serializer = SEOProductSerializer(categories, many=True)
        return Response(serializer.data)


class SEORoutesView(APIView):
    """
    GET /api/seo/routes/ — canonical URLs for sitemap.
    Returns: [{ loc, lastmod }, ...]
    """

    permission_classes = [AllowAny]

    def get(self, request):
        today = _format_date(timezone.now())
        routes = []

        # Homepage
        routes.append({"loc": "/", "lastmod": today})

        # Static hub pages
        routes.append({"loc": "/locations", "lastmod": today})
        routes.append({"loc": "/products", "lastmod": today})
        routes.append({"loc": "/shops", "lastmod": today})
        routes.append({"loc": "/gallery", "lastmod": today})

        # Location pages: /locations/[slug]
        for loc in Location.objects.filter(is_active=True).order_by("slug"):
            routes.append({
                "loc": f"/locations/{loc.slug}",
                "lastmod": _format_date(loc.updated_at) or today,
            })

        # Product category pages: /products/[slug]
        categories = ProductCategory.objects.filter(
            shop__isnull=True, is_active=True
        ).order_by("slug")
        for cat in categories:
            routes.append({
                "loc": f"/products/{cat.slug}",
                "lastmod": today,
            })

        # Location + product pages: /locations/[location]/products/[product]
        for loc in Location.objects.filter(is_active=True).order_by("slug"):
            # Shops in this location that have products
            shop_ids = set(
                Shop.objects.filter(
                    location=loc, is_active=True, pricing_ready=True
                ).values_list("id", flat=True)
            )
            if not shop_ids:
                continue
            # Product categories that have products from these shops
            cat_ids = set(
                Product.objects.filter(
                    shop_id__in=shop_ids,
                    status="PUBLISHED",
                    is_active=True,
                    category__isnull=False,
                    category__shop__isnull=True,
                    category__is_active=True,
                )
                .values_list("category_id", flat=True)
                .distinct()
            )
            for cat in ProductCategory.objects.filter(id__in=cat_ids):
                routes.append({
                    "loc": f"/locations/{loc.slug}/products/{cat.slug}",
                    "lastmod": today,
                })

        # Shop pages: /shops/[slug]
        for shop in Shop.objects.filter(is_active=True).order_by("slug"):
            if shop.slug:
                routes.append({
                    "loc": f"/shops/{shop.slug}",
                    "lastmod": _format_date(shop.updated_at) or today,
                })

        return Response(routes)


class SEOLocationDetailView(APIView):
    """GET /api/seo/locations/{slug}/ — location detail with shops."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        location = get_object_or_404(Location, slug=slug, is_active=True)
        serializer = SEOLocationDetailSerializer(location)
        return Response(serializer.data)


class SEOLocationProductsView(APIView):
    """GET /api/seo/locations/{slug}/products/ — product categories available in this location."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        location = get_object_or_404(Location, slug=slug, is_active=True)
        shop_ids = set(
            Shop.objects.filter(
                location=location, is_active=True, pricing_ready=True
            ).values_list("id", flat=True)
        )
        if not shop_ids:
            return Response([])
        cat_ids = set(
            Product.objects.filter(
                shop_id__in=shop_ids,
                status="PUBLISHED",
                is_active=True,
                category__isnull=False,
                category__shop__isnull=True,
                category__is_active=True,
            )
            .values_list("category_id", flat=True)
            .distinct()
        )
        categories = ProductCategory.objects.filter(id__in=cat_ids).order_by("name")
        return Response([{"slug": c.slug, "name": c.name} for c in categories])


class SEOProductDetailView(APIView):
    """GET /api/seo/products/{slug}/ — product category detail with product count."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        category = get_object_or_404(
            ProductCategory,
            slug=slug,
            shop__isnull=True,
            is_active=True,
        )
        serializer = SEOProductDetailSerializer(category)
        return Response(serializer.data)


class SEOLocationProductView(APIView):
    """GET /api/seo/locations/{location}/products/{product}/ — shops in location offering this category."""

    permission_classes = [AllowAny]

    def get(self, request, location_slug, product_slug):
        location = get_object_or_404(Location, slug=location_slug, is_active=True)
        category = get_object_or_404(
            ProductCategory,
            slug=product_slug,
            shop__isnull=True,
            is_active=True,
        )
        shop_ids = set(
            Shop.objects.filter(
                location=location, is_active=True, pricing_ready=True
            ).values_list("id", flat=True)
        )
        if not shop_ids:
            return Response({
                "location": {"slug": location.slug, "name": location.name},
                "category": {"slug": category.slug, "name": category.name},
                "shops": [],
            })
        shops_with_products = list(
            Shop.objects.filter(
                id__in=shop_ids,
                products__category=category,
                products__status="PUBLISHED",
                products__is_active=True,
            ).distinct().order_by("name")
        )
        return Response({
            "location": {"slug": location.slug, "name": location.name},
            "category": {"slug": category.slug, "name": category.name},
            "shops": [{"slug": s.slug, "name": s.name} for s in shops_with_products],
        })
