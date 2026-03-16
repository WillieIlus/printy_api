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
    """Allow buyer (created_by) to access their quote request. Staff can access for admin."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        return obj.created_by_id == request.user.id

    def has_permission(self, request, view):
        return request.user.is_authenticated


class IsQuoteRequestItemBuyer(permissions.BasePermission):
    """Allow buyer to access quote items. For QuoteItem: checks quote_request.created_by."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        qr = getattr(obj, "quote_request", None)
        return qr and qr.created_by_id == request.user.id

    def has_permission(self, request, view):
        return request.user.is_authenticated


class IsQuoteRequestSeller(permissions.BasePermission):
    """Allow shop owner (seller) to access quote requests for their shop. Staff can access for admin."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        return obj.shop.owner_id == request.user.id

    def has_permission(self, request, view):
        return request.user.is_authenticated


class IsShopQuoteOwner(permissions.BasePermission):
    """Allow shop owner to access their shop quote. Staff can access for admin."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        return obj.shop.owner_id == request.user.id

    def has_permission(self, request, view):
        return request.user.is_authenticated


class IsJobShopOwner(permissions.BasePermission):
    """Allow shop owner to access jobs for their shop. Staff can access for admin."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        return obj.shop.owner_id == request.user.id

    def has_permission(self, request, view):
        return request.user.is_authenticated


class IsJobCustomerOrShopOwner(permissions.BasePermission):
    """Allow shop owner OR customer (buyer from accepted quote) to access job."""

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        if obj.shop_id and obj.shop.owner_id == request.user.id:
            return True
        shop_quote = getattr(obj, "shop_quote", None)
        if shop_quote and shop_quote.quote_request_id:
            return shop_quote.quote_request.created_by_id == request.user.id
        return False

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
