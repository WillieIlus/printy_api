"""Views for shop models with seller/buyer permissions."""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from core.permissions import IsSellerOrReadOnly
from .models import Shop, Machine, Paper, PrintingRate, FinishingRate, Material, Product, ProductFinishingOption
from .serializers import (
    ShopSerializer, MachineSerializer, PaperSerializer,
    PrintingRateSerializer, FinishingRateSerializer, MaterialSerializer,
    ProductSerializer, ProductFinishingOptionSerializer,
)


class ShopViewSet(viewsets.ModelViewSet):
    """Shops - seller manages own, buyers can list (browse)."""
    queryset = Shop.objects.all()
    serializer_class = ShopSerializer
    permission_classes = [IsSellerOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_authenticated and self.request.user.owned_shops.exists():
            return qs  # Sellers see all for browse; could filter to own for write
        return qs


class ShopScopedModelViewSet(viewsets.ModelViewSet):
    """Base for shop-scoped models (inventory, pricing). Seller write, read for browse."""
    permission_classes = [IsSellerOrReadOnly]

    def get_queryset(self):
        shop_pk = self.kwargs.get('shop_pk')
        if shop_pk:
            return self.queryset.for_shop(shop_pk)
        return self.queryset.none()

    def perform_create(self, serializer):
        serializer.save(shop_id=self.kwargs['shop_pk'])


class MachineViewSet(ShopScopedModelViewSet):
    queryset = Machine.objects.all()
    serializer_class = MachineSerializer


class PaperViewSet(ShopScopedModelViewSet):
    queryset = Paper.objects.all()
    serializer_class = PaperSerializer


class PrintingRateViewSet(ShopScopedModelViewSet):
    queryset = PrintingRate.objects.all()
    serializer_class = PrintingRateSerializer


class FinishingRateViewSet(ShopScopedModelViewSet):
    queryset = FinishingRate.objects.all()
    serializer_class = FinishingRateSerializer


class MaterialViewSet(ShopScopedModelViewSet):
    queryset = Material.objects.all()
    serializer_class = MaterialSerializer


class ProductViewSet(ShopScopedModelViewSet):
    """Catalog - browse (read) for all, seller write."""
    queryset = Product.objects.all()
    serializer_class = ProductSerializer


class ProductFinishingOptionViewSet(viewsets.ModelViewSet):
    """Finishing options - scoped via product's shop."""
    queryset = ProductFinishingOption.objects.all()
    serializer_class = ProductFinishingOptionSerializer
    permission_classes = [IsSellerOrReadOnly]

    def get_queryset(self):
        shop_pk = self.kwargs.get('shop_pk')
        product_pk = self.kwargs.get('product_pk')
        if shop_pk and product_pk:
            return self.queryset.filter(product_id=product_pk, product__shop_id=shop_pk)
        return self.queryset.none()

    def perform_create(self, serializer):
        serializer.save(product_id=self.kwargs['product_pk'])
