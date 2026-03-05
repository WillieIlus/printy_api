"""Gallery API views."""
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shops.models import Shop

from .models import Product, ProductCategory
from .serializers import (
    ProductCategoryListSerializer,
    ProductCategorySerializer,
    ProductGallerySerializer,
    ProductSerializer,
)


class ProductGalleryView(APIView):
    """GET /api/products/gallery/ — grouped by category, only active products."""

    permission_classes = [AllowAny]

    def get(self, request):
        categories = ProductCategory.objects.prefetch_related("products").order_by("name")
        result = []
        for cat in categories:
            products = cat.products.filter(is_active=True).order_by("title")
            if not products.exists():
                continue
            result.append({
                "category": ProductCategoryListSerializer(cat).data,
                "products": ProductGallerySerializer(products, many=True).data,
            })
        return Response({"categories": result})


class GalleryShopScopedMixin:
    """Mixin: resolve shop from shop_slug, require ownership."""

    def _get_shop(self):
        from rest_framework.exceptions import NotFound, PermissionDenied

        shop_slug = self.kwargs.get("shop_slug")
        shop = get_object_or_404(Shop, slug=shop_slug)
        if shop.owner_id != self.request.user.id:
            raise PermissionDenied("Not your shop.")
        return shop


class GalleryCategoryViewSet(GalleryShopScopedMixin, viewsets.ModelViewSet):
    """CRUD /api/shops/<shop_slug>/products/categories/"""

    serializer_class = ProductCategorySerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        shop = self._get_shop()
        return ProductCategory.objects.filter(shop=shop).order_by("name")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["shop"] = self._get_shop()
        return ctx

    def perform_create(self, serializer):
        serializer.save(shop=self._get_shop())


class GalleryProductViewSet(GalleryShopScopedMixin, viewsets.ModelViewSet):
    """CRUD /api/shops/<shop_slug>/products/ — gallery products."""

    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        shop = self._get_shop()
        return Product.objects.filter(shop=shop).select_related("category").order_by("title")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["shop"] = self._get_shop()
        return ctx

    def perform_create(self, serializer):
        serializer.save(shop=self._get_shop())

    @action(detail=True, methods=["post"], url_path="calculate-price")
    def calculate_price(self, request, shop_slug=None, slug=None):
        """Stub: validate payload, return structured breakdown placeholder."""
        product = self.get_object()
        # Basic validation
        if not isinstance(request.data, dict):
            return Response(
                {"detail": "Invalid payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Stub response
        return Response({
            "product_id": product.id,
            "product_slug": product.slug,
            "breakdown": {
                "material": 0,
                "printing": 0,
                "finishing": 0,
                "total": 0,
            },
            "message": "Calculate-price stub. Implement with pricing logic.",
        })
