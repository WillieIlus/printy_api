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
            products = cat.products.filter(is_active=True).order_by("name")
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
        return Product.objects.filter(shop=shop).select_related("category").order_by("name")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["shop"] = self._get_shop()
        return ctx

    def perform_create(self, serializer):
        serializer.save(shop=self._get_shop())

    @action(detail=True, methods=["post"], url_path="calculate-price")
    def calculate_price(self, request, shop_slug=None, slug=None):
        """Compute price from chosen options using server-side pricing logic."""
        product = self.get_object()
        shop = self._get_shop()

        if not isinstance(request.data, dict):
            return Response(
                {"detail": "Invalid payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = request.data
        quantity = data.get("quantity") or product.min_quantity or 100
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 100
        quantity = max(1, quantity)

        paper_id = data.get("paper_id") or data.get("paper")
        material_id = data.get("material_id") or data.get("material")
        machine_id = data.get("machine_id") or data.get("machine")
        sides = data.get("sides") or product.default_sides or "SIMPLEX"
        color_mode = data.get("color_mode") or "COLOR"
        chosen_width_mm = data.get("chosen_width_mm") or product.default_finished_width_mm
        chosen_height_mm = data.get("chosen_height_mm") or product.default_finished_height_mm

        finishing_specs = []
        for f in data.get("finishings") or []:
            if isinstance(f, dict) and "finishing_rate" in f:
                spec = {"finishing_rate": f["finishing_rate"], "apply_to_sides": f.get("apply_to_sides") or "BOTH"}
                finishing_specs.append(spec)
            elif isinstance(f, (int, str)):
                finishing_specs.append({"finishing_rate": int(f), "apply_to_sides": "BOTH"})

        from inventory.models import Paper, Machine
        from pricing.models import FinishingRate, Material

        if paper_id and not Paper.objects.filter(pk=paper_id, shop=shop).exists():
            return Response({"detail": "Paper not found or not in this shop."}, status=status.HTTP_400_BAD_REQUEST)
        if material_id and not Material.objects.filter(pk=material_id, shop=shop).exists():
            return Response({"detail": "Material not found or not in this shop."}, status=status.HTTP_400_BAD_REQUEST)
        if machine_id and not Machine.objects.filter(pk=machine_id, shop=shop).exists():
            return Response({"detail": "Machine not found or not in this shop."}, status=status.HTTP_400_BAD_REQUEST)
        for spec in finishing_specs:
            fid = spec["finishing_rate"]
            if not FinishingRate.objects.filter(pk=fid, shop=shop, is_active=True).exists():
                return Response({"detail": f"Finishing rate {fid} not found or not in this shop."}, status=status.HTTP_400_BAD_REQUEST)

        from quotes.pricing_service import compute_pricing_from_spec

        result = compute_pricing_from_spec(
            product,
            quantity,
            paper_id=paper_id,
            material_id=material_id,
            machine_id=machine_id,
            sides=sides,
            color_mode=color_mode,
            chosen_width_mm=chosen_width_mm,
            chosen_height_mm=chosen_height_mm,
            finishing_specs=finishing_specs if finishing_specs else None,
        )

        d = result.to_dict()
        paper_cost = float(d.get("paper_cost", 0) or 0)
        print_cost = float(d.get("print_cost", 0) or 0)
        material_cost = float(d.get("material_cost", 0) or 0)
        finishing_total = float(d.get("finishing_total", 0) or 0)
        line_total = float(d.get("line_total", 0) or 0)
        unit_price = float(d.get("unit_price", 0) or 0)

        return Response({
            "product_id": product.id,
            "product_slug": product.slug,
            "breakdown": {
                "material": material_cost,
                "printing": print_cost,
                "paper": paper_cost,
                "finishing": finishing_total,
                "total": line_total,
            },
            "total": line_total,
            "per_unit": unit_price,
            "can_calculate": result.can_calculate,
            "pricing_snapshot": d,
        })
