"""API endpoint tests."""
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from catalog.choices import PricingMode, ProductStatus
from catalog.models import Product, ProductCategory
from inventory.models import Machine, Paper
from locations.models import Location
from pricing.choices import Sides
from pricing.models import Material, PrintingRate, VolumeDiscount
from quotes.choices import QuoteStatus
from quotes.models import QuoteItem, QuoteRequest
from shops.models import Shop


class SEOAPITestCase(TestCase):
    """Test public SEO endpoints — no auth required."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="s@t.com", password="pass")
        self.location = Location.objects.create(
            name="Westlands",
            slug="westlands",
            location_type="neighborhood",
            is_active=True,
        )
        self.shop = Shop.objects.create(
            owner=self.user,
            name="Test Shop",
            slug="test-shop",
            is_active=True,
            location=self.location,
            pricing_ready=True,
        )
        self.global_cat = ProductCategory.objects.create(
            shop=None,
            name="Business Cards",
            slug="business-cards",
            is_active=True,
        )
        self.product = Product.objects.create(
            shop=self.shop,
            category=self.global_cat,
            name="Business Card",
            slug="business-card",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            default_bleed_mm=3,
            default_sides=Sides.SIMPLEX,
            is_active=True,
            status=ProductStatus.PUBLISHED,
        )

    def test_seo_locations_list_no_auth_required(self):
        """GET /api/seo/locations/ returns active locations without auth."""
        r = self.client.get("/api/seo/locations/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["slug"], "westlands")
        self.assertEqual(data[0]["name"], "Westlands")
        self.assertIn("updated_at", data[0])

    def test_seo_products_list_no_auth_required(self):
        """GET /api/seo/products/ returns global categories without auth."""
        r = self.client.get("/api/seo/products/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["slug"], "business-cards")
        self.assertEqual(data[0]["name"], "Business Cards")

    def test_seo_routes_returns_canonical_urls(self):
        """GET /api/seo/routes/ returns loc and lastmod for sitemap."""
        r = self.client.get("/api/seo/routes/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        locs = [d["loc"] for d in data]
        self.assertIn("/", locs)
        self.assertIn("/locations", locs)
        self.assertIn("/products", locs)
        self.assertIn("/shops", locs)
        self.assertIn("/gallery", locs)
        self.assertIn("/locations/westlands", locs)
        self.assertIn("/products/business-cards", locs)
        self.assertIn("/locations/westlands/products/business-cards", locs)
        self.assertIn("/shops/test-shop", locs)
        for d in data:
            self.assertIn("loc", d)
            self.assertIn("lastmod", d)

    def test_seo_location_detail_returns_shops(self):
        """GET /api/seo/locations/{slug}/ returns location with shops."""
        r = self.client.get("/api/seo/locations/westlands/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["slug"], "westlands")
        self.assertEqual(data["name"], "Westlands")
        self.assertEqual(len(data["shops"]), 1)
        self.assertEqual(data["shops"][0]["slug"], "test-shop")

    def test_seo_location_products_returns_categories_in_location(self):
        """GET /api/seo/locations/{slug}/products/ returns product categories available in location."""
        r = self.client.get("/api/seo/locations/westlands/products/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["slug"], "business-cards")
        self.assertEqual(data[0]["name"], "Business Cards")

    def test_seo_location_detail_404_invalid_slug(self):
        """GET /api/seo/locations/{slug}/ returns 404 for invalid slug."""
        r = self.client.get("/api/seo/locations/nonexistent/")
        self.assertEqual(r.status_code, 404)

    def test_seo_product_detail_returns_product_count(self):
        """GET /api/seo/products/{slug}/ returns category with product_count."""
        r = self.client.get("/api/seo/products/business-cards/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["slug"], "business-cards")
        self.assertEqual(data["product_count"], 1)

    def test_seo_product_detail_404_invalid_slug(self):
        """GET /api/seo/products/{slug}/ returns 404 for invalid slug."""
        r = self.client.get("/api/seo/products/nonexistent/")
        self.assertEqual(r.status_code, 404)

    def test_seo_location_product_returns_shops(self):
        """GET /api/seo/locations/{loc}/products/{prod}/ returns shops offering category."""
        r = self.client.get("/api/seo/locations/westlands/products/business-cards/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["location"]["slug"], "westlands")
        self.assertEqual(data["category"]["slug"], "business-cards")
        self.assertEqual(len(data["shops"]), 1)
        self.assertEqual(data["shops"][0]["slug"], "test-shop")

    def test_seo_location_product_empty_shops_when_none_offer_category(self):
        """Location+product returns empty shops when no shop offers that category."""
        ProductCategory.objects.create(
            shop=None,
            name="Posters",
            slug="posters",
            is_active=True,
        )
        r = self.client.get("/api/seo/locations/westlands/products/posters/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["category"]["slug"], "posters")
        self.assertEqual(len(data["shops"]), 0)

    def test_seo_location_product_404_invalid_location(self):
        """GET /api/seo/locations/{loc}/products/{prod}/ returns 404 for invalid location."""
        r = self.client.get("/api/seo/locations/nonexistent/products/business-cards/")
        self.assertEqual(r.status_code, 404)

    def test_seo_location_product_404_invalid_product(self):
        """GET /api/seo/locations/{loc}/products/{prod}/ returns 404 for invalid product."""
        r = self.client.get("/api/seo/locations/westlands/products/nonexistent/")
        self.assertEqual(r.status_code, 404)


class PublicShopsAPITestCase(TestCase):
    """Test public shop and catalog endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="s@t.com", password="pass")
        self.shop = Shop.objects.create(
            owner=self.user, name="Test Shop", slug="test-shop", is_active=True
        )
        Product.objects.create(
            shop=self.shop,
            name="Business Card",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            default_bleed_mm=3,
            default_sides=Sides.SIMPLEX,
            is_active=True,
            status=ProductStatus.PUBLISHED,
        )

    def test_list_public_shops(self):
        response = self.client.get("/api/public/shops/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        results = data.get("results", data)  # Paginated or raw list
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "test-shop")

    def test_catalog_by_slug(self):
        response = self.client.get("/api/public/shops/test-shop/catalog/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("products", data)
        self.assertIn("shop", data)
        products = data["products"]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["name"], "Business Card")


