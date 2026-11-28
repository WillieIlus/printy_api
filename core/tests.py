"""Smoke tests for the core app (audit #10a: it had no tests).

core has no models, views or URLs of its own. It provides the shared
permission helpers, the QuoteRequest queryset and the seed commands, so the
smoke tests cover those: the app loads, the seed command creates and saves
real rows that the main public endpoint then serves, and the queryset and
permission helpers scope by owner.
"""
from io import StringIO

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from core.permissions import is_buyer, is_seller
from quotes.models import QuoteRequest
from shops.models import Shop


class CoreSmokeTests(TestCase):
    def test_app_is_installed(self):
        self.assertEqual(apps.get_app_config("core").name, "core")

    def test_seed_data_command_creates_rows_served_by_the_public_catalog(self):
        from catalog.models import Product
        from pricing.models import PlatformFeePolicy

        out = StringIO()
        call_command("seed_data", stdout=out)

        self.assertIn("Seeded", out.getvalue())
        self.assertTrue(PlatformFeePolicy.objects.filter(is_active=True).exists())
        self.assertTrue(Shop.objects.exists())
        self.assertTrue(Product.objects.exists())
        response = APIClient().get("/api/public/products/")
        self.assertEqual(response.status_code, 200)

    def test_quote_request_queryset_and_permissions_scope_by_owner(self):
        owner = User.objects.create_user(email="core-smoke-owner@test.com", password="x", role="shop_owner")
        buyer = User.objects.create_user(email="core-smoke-buyer@test.com", password="x", role="client")
        shop = Shop.objects.create(owner=owner, name="Core Smoke Shop", slug="core-smoke-shop", is_active=True)
        request = QuoteRequest.objects.create(shop=shop, created_by=buyer, customer_name="Core Smoke")

        self.assertEqual(list(QuoteRequest.objects.for_buyer(buyer)), [request])
        self.assertEqual(list(QuoteRequest.objects.for_seller(owner)), [request])
        self.assertEqual(list(QuoteRequest.objects.for_seller(buyer)), [])
        self.assertTrue(is_seller(owner, shop))
        self.assertFalse(is_seller(buyer, shop))
        self.assertTrue(is_buyer(buyer, shop.pk))
