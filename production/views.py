"""Production API for shop-side ProductionOrder fulfillment."""
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import ProductionOrder
from .serializers import ProductionOrderListSerializer, ProductionOrderSerializer, ProductionOrderWriteSerializer


class ProductionOrderViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = ProductionOrder.objects.select_related("shop", "quote", "created_by")
        if user.is_staff:
            return qs
        return qs.filter(shop__owner=user)

    def get_serializer_class(self):
        if self.action == "list":
            return ProductionOrderListSerializer
        if self.action in {"create", "update", "partial_update"}:
            return ProductionOrderWriteSerializer
        return ProductionOrderSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
