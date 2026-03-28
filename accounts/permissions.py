"""Reusable account-role permissions."""

from rest_framework import permissions

from accounts.models import User
from accounts.services.roles import has_role


class HasAccountRole(permissions.BasePermission):
    """Allow authenticated users with any of the configured account roles."""

    allowed_roles: tuple[str, ...] = ()

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and has_role(request.user, *self.allowed_roles)
        )


class IsClientRole(HasAccountRole):
    allowed_roles = (User.Role.CLIENT,)


class IsShopOwnerRole(HasAccountRole):
    allowed_roles = (User.Role.SHOP_OWNER,)


class IsAccountStaffRole(HasAccountRole):
    allowed_roles = (User.Role.STAFF,)