class ShopsNearbyAPITestCase(TestCase):
    """Test GET /api/shops/nearby/ — bounding box geo search."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="s@t.com", password="pass")

    def test_missing_params_returns_empty_list(self):
        r = self.client.get("/api/shops/nearby/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"results": []})

    def test_missing_lng_returns_empty_list(self):
        r = self.client.get("/api/shops/nearby/?lat=-1.29")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"results": []})

    def test_missing_lat_returns_empty_list(self):
        r = self.client.get("/api/shops/nearby/?lng=36.82")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"results": []})

    def test_invalid_lat_returns_empty_list(self):
        r = self.client.get("/api/shops/nearby/?lat=invalid&lng=36.82")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"results": []})

    def test_invalid_lng_returns_empty_list(self):
        r = self.client.get("/api/shops/nearby/?lat=-1.29&lng=notanumber")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"results": []})

    def test_invalid_radius_returns_empty_list(self):
        r = self.client.get("/api/shops/nearby/?lat=-1.29&lng=36.82&radius=-5")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"results": []})

    def test_valid_returns_shops_within_bounding_box(self):
        # Nairobi center ~ -1.29, 36.82
        Shop.objects.create(
            owner=self.user,
            name="Shop A",
            slug="shop-a",
            is_active=True,
            latitude=Decimal("-1.29"),
            longitude=Decimal("36.82"),
        )
        Shop.objects.create(
            owner=User.objects.create_user(email="s2@t.com", password="pass"),
            name="Shop B",
            slug="shop-b",
            is_active=True,
            latitude=Decimal("-1.30"),
            longitude=Decimal("36.83"),
        )
        Shop.objects.create(
            owner=User.objects.create_user(email="s3@t.com", password="pass"),
            name="Shop No Geo",
            slug="shop-no-geo",
            is_active=True,
            latitude=None,
            longitude=None,
        )
        Shop.objects.create(
            owner=User.objects.create_user(email="s4@t.com", password="pass"),
            name="Shop Inactive",
            slug="shop-inactive",
            is_active=False,
            latitude=Decimal("-1.29"),
            longitude=Decimal("36.82"),
        )

        r = self.client.get("/api/shops/nearby/?lat=-1.29&lng=36.82&radius=10")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        results = data["results"]
        self.assertEqual(len(results), 2)
        slugs = {s["slug"] for s in results}
        self.assertEqual(slugs, {"shop-a", "shop-b"})
        self.assertIn("latitude", results[0])
        self.assertIn("longitude", results[0])
        self.assertIn("distance_km", results[0])

    def test_results_sorted_by_distance_ascending(self):
        # Origin at -1.29, 36.82. Shop A at origin (closest), Shop B farther, Shop C farthest
        Shop.objects.create(
            owner=self.user,
            name="Shop A",
            slug="shop-a",
            is_active=True,
            latitude=Decimal("-1.29"),
            longitude=Decimal("36.82"),
        )
        Shop.objects.create(
            owner=User.objects.create_user(email="s2@t.com", password="pass"),
            name="Shop B",
            slug="shop-b",
            is_active=True,
            latitude=Decimal("-1.35"),
            longitude=Decimal("36.85"),
        )
        Shop.objects.create(
            owner=User.objects.create_user(email="s3@t.com", password="pass"),
            name="Shop C",
            slug="shop-c",
            is_active=True,
            latitude=Decimal("-1.40"),
            longitude=Decimal("36.90"),
        )

        r = self.client.get("/api/shops/nearby/?lat=-1.29&lng=36.82&radius=50")
        self.assertEqual(r.status_code, 200)
        results = r.json()["results"]
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["slug"], "shop-a")
        self.assertEqual(results[1]["slug"], "shop-b")
        self.assertEqual(results[2]["slug"], "shop-c")
        self.assertLessEqual(results[0]["distance_km"], results[1]["distance_km"])
        self.assertLessEqual(results[1]["distance_km"], results[2]["distance_km"])

    def test_exact_radius_filters_out_shops_beyond_radius(self):
        # Origin at -1.29, 36.82. Shop A at origin. Shop B ~15km away.
        Shop.objects.create(
            owner=self.user,
            name="Shop A",
            slug="shop-a",
            is_active=True,
            latitude=Decimal("-1.29"),
            longitude=Decimal("36.82"),
        )
        Shop.objects.create(
            owner=User.objects.create_user(email="s2@t.com", password="pass"),
            name="Shop B",
            slug="shop-b",
            is_active=True,
            latitude=Decimal("-1.42"),
            longitude=Decimal("36.92"),
        )

        r = self.client.get("/api/shops/nearby/?lat=-1.29&lng=36.82&radius=5")
        self.assertEqual(r.status_code, 200)
        results = r.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["slug"], "shop-a")
        self.assertLessEqual(results[0]["distance_km"], 5)


class QuoteRequestAPITestCase(TestCase):
    """Test quote request buyer flow."""

    def setUp(self):
        self.client = APIClient()
        self.buyer = User.objects.create_user(email="b@t.com", password="pass")
        self.seller = User.objects.create_user(email="s@t.com", password="pass")
        self.shop = Shop.objects.create(
            owner=self.seller, name="Test Shop", slug="test-shop", is_active=True
        )
        self.product = Product.objects.create(
            shop=self.shop,
            name="Business Card",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            default_bleed_mm=3,
            default_sides=Sides.SIMPLEX,
            is_active=True,
        )

    def test_buyer_creates_and_submits_quote(self):
        self.client.force_authenticate(user=self.buyer)
        # Create
        r = self.client.post(
            "/api/quote-requests/",
            {"shop": self.shop.id, "customer_name": "Buyer", "customer_email": "b@t.com"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        qr_id = r.json()["id"]
        # Add item
        r2 = self.client.post(
            f"/api/quote-requests/{qr_id}/items/",
            {"product": self.product.id, "quantity": 100, "pricing_mode": PricingMode.SHEET},
            format="json",
        )
        self.assertEqual(r2.status_code, 201)
        # Submit
        r3 = self.client.post(f"/api/quote-requests/{qr_id}/submit/")
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.json()["status"], "SUBMITTED")


class QuoteStaffAPITestCase(TestCase):
    """Test staff-only quoting API (/api/quotes/)."""

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            email="staff@test.com", password="pass", is_staff=True
        )
        self.non_staff = User.objects.create_user(
            email="user@test.com", password="pass", is_staff=False
        )
        self.shop = Shop.objects.create(
            owner=self.staff,
            name="Test Shop",
            slug="test-shop",
            is_active=True,
        )
        self.machine = Machine.objects.create(
            shop=self.shop,
            name="Konica",
            machine_type="DIGITAL",
            max_width_mm=320,
            max_height_mm=450,
        )
        self.paper = Paper.objects.create(
            shop=self.shop,
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            buying_price=Decimal("15"),
            selling_price=Decimal("24"),
            width_mm=320,
            height_mm=450,
        )
        PrintingRate.objects.create(
            machine=self.machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("45"),
            double_price=Decimal("75"),
        )
        self.product = Product.objects.create(
            shop=self.shop,
            name="Business Card",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            default_bleed_mm=3,
            default_sides=Sides.SIMPLEX,
            min_quantity=100,
            is_active=True,
        )

    def test_non_staff_cannot_access_quotes(self):
        """Non-staff users cannot create or list quotes."""
        self.client.force_authenticate(user=self.non_staff)
        r = self.client.post(
            "/api/quotes/",
            {"shop": self.shop.id, "customer_name": "Customer"},
            format="json",
        )
        self.assertEqual(r.status_code, 403)
        r2 = self.client.get("/api/quotes/")
        self.assertEqual(r2.status_code, 403)

    def test_staff_creates_quote_draft(self):
        """Staff can create a quote draft via POST /api/quotes/."""
        self.client.force_authenticate(user=self.staff)
        r = self.client.post(
            "/api/quotes/",
            {
                "shop": self.shop.id,
                "customer_name": "John Doe",
                "customer_email": "john@example.com",
            },
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertEqual(data["status"], QuoteStatus.DRAFT)
        self.assertEqual(data["customer_name"], "John Doe")
        self.assertEqual(data["shop"], self.shop.id)

    def test_staff_adds_item_and_snapshots_breakdown(self):
        """Adding item computes and stores pricing_snapshot on QuoteItem."""
        self.client.force_authenticate(user=self.staff)
        # Create quote
        r = self.client.post(
            "/api/quotes/",
            {"shop": self.shop.id, "customer_name": "Jane"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        quote_id = r.json()["id"]
        # Add item with full calculator input
        r2 = self.client.post(
            f"/api/quotes/{quote_id}/items/",
            {
                "item_type": "PRODUCT",
                "product": self.product.id,
                "quantity": 200,
                "paper": self.paper.id,
                "machine": self.machine.id,
                "sides": "DUPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )
        self.assertEqual(r2.status_code, 201)
        item_data = r2.json()
        self.assertIn("pricing_snapshot", item_data)
        snapshot = item_data["pricing_snapshot"]
        self.assertTrue(snapshot.get("can_calculate"))
        self.assertIn("line_total", snapshot)
        self.assertIn("unit_price", snapshot)
        # Verify persisted
        item = QuoteItem.objects.get(pk=item_data["id"])
        self.assertIsNotNone(item.pricing_snapshot)
        self.assertIsNotNone(item.line_total)

    def test_send_quote_locks_snapshot(self):
        """POST /api/quotes/{id}/send/ marks SENT, locks pricing, stores whatsapp_message + sent_at."""
        self.client.force_authenticate(user=self.staff)
        # Create quote and add item
        r = self.client.post(
            "/api/quotes/",
            {"shop": self.shop.id, "customer_name": "Bob"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        quote_id = r.json()["id"]
        self.client.post(
            f"/api/quotes/{quote_id}/items/",
            {
                "item_type": "PRODUCT",
                "product": self.product.id,
                "quantity": 100,
                "paper": self.paper.id,
                "machine": self.machine.id,
                "sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )
        # Send quote (message is auto-generated)
        r_send = self.client.post(
            f"/api/quotes/{quote_id}/send/",
            {},
            format="json",
        )
        self.assertEqual(r_send.status_code, 200)
        data = r_send.json()
        self.assertEqual(data["status"], QuoteStatus.SENT)
        self.assertIn("Business Card", data["whatsapp_message"])
        self.assertIn("100 pcs", data["whatsapp_message"])
        self.assertIsNotNone(data["sent_at"])
        # Verify items are locked
        quote = QuoteRequest.objects.get(pk=quote_id)
        for item in quote.items.all():
            self.assertIsNotNone(item.pricing_locked_at)

    def test_whatsapp_preview_returns_message(self):
        """POST /api/quotes/{id}/whatsapp-preview/ returns { message }."""
        self.client.force_authenticate(user=self.staff)
        r = self.client.post(
            "/api/quotes/",
            {"shop": self.shop.id, "customer_name": "Preview Customer"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        quote_id = r.json()["id"]
        self.client.post(
            f"/api/quotes/{quote_id}/items/",
            {
                "item_type": "PRODUCT",
                "product": self.product.id,
                "quantity": 100,
                "paper": self.paper.id,
                "machine": self.machine.id,
                "sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )
        r_preview = self.client.post(
            f"/api/quotes/{quote_id}/whatsapp-preview/",
            {},
            format="json",
        )
        self.assertEqual(r_preview.status_code, 200)
        data = r_preview.json()
        self.assertIn("message", data)
        self.assertIn("Preview Customer", data["message"])
        self.assertIn("Business Card", data["message"])
        self.assertIn("Total:", data["message"])

    def test_share_returns_url_and_whatsapp_text(self):
        """POST /api/quotes/{id}/share/ returns { share_url, whatsapp_text }."""
        self.client.force_authenticate(user=self.staff)
        r = self.client.post(
            "/api/quotes/",
            {"shop": self.shop.id, "customer_name": "Share Customer"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        quote_id = r.json()["id"]
        self.client.post(
            f"/api/quotes/{quote_id}/items/",
            {
                "item_type": "PRODUCT",
                "product": self.product.id,
                "quantity": 100,
                "paper": self.paper.id,
                "machine": self.machine.id,
                "sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )
        r_share = self.client.post(
            f"/api/quotes/{quote_id}/share/",
            {},
            format="json",
        )
        self.assertEqual(r_share.status_code, 200)
        data = r_share.json()
        self.assertIn("share_url", data)
        self.assertIn("whatsapp_text", data)
        self.assertIn("/share/", data["share_url"])
        self.assertIn("Share Customer", data["whatsapp_text"])
        self.assertIn(data["share_url"], data["whatsapp_text"])

        # GET /api/share/<token>/ returns public summary
        token = data["share_url"].split("/share/")[-1].rstrip("/")
        r_public = self.client.get(f"/api/share/{token}/")
        self.assertEqual(r_public.status_code, 200)
        pub = r_public.json()
        self.assertEqual(pub["customer_name"], "Share Customer")
        self.assertEqual(pub["shop_name"], self.shop.name)
        self.assertIn("items", pub)
        self.assertEqual(len(pub["items"]), 1)
        self.assertEqual(pub["items"][0]["product_name"], "Business Card")


class QuoteCalculatorAPITestCase(TestCase):
    """Test POST /api/calculator/quote-item/ (staff-only)."""

    def setUp(self):
        self.client = APIClient()
        self.staff = User.objects.create_user(
            email="staff-calc@test.com", password="pass", is_staff=True
        )
        self.shop = Shop.objects.create(
            owner=self.staff, name="Calc Shop", slug="calc-shop", is_active=True
        )
        self.product = Product.objects.create(
            shop=self.shop,
            name="Business Card",
            pricing_mode="SHEET",
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            min_quantity=100,
            is_active=True,
        )
        self.paper = Paper.objects.create(
            shop=self.shop,
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            buying_price=Decimal("15"),
            selling_price=Decimal("24"),
            width_mm=320,
            height_mm=450,
            is_active=True,
        )

    def test_non_staff_forbidden(self):
        """Non-staff cannot access calculator."""
        user = User.objects.create_user(email="u@test.com", password="pass", is_staff=False)
        self.client.force_authenticate(user=user)
        r = self.client.post(
            "/api/calculator/quote-item/",
            {"product_id": self.product.id, "quantity": 100, "paper_id": self.paper.id},
            format="json",
        )
        self.assertEqual(r.status_code, 403)

    def test_staff_gets_calculator_result(self):
        """Staff gets JSON result with sheets_required, imposition, costs, lead_time."""
        self.client.force_authenticate(user=self.staff)
        r = self.client.post(
            "/api/calculator/quote-item/",
            {"product_id": self.product.id, "quantity": 100, "paper_id": self.paper.id},
            format="json",
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("sheets_required", data)
        self.assertIn("imposition", data)
        self.assertIn("costs", data)
        self.assertIn("lead_time_estimate_hours", data)
        self.assertTrue(data.get("can_calculate", True))
        self.assertIn("paper_cost", data["costs"])
        self.assertIn("suggested_price", data["costs"])


class PricingAPITestCase(TestCase):
    """Test shop pricing endpoints: papers, materials, volume discounts."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="owner@shop.com", password="pass")
        self.location = Location.objects.create(
            name="Nairobi",
            slug="nairobi",
            location_type="city",
            is_active=True,
        )
        self.shop = Shop.objects.create(
            owner=self.user,
            name="Test Print Shop",
            slug="test-print-shop",
            is_active=True,
            location=self.location,
            pricing_ready=False,
        )

    def test_papers_list_requires_auth(self):
        """GET /api/shops/{slug}/papers/ requires authentication."""
        r = self.client.get("/api/shops/test-print-shop/papers/")
        self.assertEqual(r.status_code, 401)

    def test_papers_list_owner_returns_empty(self):
        """GET /api/shops/{slug}/papers/ returns list for shop owner."""
        self.client.force_authenticate(user=self.user)
        r = self.client.get("/api/shops/test-print-shop/papers/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsInstance(data, (list, dict))
        if isinstance(data, dict):
            self.assertIn("results", data)


    def test_papers_create_owner_creates_paper(self):
        """POST /api/shops/{slug}/papers/ creates paper for owner."""
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            "/api/shops/test-print-shop/papers/",
            {"sheet_size": "A4", "gsm": 80, "paper_type": "GLOSS", "buying_price": "5", "selling_price": "10"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertEqual(data["sheet_size"], "A4")
        self.assertEqual(data["gsm"], 80)
        self.assertEqual(data["selling_price"], "10.00")

    def test_materials_list_requires_auth(self):
        """GET /api/shops/{slug}/materials/ requires authentication."""
        r = self.client.get("/api/shops/test-print-shop/materials/")
        self.assertEqual(r.status_code, 401)

    def test_materials_list_owner_returns_list(self):
        """GET /api/shops/{slug}/materials/ returns list for owner."""
        self.client.force_authenticate(user=self.user)
        r = self.client.get("/api/shops/test-print-shop/materials/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsInstance(data, (list, dict))

    def test_pricing_discounts_list_requires_auth(self):
        """GET /api/shops/{slug}/pricing/discounts/ requires authentication."""
        r = self.client.get("/api/shops/test-print-shop/pricing/discounts/")
        self.assertEqual(r.status_code, 401)

    def test_pricing_discounts_list_owner_returns_list(self):
        """GET /api/shops/{slug}/pricing/discounts/ returns list for owner."""
        self.client.force_authenticate(user=self.user)
        r = self.client.get("/api/shops/test-print-shop/pricing/discounts/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsInstance(data, list)

    def test_pricing_discounts_create_owner_creates_discount(self):
        """POST /api/shops/{slug}/pricing/discounts/ creates discount for owner."""
        self.client.force_authenticate(user=self.user)
        r = self.client.post(
            "/api/shops/test-print-shop/pricing/discounts/",
            {"name": "Bulk 500+", "min_quantity": 500, "discount_percent": "10"},
            format="json",
        )
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertEqual(data["name"], "Bulk 500+")
        self.assertEqual(data["min_quantity"], 500)
        self.assertEqual(str(data["discount_percent"]), "10.00")
