"""API permissions for buyer and seller roles."""
from rest_framework import permissions


class IsStaffUser(permissions.BasePermission):
    """Allow only authenticated staff users."""

    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.is_staff


class IsShopOwner(permissions.BasePermission):
    """Allow only the shop owner."""

    def has_object_permission(self, request, view, obj):
        shop = getattr(obj, "shop", None)
        if shop is None:
            return False
        return shop.owner_id == request.user.id

    def has_permission(self, request, view):
        # For list/create, we check in the view
        return request.user.is_authenticated


class IsQuoteRequestBuyer(permissions.BasePermission):
    """Allow buyer (created_by) to access their quote request."""

    def has_object_permission(self, request, view, obj):
        return obj.created_by_id == request.user.id

    def has_permission(self, request, view):
        return request.user.is_authenticated


class IsQuoteRequestSeller(permissions.BasePermission):
    """Allow shop owner (seller) to access quote requests for their shop."""

    def has_object_permission(self, request, view, obj):
        return obj.shop.owner_id == request.user.id

    def has_permission(self, request, view):
        return request.user.is_authenticated


class PublicReadOnly(permissions.BasePermission):
    """Allow any read (GET), no write."""

    def has_permission(self, request, view):
        if view.action in ("list", "retrieve"):
            return True
        return False

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return False
