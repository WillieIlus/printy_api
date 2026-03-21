"""
Minimal test suite for Printy API.
Uses Django TestCase (standard in Django projects).
"""
import unittest
from decimal import Decimal
from pathlib import Path

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status


raise unittest.SkipTest("Legacy printy_api.shops tests are outside the active Django app registry.")

from .models import (
    SheetSize,
    Shop,
    Paper,
    Machine,
    Product,
    QuoteRequest,
    QuoteItem,
)
from .quote_engine import recalculate_quote_request

User = get_user_model()


class BuyerPermissionTest(TestCase):
    """Test 1: Buyer cannot create Product or Paper (403)."""

    def setUp(self):
        self.buyer = User.objects.create_user("buyer", "b@t.com", "pass")
        self.seller = User.objects.create_user("seller", "s@t.com", "pass")
        a4 = SheetSize.objects.create(name="A4", width_mm=210, height_mm=297)
        self.shop = Shop.objects.create(name="Test Shop", slug="test-shop", owner=self.seller)
        self.client = APIClient()

    def test_buyer_cannot_create_product(self):
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            "/api/shops/test-shop/products/create/",
            {"name": "Cards", "slug": "cards", "description": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_buyer_cannot_create_paper(self):
        a4 = SheetSize.objects.get(name="A4")
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            "/api/shops/test-shop/papers/create/",
            {
                "name": "Silk 300",
                "sheet_size": a4.pk,
                "gsm": 300,
                "price_per_sheet": "0.10",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class BuyerQuoteFlowTest(TestCase):
    """Test 2: Buyer can create QuoteRequest, add multiple QuoteItems, submit."""

    def setUp(self):
        self.buyer = User.objects.create_user("buyer", "b@t.com", "pass")
        self.seller = User.objects.create_user("seller", "s@t.com", "pass")
        a4 = SheetSize.objects.create(name="A4", width_mm=210, height_mm=297)
        self.shop = Shop.objects.create(name="S", slug="s", owner=self.seller)
        self.paper = Paper.objects.create(
            shop=self.shop,
            name="P",
            sheet_size=a4,
            gsm=300,
            price_per_sheet=Decimal("0.1"),
        )
        self.machine = Machine.objects.create(
            shop=self.shop,
            name="M",
            sheet_size=a4,
            cost_per_impression=Decimal("0.02"),
        )
        self.product = Product.objects.create(shop=self.shop, name="Prod", slug="prod")
        self.client = APIClient()

    def test_buyer_create_quote_add_items_submit(self):
        self.client.force_authenticate(user=self.buyer)

        # Create quote
        resp = self.client.post(
            "/api/quotes/",
            {"shop": self.shop.pk},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        quote_pk = resp.data["id"]

        # Add multiple items
        for qty in [50, 100]:
            resp = self.client.post(
                f"/api/quotes/{quote_pk}/items/",
                {
                    "product": self.product.pk,
                    "quantity": qty,
                    "paper": self.paper.pk,
                    "machine": self.machine.pk,
                },
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        quote = QuoteRequest.objects.get(pk=quote_pk)
        self.assertEqual(quote.items.count(), 2)

        # Submit
        resp = self.client.post(f"/api/quotes/{quote_pk}/submit/", format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteRequest.Status.SUBMITTED)


class SellerPriceLockTest(TestCase):
    """Test 3: Seller can price quote and prices lock (unit_price/line_total set; no recalc unless forced)."""

    def setUp(self):
        self.buyer = User.objects.create_user("buyer", "b@t.com", "pass")
        self.seller = User.objects.create_user("seller", "s@t.com", "pass")
        a4 = SheetSize.objects.create(name="A4", width_mm=210, height_mm=297)
        self.shop = Shop.objects.create(name="S", slug="s", owner=self.seller)
        self.paper = Paper.objects.create(
            shop=self.shop,
            name="P",
            sheet_size=a4,
            gsm=300,
            price_per_sheet=Decimal("0.10"),
        )
        self.machine = Machine.objects.create(
            shop=self.shop,
            name="M",
            sheet_size=a4,
            cost_per_impression=Decimal("0.02"),
        )
        self.product = Product.objects.create(shop=self.shop, name="Prod", slug="prod")
        self.client = APIClient()

    def test_seller_prices_quote_and_prices_lock(self):
        quote = QuoteRequest.objects.create(shop=self.shop, buyer=self.buyer, status=QuoteRequest.Status.SUBMITTED)
        item = QuoteItem.objects.create(
            quote_request=quote,
            product=self.product,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
        )
        recalculate_quote_request(quote)
        item.refresh_from_db()
        self.assertIsNotNone(item.unit_price)
        self.assertIsNotNone(item.total_price)
        original_total = item.total_price

        # Seller prices (sets status to PRICED)
        self.client.force_authenticate(user=self.seller)
        resp = self.client.post(f"/api/quotes/{quote.pk}/price/", format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteRequest.Status.PRICED)

        # Change underlying paper price — recalc without force should NOT update item
        self.paper.price_per_sheet = Decimal("999.00")
        self.paper.save()
        recalculate_quote_request(quote, force=False)
        item.refresh_from_db()
        self.assertEqual(item.total_price, original_total, "Prices should stay locked without force")

        # With force=True, recalc should update
        recalculate_quote_request(quote, force=True)
        item.refresh_from_db()
        self.assertNotEqual(item.total_price, original_total)


class ShopConsistencyTest(TestCase):
    """Test 4: Enforce shop consistency — cannot attach paper from another shop."""

    def setUp(self):
        self.buyer = User.objects.create_user("buyer", "b@t.com", "pass")
        self.seller1 = User.objects.create_user("seller1", "s1@t.com", "pass")
        self.seller2 = User.objects.create_user("seller2", "s2@t.com", "pass")
        a4 = SheetSize.objects.create(name="A4", width_mm=210, height_mm=297)
        self.shop1 = Shop.objects.create(name="Shop1", slug="shop1", owner=self.seller1)
        self.shop2 = Shop.objects.create(name="Shop2", slug="shop2", owner=self.seller2)
        self.paper1 = Paper.objects.create(
            shop=self.shop1,
            name="P1",
            sheet_size=a4,
            gsm=300,
            price_per_sheet=Decimal("0.1"),
        )
        self.paper2 = Paper.objects.create(
            shop=self.shop2,
            name="P2",
            sheet_size=a4,
            gsm=300,
            price_per_sheet=Decimal("0.1"),
        )
        self.machine1 = Machine.objects.create(
            shop=self.shop1,
            name="M1",
            sheet_size=a4,
            cost_per_impression=Decimal("0.02"),
        )
        self.product1 = Product.objects.create(shop=self.shop1, name="Prod1", slug="prod1")
        self.client = APIClient()

    def test_cannot_attach_paper_from_another_shop(self):
        quote = QuoteRequest.objects.create(shop=self.shop1, buyer=self.buyer)
        self.client.force_authenticate(user=self.buyer)
        resp = self.client.post(
            f"/api/quotes/{quote.pk}/items/",
            {
                "product": self.product1.pk,
                "quantity": 100,
                "paper": self.paper2.pk,  # paper from shop2
                "machine": self.machine1.pk,
            },
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(quote.items.count(), 0)


class NoPaperPriceLookupTest(TestCase):
    """Test 5: Ensure no attribute-based PaperPrice lookups exist in services (code-level check)."""

    def test_no_paperprice_lookups_in_quote_engine(self):
        """Quote engine uses direct FK refs only — no PaperPrice or attribute-based lookups."""
        quote_engine_path = Path(__file__).resolve().parent / "quote_engine.py"
        content = quote_engine_path.read_text()
        self.assertNotIn("PaperPrice", content)
        self.assertNotIn(".get(", content)
        self.assertNotIn("get_or_create", content)

    def test_no_paperprice_in_services(self):
        """No PaperPrice model or attribute-based paper lookups in shops app."""
        shops_dir = Path(__file__).resolve().parent
        for py_file in shops_dir.rglob("*.py"):
            if "test" in str(py_file).lower() or "migration" in str(py_file).lower():
                continue
            content = py_file.read_text()
            self.assertNotIn("PaperPrice", content, msg=f"Found PaperPrice in {py_file}")
            # Avoid attribute-based lookups like PaperPrice.objects.filter(paper=..., gsm=...)
            if "quote_engine" in str(py_file) or "views" in str(py_file) or "serializers" in str(py_file):
                self.assertNotRegex(
                    content,
                    r"PaperPrice\.objects\.(get|filter|get_or_create)",
                    msg=f"Attribute-based PaperPrice lookup in {py_file}",
                )
