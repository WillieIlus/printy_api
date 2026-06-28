"""
Reusable DRF permission classes for seller/buyer access control.

Definitions:
- Seller = Shop.owner (staff support can be added later)
- Buyer = authenticated user who is not seller for that shop

Seller-only write access: inventory, pricing, catalog, price/lock quotes.
Buyer access: browse catalog, create QuoteRequest, manage QuoteItems on own DRAFT,
  submit QuoteRequest, view own QuoteRequests.
"""
from rest_framework import permissions

from shops.models import Shop
from quotes.models import QuoteRequest


def _resolve_shop(shop):
    """Resolve shop to Shop instance if pk given."""
    if shop is None:
        return None
    if isinstance(shop, Shop):
        return shop
    try:
        return Shop.objects.get(pk=shop)
    except (Shop.DoesNotExist, ValueError, TypeError):
        return None


def _get_shop_from_request(request, view):
    """Resolve shop from request/view. Handles shop in URL kwargs or object."""
    shop = None
    if hasattr(view, 'kwargs') and 'shop_pk' in view.kwargs:
        shop = view.kwargs.get('shop_pk')
    if shop is None and hasattr(view, 'kwargs') and 'shop_id' in view.kwargs:
        shop = view.kwargs.get('shop_id')
    if shop is None and hasattr(view, 'get_shop'):
        shop = view.get_shop(request)
    if shop is None and hasattr(request, 'resolver_match') and request.resolver_match:
        kwargs = request.resolver_match.kwargs
        shop = kwargs.get('shop_pk') or kwargs.get('shop_id')
    return _resolve_shop(shop)


def _get_shop_from_object(obj):
    """Get Shop instance from model instance (direct or via FK)."""
    if obj is None:
        return None
    if hasattr(obj, 'shop') and obj.shop is not None:
        return obj.shop
    if hasattr(obj, 'shop_id') and obj.shop_id:
        return _resolve_shop(obj.shop_id)
    if hasattr(obj, 'product') and hasattr(obj.product, 'shop'):
        return obj.product.shop
    if hasattr(obj, 'quote_request') and hasattr(obj.quote_request, 'shop'):
        return obj.quote_request.shop
    return None


def is_seller(user, shop):
    """Check if user is seller (owner) for the shop."""
    if not user or not user.is_authenticated:
        return False
    shop = _resolve_shop(shop)
    if shop is None:
        return False
    return shop.owner_id == user.pk


def is_buyer(user, shop):
    """Check if user is authenticated and NOT seller for that shop."""
    if not user or not user.is_authenticated:
        return False
    return not is_seller(user, shop)


class IsSellerOrReadOnly(permissions.BasePermission):
    """
    Seller can do anything. Others get read-only (for catalog browse).
    Use with shop-scoped views (inventory, pricing, catalog).
    """

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True  # AllowAny-style for GET/HEAD/OPTIONS (browse catalog)
        if not request.user.is_authenticated:
            return False
        shop = _get_shop_from_request(request, view)
        return is_seller(request.user, shop)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        shop = _get_shop_from_object(obj)
        return is_seller(request.user, shop)


class IsSeller(permissions.BasePermission):
    """
    Only seller can access. Use for seller-only actions (price/lock quotes).
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        shop = _get_shop_from_request(request, view)
        return is_seller(request.user, shop)

    def has_object_permission(self, request, view, obj):
        shop = _get_shop_from_object(obj)
        return is_seller(request.user, shop)


class IsBuyerOrSeller(permissions.BasePermission):
    """
    Buyer or seller can access. Use for quote views where both need access.
    Buyer: create, manage own DRAFT, submit, view own.
    Seller: view all, price, lock.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        shop = _get_shop_from_request(request, view)
        return is_seller(request.user, shop) or is_buyer(request.user, shop)

    def has_object_permission(self, request, view, obj):
        shop = _get_shop_from_object(obj)
        if is_seller(request.user, shop):
            return True
        # Buyer: only own quote requests
        if hasattr(obj, 'buyer_id'):
            return obj.buyer_id == request.user.pk
        if hasattr(obj, 'quote_request') and hasattr(obj.quote_request, 'buyer_id'):
            return obj.quote_request.buyer_id == request.user.pk
        return False


class IsBuyerForDraftQuote(permissions.BasePermission):
    """
    Buyer can add/update/remove QuoteItems only on own QuoteRequest while status=DRAFT.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        # Write methods only
        if request.method in permissions.SAFE_METHODS:
            return True  # Read handled by IsBuyerOrSeller
        shop = _get_shop_from_request(request, view)
        return is_buyer(request.user, shop) or is_seller(request.user, shop)

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        # For write: must be buyer and quote must be DRAFT
        quote_request = obj.quote_request if hasattr(obj, 'quote_request') else obj
        if not hasattr(quote_request, 'buyer_id') or quote_request.buyer_id != request.user.pk:
            return False
        return quote_request.status == QuoteRequest.DRAFT


class CanSubmitQuoteRequest(permissions.BasePermission):
    """
    Buyer can submit own QuoteRequest (status -> SUBMITTED) when DRAFT.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        shop = _get_shop_from_request(request, view)
        return is_buyer(request.user, shop)

    def has_object_permission(self, request, view, obj):
        if obj.buyer_id != request.user.pk:
            return False
        return obj.status == QuoteRequest.DRAFT


class CanPriceOrLockQuote(permissions.BasePermission):
    """
    Only seller can price or lock a QuoteRequest.
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        shop = _get_shop_from_request(request, view)
        return is_seller(request.user, shop)

    def has_object_permission(self, request, view, obj):
        shop = _get_shop_from_object(obj)
        return is_seller(request.user, shop)


class CatalogBrowsePermission(permissions.BasePermission):
    """
    AllowAny or authenticated buyer for browsing catalog.
    Use for Product, ProductFinishingOption list/detail.
    """

    def has_permission(self, request, view):
        return True  # AllowAny for browse

    def has_object_permission(self, request, view, obj):
        return True  # Anyone can view catalog items
