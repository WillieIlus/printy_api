"""
Product Gallery — merged into catalog.
Re-export catalog models for backwards compatibility.
"""
from catalog.models import Product, ProductCategory

__all__ = ["Product", "ProductCategory"]
