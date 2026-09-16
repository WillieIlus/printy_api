"""Re-export core permissions for shop-scoped models."""
from core.permissions import (
    IsSellerOrReadOnly,
    IsSeller,
    CatalogBrowsePermission,
)

__all__ = ['IsSellerOrReadOnly', 'IsSeller', 'CatalogBrowsePermission']
