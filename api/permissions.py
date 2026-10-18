"""
Custom DRF permission classes for the api app.

Covers superuser/staff gates, shop-owner checks and quote/quote-item ownership
checks, plus the marketplace gate that keeps printers and raw printer pricing
visible only to verified printing managers and shop staff.
"""
from rest_framework.permissions import BasePermission, SAFE_METHODS

from accounts.services.roles import (
    CANONICAL_CLIENT_ROLE,
    ActiveRoleNotHeld,
    get_request_active_role_raw,
    has_role,
    validate_active_role,
)
from shops.models import Shop


def _is_staff(user):
    return bool(user and getattr(user, "is_authenticated", False) and (user.is_staff or user.is_superuser))


class IsBuyerActor(BasePermission):
    """Buyer-only gate (law 9.2 + law 9.3).

    Only an authenticated account whose validated active role is ``client``
    (buyer) may proceed. The active role is validated against the roles the
    account actually holds, so a multi-role account acting as a manager or
    printer is refused with a clear 403.

    Subclass :class:`IsBuyerActorOrGuest` for guest endpoints that stay open to
    anonymous visitors while still refusing signed-in non-buyers.
    """

    message = "Only buyer accounts can perform this action."
    allow_anonymous = False

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return bool(self.allow_anonymous)
        requested_role = get_request_active_role_raw(request)
        try:
            active_role = validate_active_role(user, requested_role)
        except ActiveRoleNotHeld as exc:
            self.message = str(exc)
            return False
        if active_role != CANONICAL_CLIENT_ROLE:
            self.message = "Only buyer accounts can perform this action."
            return False
        return True


class IsBuyerActorOrGuest(IsBuyerActor):
    """Buyer-only for signed-in accounts, but anonymous visitors are allowed."""

    allow_anonymous = True


class IsSuperUser(BasePermission):
    """Allow only authenticated superusers."""

    message = "Superuser access required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and user.is_superuser)


class IsStaffUser(BasePermission):
    """Allow authenticated staff or superusers."""

    message = "Staff access required."

    def has_permission(self, request, view):
        return _is_staff(getattr(request, "user", None))


class PublicReadOnly(BasePermission):
    """Allow read access to everyone; restrict writes to staff."""

    message = "Write access requires staff."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return _is_staff(getattr(request, "user", None))


class IsQuoteRequestBuyer(BasePermission):
    """Allow a customer to access only their own quote requests."""

    message = "You can only access your own quote requests."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if _is_staff(user):
            return True
        created_by = getattr(obj, "created_by_id", None)
        return created_by is not None and created_by == user.pk


class IsQuoteRequestItemBuyer(BasePermission):
    """Allow a customer to access items on their own quote requests."""

    message = "You can only access items on your own quote requests."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if _is_staff(user):
            return True
        quote_request = getattr(obj, "quote_request", None)
        if quote_request is None:
            quote = getattr(obj, "quote", None)
            quote_request = getattr(quote, "quote_request", None)
        created_by = getattr(quote_request, "created_by_id", None) if quote_request else None
        return created_by is not None and created_by == user.pk


class IsQuoteRequestSeller(BasePermission):
    """Allow only the shop owner to access incoming quote requests."""

    message = "Only the shop owner can access incoming requests."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return False
        if _is_staff(user):
            return True
        slug = view.kwargs.get("shop_slug") or view.kwargs.get("slug")
        if not slug:
            return True  # ownership enforced at object level / via queryset
        shop = Shop.objects.filter(slug=str(slug)).first()
        return bool(shop and shop.owner_id == user.pk)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if _is_staff(user):
            return True
        shop = getattr(obj, "shop", None)
        return bool(shop and shop.owner_id == user.pk)


class IsQuoteOwner(BasePermission):
    """Allow only the shop that sent a quote to manage it."""

    message = "Only the shop owner can manage this quote."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if _is_staff(user):
            return True
        shop = getattr(obj, "shop", None)
        return bool(shop and shop.owner_id == user.pk)


class IsShopOwner(BasePermission):
    """Allow only the owner of the shop resolved from the URL."""

    message = "Only the shop owner can access this shop."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return False
        if _is_staff(user):
            return True
        slug = (
            view.kwargs.get("shop_slug")
            or view.kwargs.get("slug")
            or view.kwargs.get("shop_pk")
        )
        if not slug:
            return False
        shop = Shop.objects.filter(slug=str(slug)).first()
        return bool(shop and shop.owner_id == user.pk)


class PrintManagerMarketplaceAccess(BasePermission):
    """
    Restrict a printer-facing resource to verified printing managers, shop
    staff and platform admins. Buyers, clients and anonymous visitors are
    always denied.

    A user qualifies when the canonical role resolver yields `partner`
    (printing manager), `production` (shop staff / shop owner) or
    `super_admin`.
    """

    message = "Printer details are only available to verified printing managers."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return False
        return has_role(user, "partner", "production", "super_admin")