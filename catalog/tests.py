"""Smoke tests for the catalog app (audit #10a: it had no tests).

Checks that the public catalog URL resolves, its list endpoint answers 200
for a signed-out visitor, and that a catalog model saves (including the
auto-slug).
"""
from django.test import TestCase
from django.urls import resolve, reverse
from rest_framework.test import APIClient, APIRequestFactory

from catalog.choices import ProductStatus
from catalog.models import Product, ProductCategory
from catalog.views import CatalogBrowseView


class CatalogSmokeTests(TestCase):
    def test_public_products_url_resolves(self):
        path = reverse("public-all-products")
        self.assertEqual(path, "/api/public/products/")
        self.assertEqual(resolve(path).url_name, "public-all-products")

    def test_public_products_list_returns_published_products_to_anonymous(self):
        published = Product.objects.create(
            name="Smoke Business Cards", status=ProductStatus.PUBLISHED, is_active=True, is_public=True
        )
        Product.objects.create(name="Smoke Draft Flyer", status=ProductStatus.DRAFT, is_active=True, is_public=True)

        response = APIClient().get("/api/public/products/")

        self.assertEqual(response.status_code, 200)
        names = [row["name"] for row in response.json()["products"]]
        self.assertIn(published.name, names)
        self.assertNotIn("Smoke Draft Flyer", names)

    def test_category_and_product_save_with_auto_slug(self):
        category = ProductCategory.objects.create(name="Smoke Cards")
        product = Product.objects.create(name="Smoke Letterhead", category=category)

        category.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(category.slug, "smoke-cards")
        self.assertTrue(product.slug)
        self.assertEqual(product.category_id, category.id)
        self.assertEqual(product.status, ProductStatus.DRAFT)

    def test_catalog_browse_view_answers_anonymous(self):
        # catalog/urls.py is not included in config/urls.py (see results.txt),
        # so the view is called directly.
        request = APIRequestFactory().get("/")
        response = CatalogBrowseView.as_view()(request)
        self.assertEqual(response.status_code, 200)
