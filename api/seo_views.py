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
from catalog.services import public_products_queryset
from shops.models import Shop

from .seo_serializers import (
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
        return Response([])


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
        routes.append({"loc": "/for-shops", "lastmod": today})

        # Static hub pages
        routes.append({"loc": "/locations", "lastmod": today})
        routes.append({"loc": "/products", "lastmod": today})
        routes.append({"loc": "/shops", "lastmod": today})
        routes.append({"loc": "/gallery", "lastmod": today})

        # Product category pages: /products/[slug]
        categories = ProductCategory.objects.filter(
            shop__isnull=True, is_active=True
        ).order_by("slug")
        for cat in categories:
            routes.append({
                "loc": f"/products/{cat.slug}",
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
        return Response({"detail": "Location SEO pages are postponed for MVP."}, status=410)


class SEOLocationProductsView(APIView):
    """GET /api/seo/locations/{slug}/products/ — product categories available in this location."""

    permission_classes = [AllowAny]

    def get(self, request, slug):
        return Response([])


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
        category = get_object_or_404(
            ProductCategory,
            slug=product_slug,
            shop__isnull=True,
            is_active=True,
        )
        return Response({
            "location": {"slug": location_slug, "name": location_slug},
            "category": {"slug": category.slug, "name": category.name},
            "shops": [],
            "detail": "Location SEO pages are postponed for MVP.",
        })
