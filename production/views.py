"""Production API for shop-side ProductionOrder fulfillment."""
from rest_framework import viewsets
from rest_framework.permissions import BasePermission

from .models import ProductionOrder
from .serializers import ProductionOrderListSerializer, ProductionOrderSerializer, ProductionOrderWriteSerializer


def _is_platform_admin(user) -> bool:
    return bool(user and getattr(user, "is_authenticated", False) and (user.is_staff or user.is_superuser))


class IsShopPrinterOrPlatformAdmin(BasePermission):
    """Audit #5: the printer who owns the order's shop, or a platform admin.

    View-level: an authenticated shop owner or platform admin. Object-level:
    the order's shop must be owned by the requester (admins excepted). The
    queryset is tenant-scoped as well, so other shops' orders 404.
    """

    message = "Only the printer of this shop can access its production orders."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if _is_platform_admin(user):
            return True
        return user.owned_shops.exists()

    def has_object_permission(self, request, view, obj):
        user = request.user
        if _is_platform_admin(user):
            return True
        return obj.shop.owner_id == user.pk


class ProductionOrderViewSet(viewsets.ModelViewSet):
    permission_classes = [IsShopPrinterOrPlatformAdmin]

    def get_queryset(self):
        user = self.request.user
        qs = ProductionOrder.objects.select_related("shop", "quote", "created_by")
        if _is_platform_admin(user):
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
