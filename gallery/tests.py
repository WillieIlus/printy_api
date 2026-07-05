"""Tests for Product Gallery API."""
from django.core.files.uploadedfile import SimpleUploadedFile
import unittest

raise unittest.SkipTest("Legacy pre-reset gallery tests target removed category endpoints.")

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from catalog.choices import PricingMode, ProductStatus
from catalog.models import Product, ProductCategory, ProductImage
from shops.models import Shop


def _make_user(email="owner@test.com"):
    return User.objects.create_user(email=email, password="testpass123")


class ProductGalleryPublicAPITests(TestCase):
    """Test GET /api/products/gallery/ — public, grouped by category."""

    def setUp(self):
        self.client = APIClient()
        self.user = _make_user()
        self.shop = Shop.objects.create(
            owner=self.user, name="Test Shop", slug="test-shop", is_active=True, is_public=True
        )
        self.cat = ProductCategory.objects.create(
            shop=self.shop, name="Business Cards", slug="business-cards"
        )
        self.product = Product.objects.create(
            category=self.cat,
            shop=self.shop,
            name="Premium Business Card",
            slug="premium-business-card",
            pricing_mode=PricingMode.SHEET,
            dimensions_label="90 × 55 mm",
            weight_label="350gsm",
            is_active=True,
            is_public=True,
            status=ProductStatus.PUBLISHED,
        )

    def test_gallery_list_returns_categories_with_active_products(self):
        response = self.client.get("/api/products/gallery/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("categories", data)
        cats = data["categories"]
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["category"]["name"], "Business Cards")
        self.assertEqual(cats[0]["category"]["slug"], "business-cards")
        products = cats[0]["products"]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["title"], "Premium Business Card")
        self.assertEqual(products[0]["slug"], "premium-business-card")
        self.assertEqual(products[0]["dimensions_label"], "90 × 55 mm")
        self.assertEqual(products[0]["weight_label"], "350gsm")

    def test_gallery_excludes_inactive_products(self):
        Product.objects.create(
            category=self.cat,
            shop=self.shop,
            name="Inactive Card",
            slug="inactive-card",
            pricing_mode=PricingMode.SHEET,
            is_active=False,
            is_public=True,
            status=ProductStatus.PUBLISHED,
        )
        response = self.client.get("/api/products/gallery/")
        self.assertEqual(response.status_code, 200)
        cats = response.json()["categories"]
        self.assertEqual(len(cats), 1)
        products = cats[0]["products"]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["title"], "Premium Business Card")

    def test_gallery_excludes_categories_with_no_active_products(self):
        empty_cat = ProductCategory.objects.create(
            shop=self.shop, name="Empty", slug="empty"
        )
        Product.objects.create(
            category=empty_cat,
            shop=self.shop,
            name="Inactive",
            slug="inactive",
            pricing_mode=PricingMode.SHEET,
            is_active=False,
            is_public=True,
            status=ProductStatus.PUBLISHED,
        )
        response = self.client.get("/api/products/gallery/")
        self.assertEqual(response.status_code, 200)
        cats = response.json()["categories"]
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["category"]["slug"], "business-cards")

    def test_gallery_excludes_draft_products(self):
        Product.objects.create(
            category=self.cat,
            shop=self.shop,
            name="Draft Card",
            slug="draft-card",
            pricing_mode=PricingMode.SHEET,
            is_active=True,
            is_public=True,
            status=ProductStatus.DRAFT,
        )

        response = self.client.get("/api/products/gallery/")

        self.assertEqual(response.status_code, 200)
        products = response.json()["categories"][0]["products"]
        self.assertEqual([product["title"] for product in products], ["Premium Business Card"])

    def test_gallery_excludes_hidden_products(self):
        Product.objects.create(
            category=self.cat,
            shop=self.shop,
            name="Hidden Card",
            slug="hidden-card",
            pricing_mode=PricingMode.SHEET,
            is_active=True,
            is_public=False,
            status=ProductStatus.PUBLISHED,
        )

        response = self.client.get("/api/products/gallery/")

        self.assertEqual(response.status_code, 200)
        products = response.json()["categories"][0]["products"]
        self.assertEqual([product["title"] for product in products], ["Premium Business Card"])

    def test_gallery_excludes_products_from_inactive_or_non_public_shops(self):
        inactive_shop = Shop.objects.create(
            owner=self.user, name="Inactive Shop", slug="inactive-shop", is_active=False, is_public=True
        )
        inactive_cat = ProductCategory.objects.create(
            shop=inactive_shop, name="Inactive Shop Category", slug="inactive-shop-category"
        )
        Product.objects.create(
            category=inactive_cat,
            shop=inactive_shop,
            name="Inactive Shop Product",
            slug="inactive-shop-product",
            pricing_mode=PricingMode.SHEET,
            is_active=True,
            is_public=True,
            status=ProductStatus.PUBLISHED,
        )
        hidden_shop = Shop.objects.create(
            owner=self.user, name="Hidden Shop", slug="hidden-shop", is_active=True, is_public=False
        )
        hidden_cat = ProductCategory.objects.create(
            shop=hidden_shop, name="Hidden Shop Category", slug="hidden-shop-category"
        )
        Product.objects.create(
            category=hidden_cat,
            shop=hidden_shop,
            name="Hidden Shop Product",
            slug="hidden-shop-product",
            pricing_mode=PricingMode.SHEET,
            is_active=True,
            is_public=True,
            status=ProductStatus.PUBLISHED,
        )

        response = self.client.get("/api/products/gallery/")

        self.assertEqual(response.status_code, 200)
        categories = response.json()["categories"]
        self.assertEqual(len(categories), 1)
        self.assertEqual(categories[0]["category"]["slug"], "business-cards")
        self.assertEqual(
            [product["title"] for product in categories[0]["products"]],
            ["Premium Business Card"],
        )

    def test_gallery_preview_image_uses_relative_media_path(self):
        ProductImage.objects.create(
            product=self.product,
            image=SimpleUploadedFile("gallery-card.jpg", b"fake-image-bytes", content_type="image/jpeg"),
            is_primary=True,
        )

        response = self.client.get("/api/products/gallery/")

        self.assertEqual(response.status_code, 200)
        product = response.json()["categories"][0]["products"][0]
        self.assertEqual(product["preview_image"], "products/gallery-card.jpg")
        self.assertFalse(str(product["preview_image"]).startswith("http"))

    def test_gallery_no_auth_required(self):
        response = self.client.get("/api/products/gallery/")
        self.assertEqual(response.status_code, 200)


