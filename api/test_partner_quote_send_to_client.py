from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from pricing.models import QuantityPricingTier
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote, QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PartnerQuoteSendToClientTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.partner = User.objects.create_user(
            email="send-quote-partner@test.com",
            password="pass12345",
            role="broker",
            partner_profile_enabled=True,
            name="Send Quote Partner",
        )
        self.end_client = User.objects.create_user(
            email="send-quote-client@test.com",
            password="pass12345",
            role="client",
            name="Send Quote Client",
        )
        self.shop_owner = User.objects.create_user(
            email="send-quote-shop@test.com",
            password="pass12345",
            role="shop_owner",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Send Quote Shop",
            slug="send-quote-shop",
            is_active=True,
        )
        QuantityPricingTier.objects.create(
            name="Default test tier",
            min_sheets=1,
            max_sheets=None,
            multiplier=Decimal("1.50"),
            minimum_order_floor=Decimal("700.00"),
        )

    def _production_cost_inputs(self):
        return {
            "quantity": 100,
            "yield_per_sheet": 10,
            "paper_cost_per_sheet": "10.00",
            "click_charge_per_sheet": "5.00",
            "finishing_cost": "0.00",
        }

    @patch("quotes.messaging.EmailMultiAlternatives.send")
    def test_send_to_client_supports_canonical_quantity_pricing(self, mock_send):
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.partner,
            on_behalf_of=self.end_client,
            customer_name="Send Quote Client",
            customer_email=self.end_client.email,
            status=QuoteStatus.DRAFT,
            request_snapshot={"source": "partner_quote_builder"},
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            created_by=self.partner,
            status=QuoteOfferStatus.PENDING,
            total=Decimal("1000.00"),
            response_snapshot={"production_cost_inputs": self._production_cost_inputs()},
        )
        self.client.force_authenticate(user=self.partner)

        response = self.client.post(
            f"/api/dashboard/partner/quotes/{quote_request.id}/send-to-client/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteOfferStatus.SENT)
        self.assertEqual(quote.client_quote_status, "sent")
        self.assertEqual(quote.response_snapshot["customer_pricing"]["gross_margin_type"], "quantity_tier")
        self.assertEqual(quote.response_snapshot["quantity_pricing_snapshot"]["final_client_price"], "700.00")
        self.assertEqual(str(quote.total), "700.00")
        self.assertEqual(str(quote.financial_split.client_total), "700.00")
        self.assertTrue(mock_send.called)
