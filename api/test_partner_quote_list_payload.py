from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.models import Quote, QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PartnerQuoteListPayloadTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.partner = User.objects.create_user(
            email="partner-quote-list-payload@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
            name="Partner Payload",
        )
        self.buyer = User.objects.create_user(
            email="buyer-quote-list-payload@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            name="Payload Buyer",
        )
        self.shop_owner = User.objects.create_user(
            email="shop-quote-list-payload@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(
            name="Payload Print Shop",
            slug="payload-print-shop",
            owner=self.shop_owner,
        )

    def test_partner_quote_list_exposes_product_and_partner_pricing_fields(self):
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            assigned_manager=self.partner,
            customer_name="Print Buyer",
            status=QuoteRequest.SUBMITTED,
            request_snapshot={
                "calculator_inputs": {
                    "product_label": "Business cards",
                    "product_type": "business_cards",
                }
            },
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.shop_owner,
            status=Quote.SENT,
            total=Decimal("1500.00"),
            response_snapshot={"customer_pricing": {"final_client_price": "1500.00"}},
        )
        create_quote_financial_split(
            quote=quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
        )

        self.client.force_authenticate(user=self.partner)
        response = self.client.get("/api/dashboard/partner/quotes/")

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()["results"] if item["id"] == quote_request.id)
        self.assertEqual(row["product"], "Business cards")
        self.assertEqual(row["client_total"], "1500.00")
        self.assertEqual(row["production_cost"], "1000.00")
        self.assertEqual(row["gross_margin"], "500.00")
        self.assertEqual(row["manager_margin"], "500.00")
        self.assertEqual(row["margin_percent"], "50.00")