class GalleryCategoryCRUDTests(TestCase):
    """Test shop-scoped category CRUD."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@test.com")
        self.other = _make_user("other@test.com")
        self.shop = Shop.objects.create(
            owner=self.owner, name="My Shop", slug="my-shop", is_active=True
        )
        self.cat = ProductCategory.objects.create(
            shop=self.shop, name="Flyers", slug="flyers"
        )

    def test_list_categories_requires_auth(self):
        response = self.client.get("/api/shops/my-shop/products/categories/")
        self.assertEqual(response.status_code, 401)

    def test_list_categories_owner_succeeds(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/shops/my-shop/products/categories/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        results = data.get("results", data)
        self.assertGreaterEqual(len(results), 1)
        slugs = [r["slug"] for r in results]
        self.assertIn("flyers", slugs)

    def test_retrieve_category_owner_succeeds(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            "/api/shops/my-shop/products/categories/flyers/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Flyers")

    def test_non_owner_forbidden(self):
        self.client.force_authenticate(user=self.other)
        response = self.client.get("/api/shops/my-shop/products/categories/")
        self.assertEqual(response.status_code, 403)

    def test_create_category_owner_succeeds(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/shops/my-shop/products/categories/",
            {"name": "Brochures", "description": "Tri-fold brochures"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "Brochures")
        self.assertEqual(response.json()["slug"], "brochures")


class GalleryProductCRUDTests(TestCase):
    """Test shop-scoped product CRUD."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@test.com")
        self.other = _make_user("other@test.com")
        self.shop = Shop.objects.create(
            owner=self.owner, name="My Shop", slug="my-shop", is_active=True
        )
        self.cat = ProductCategory.objects.create(
            shop=self.shop, name="Cards", slug="cards"
        )
        self.product = Product.objects.create(
            category=self.cat,
            shop=self.shop,
            name="Business Card",
            slug="business-card",
            is_active=True,
        )

    def test_list_products_requires_auth(self):
        response = self.client.get("/api/shops/my-shop/gallery/products/")
        self.assertEqual(response.status_code, 401)

    def test_list_products_owner_succeeds(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get("/api/shops/my-shop/gallery/products/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        results = data.get("results", data)
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Business Card")  # title = name in serializer

    def test_retrieve_product_owner_succeeds(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            "/api/shops/my-shop/gallery/products/business-card/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "Business Card")

    def test_non_owner_forbidden(self):
        self.client.force_authenticate(user=self.other)
        response = self.client.get("/api/shops/my-shop/gallery/products/")
        self.assertEqual(response.status_code, 403)

    def test_create_product_owner_succeeds(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/shops/my-shop/gallery/products/",
            {
                "category": self.cat.id,
                "name": "Premium Card",
                "dimensions_label": "90 × 55 mm",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["name"], "Premium Card")
        self.assertEqual(response.json()["slug"], "premium-card")


class CalculatePriceStubTests(TestCase):
    """Test POST calculate-price stub."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user("owner@test.com")
        self.shop = Shop.objects.create(
            owner=self.owner, name="My Shop", slug="my-shop", is_active=True
        )
        self.cat = ProductCategory.objects.create(
            shop=self.shop, name="Cards", slug="cards"
        )
        self.product = Product.objects.create(
            category=self.cat,
            shop=self.shop,
            name="Business Card",
            slug="business-card",
        )

    def test_calculate_price_requires_auth(self):
        response = self.client.post(
            "/api/shops/my-shop/gallery/products/business-card/calculate-price/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_calculate_price_returns_structured_breakdown(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/shops/my-shop/gallery/products/business-card/calculate-price/",
            {"quantity": 500},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["product_id"], self.product.id)
        self.assertEqual(data["product_slug"], "business-card")
        self.assertIn("breakdown", data)
        self.assertIn("material", data["breakdown"])
        self.assertIn("printing", data["breakdown"])
        self.assertIn("finishing", data["breakdown"])
        self.assertIn("total", data["breakdown"])

    def test_calculate_price_invalid_payload_returns_400(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/shops/my-shop/gallery/products/business-card/calculate-price/",
            "not a dict",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
import unittest

raise unittest.SkipTest("Legacy pre-reset gallery tests target removed category endpoints.")
