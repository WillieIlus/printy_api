"""Tests for Product Gallery API."""
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from gallery.models import Product, ProductCategory
from shops.models import Shop


def _make_user(email="owner@test.com"):
    return User.objects.create_user(email=email, password="testpass123")


class ProductGalleryPublicAPITests(TestCase):
    """Test GET /api/products/gallery/ — public, grouped by category."""

    def setUp(self):
        self.client = APIClient()
        self.user = _make_user()
        self.shop = Shop.objects.create(
            owner=self.user, name="Test Shop", slug="test-shop", is_active=True
        )
        self.cat = ProductCategory.objects.create(
            shop=self.shop, name="Business Cards", slug="business-cards"
        )
        self.product = Product.objects.create(
            category=self.cat,
            shop=self.shop,
            title="Premium Business Card",
            slug="premium-business-card",
            dimensions_label="90 × 55 mm",
            weight_label="350gsm",
            is_active=True,
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
            title="Inactive Card",
            slug="inactive-card",
            is_active=False,
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
            title="Inactive",
            slug="inactive",
            is_active=False,
        )
        response = self.client.get("/api/products/gallery/")
        self.assertEqual(response.status_code, 200)
        cats = response.json()["categories"]
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["category"]["slug"], "business-cards")

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
            title="Business Card",
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
        self.assertEqual(results[0]["title"], "Business Card")

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
                "title": "Premium Card",
                "dimensions_label": "90 × 55 mm",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["title"], "Premium Card")
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
            title="Business Card",
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
