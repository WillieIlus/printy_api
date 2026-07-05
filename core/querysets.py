"""
Reusable querysets filtered by shop and ownership.

Use in views to scope data:
- Shop-scoped: Machine, Paper, PrintingRate, FinishingRate, Material, Product
- Ownership: QuoteRequest (buyer's own, or seller's shop)
"""
from django.db import models


class ShopScopedQuerySet(models.QuerySet):
    """
    Queryset for models with shop FK. Filter by shop_id/shop_pk.
    """

    def for_shop(self, shop):
        """Filter to objects belonging to the given shop (pk or instance)."""
        shop_pk = getattr(shop, 'pk', shop)
        return self.filter(shop_id=shop_pk)


class ProductScopedQuerySet(models.QuerySet):
    """
    Queryset for ProductFinishingOption - filter via product's shop.
    """

    def for_shop(self, shop):
        """Filter to finishing options of products in the given shop."""
        shop_pk = getattr(shop, 'pk', shop)
        return self.filter(product__shop_id=shop_pk)


class QuoteRequestQuerySet(models.QuerySet):
    """
    Queryset for QuoteRequest - filter by buyer (own) or shop (seller).
    """

    def for_buyer(self, user):
        """Filter to quote requests owned by the buyer."""
        if not user or not user.is_authenticated:
            return self.none()
        return self.filter(created_by_id=user.pk)

    def for_shop(self, shop):
        """Filter to quote requests for the given shop."""
        shop_pk = getattr(shop, 'pk', shop)
        return self.filter(shop_id=shop_pk)

    def for_seller(self, user):
        """Filter to quote requests for shops owned by the seller."""
        if not user or not user.is_authenticated:
            return self.none()
        return self.filter(shop__owner_id=user.pk)

    def for_buyer_or_seller(self, user):
        """
        Filter to quote requests the user can access:
        - Own as buyer, or
        - Shop they own as seller.
        """
        if not user or not user.is_authenticated:
            return self.none()
        from django.db.models import Q
        return self.filter(
            Q(created_by_id=user.pk) | Q(shop__owner_id=user.pk)
        )


class QuoteItemQuerySet(models.QuerySet):
    """
    Queryset for QuoteItem - filter via quote_request.
    """

    def for_buyer(self, user):
        """Filter to items on quote requests owned by the buyer."""
        if not user or not user.is_authenticated:
            return self.none()
        return self.filter(quote_request__created_by_id=user.pk)

    def for_shop(self, shop):
        """Filter to items on quote requests for the given shop."""
        shop_pk = getattr(shop, 'pk', shop)
        return self.filter(quote_request__shop_id=shop_pk)

    def for_buyer_or_seller(self, user):
        """Filter to items on quote requests the user can access."""
        if not user or not user.is_authenticated:
            return self.none()
        from django.db.models import Q
        return self.filter(
            Q(quote_request__created_by_id=user.pk) | Q(quote_request__shop__owner_id=user.pk)
        )
