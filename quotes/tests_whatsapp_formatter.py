"""Snapshot tests for WhatsApp quote message formatting."""
from decimal import Decimal

from django.test import TestCase

from accounts.models import User
from catalog.choices import PricingMode
from catalog.models import Product
from inventory.models import Paper
from pricing.choices import ChargeUnit
from pricing.models import FinishingCategory, FinishingRate
from shops.models import Shop

from quotes.models import QuoteItem, QuoteItemFinishing, QuoteRequest
from quotes.whatsapp_formatter import format_quote_for_whatsapp


class WhatsAppQuoteFormatterTestCase(TestCase):
    """Snapshot tests: assert exact message output for given inputs."""

    def setUp(self):
        self.user = User.objects.create_user(email="owner@test.com", password="pass")
        self.shop = Shop.objects.create(
            name="PrintPro Kenya",
            slug="printpro",
            phone_number="+254712345678",
            is_active=True,
            owner=self.user,
        )
        self.product = Product.objects.create(
            shop=self.shop,
            name="Business Card",
            pricing_mode=PricingMode.SHEET,
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            default_bleed_mm=3,
            default_sides="SIMPLEX",
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

    def test_single_item_snapshot(self):
        """Single SHEET item — stable output."""
        quote = QuoteRequest.objects.create(
            shop=self.shop,
            customer_name="Jane Doe",
            customer_email="jane@example.com",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=quote,
            item_type="PRODUCT",
            product=self.product,
            quantity=200,
            pricing_mode="SHEET",
            paper=self.paper,
            sides="DUPLEX",
            color_mode="COLOR",
            line_total=Decimal("5400.00"),
        )
        quote.total = Decimal("5400.00")

        msg = format_quote_for_whatsapp(
            quote,
            company_name="PrintPro Kenya",
            company_phone="+254712345678",
            turnaround="2-3 business days",
        )

        expected = """Hi Jane Doe,

Here is your quote:

• Business Card (90×55mm) × 200 pcs — SRA3 300gsm GLOSS = KES 5,400.00

Total: KES 5,400.00

Turnaround: 2-3 business days

Best regards,
PrintPro Kenya
+254712345678"""
        self.assertEqual(msg, expected)

    def test_multiple_items_snapshot(self):
        """Multiple items — stable output."""
        quote = QuoteRequest.objects.create(
            shop=self.shop,
            customer_name="Bob",
            status="DRAFT",
        )
        QuoteItem.objects.create(
            quote_request=quote,
            item_type="PRODUCT",
            product=self.product,
            quantity=100,
            pricing_mode="SHEET",
            paper=self.paper,
            line_total=Decimal("2700.00"),
        )
        QuoteItem.objects.create(
            quote_request=quote,
            item_type="PRODUCT",
            product=self.product,
            quantity=500,
            pricing_mode="SHEET",
            paper=self.paper,
            line_total=Decimal("6000.00"),
        )
        quote.total = Decimal("8700.00")

        msg = format_quote_for_whatsapp(
            quote,
            company_name="PrintPro Kenya",
            company_phone="",
            turnaround="3-5 days",
            payment_terms="50% deposit, balance on delivery",
        )

        self.assertIn("Hi Bob,", msg)
        self.assertIn("• Business Card (90×55mm) × 100 pcs", msg)
        self.assertIn("• Business Card (90×55mm) × 500 pcs", msg)
        self.assertIn("Total: KES 8,700.00", msg)
        self.assertIn("Turnaround: 3-5 days", msg)
        self.assertIn("Payment: 50% deposit, balance on delivery", msg)
        self.assertIn("PrintPro Kenya", msg)

    def test_item_with_finishing_snapshot(self):
        """Item with finishing — includes finishing in line."""
        cat, _ = FinishingCategory.objects.get_or_create(
            slug="lamination", defaults={"name": "Lamination"}
        )
        fr = FinishingRate.objects.create(
            shop=self.shop,
            name="Lamination",
            price=Decimal("2.50"),
            charge_unit=ChargeUnit.PER_PIECE,
            category=cat,
        )

        quote = QuoteRequest.objects.create(
            shop=self.shop,
            customer_name="Alice",
            status="DRAFT",
        )
        item = QuoteItem.objects.create(
            quote_request=quote,
            item_type="PRODUCT",
            product=self.product,
            quantity=100,
            pricing_mode="SHEET",
            paper=self.paper,
            line_total=Decimal("3200.00"),
        )
        QuoteItemFinishing.objects.create(quote_item=item, finishing_rate=fr)
        quote.total = Decimal("3200.00")

        msg = format_quote_for_whatsapp(
            quote,
            company_name="PrintPro",
            company_phone="",
            turnaround="2 days",
        )

        self.assertIn("Business Card", msg)
        self.assertIn("100 pcs", msg)
        self.assertIn("KES 3,200.00", msg)
        self.assertIn("Lamination", msg)

    def test_no_internal_costs(self):
        """Message must not reveal internal costs (paper_cost, print_cost, etc)."""
        quote = QuoteRequest.objects.create(
            shop=self.shop,
            customer_name="Test",
            status="DRAFT",
        )
        QuoteItem.objects.create(
            quote_request=quote,
            item_type="PRODUCT",
            product=self.product,
            quantity=100,
            paper=self.paper,
            line_total=Decimal("2500.00"),
        )
        quote.total = Decimal("2500.00")

        msg = format_quote_for_whatsapp(quote, company_name="Shop")

        # Must not contain internal cost terms
        self.assertNotIn("paper_cost", msg)
        self.assertNotIn("print_cost", msg)
        self.assertNotIn("margin", msg)
        self.assertNotIn("overhead", msg)
        # Only final price
        self.assertIn("2,500.00", msg)
