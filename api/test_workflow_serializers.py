from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient, APIRequestFactory

from accounts.models import User
from api.dashboard_views import PartnerQuoteListDetailView
from api.workflow_serializers import QuoteRequestReadSerializer, QuoteResponseReadSerializer
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import Quote, QuoteRequest
from shops.models import Shop


class QuoteClientTotalSerializerRegressionTestCase(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.client_user = User.objects.create_user(
            email="serializer-client@test.com",
            password="pass12345",
            role="client",
            name="Serializer Client",
        )
        self.shop_owner = User.objects.create_user(
            email="serializer-shop@test.com",
            password="pass12345",
            role="shop_owner",
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Serializer Shop",
            slug="serializer-shop",
            is_active=True,
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=None,
            created_by=self.client_user,
            customer_name="Serializer Client",
            customer_email=self.client_user.email,
            status=QuoteStatus.SUBMITTED,
        )

    def _request(self):
        request = self.factory.get("/")
        request.user = self.client_user
        return request

    def _quote(self, *, total="1000.00", snapshot_total="1600.00"):
        return Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.shop_owner,
            status=QuoteOfferStatus.SENT,
            total=Decimal(total),
            response_snapshot={
                "customer_pricing": {
                    "final_client_price": snapshot_total,
                    "estimated_total": snapshot_total,
                },
                "pricing": {"grand_total": snapshot_total},
                "totals": {"grand_total": snapshot_total},
            },
        )

    def test_quote_response_serializer_uses_financial_split_client_total(self):
        quote = self._quote()
        create_quote_financial_split(
            quote=quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1600.00"),
        )

        payload = QuoteResponseReadSerializer(quote, context={"request": self._request()}).data

        self.assertEqual(Decimal(str(payload["total"])), Decimal("1600.00"))

    def test_quote_request_serializer_handles_latest_quote_without_financial_split(self):
        self._quote(snapshot_total="1550.00")

        payload = QuoteRequestReadSerializer(self.quote_request, context={"request": self._request()}).data

        self.assertEqual(Decimal(str(payload["latest_response"]["total"])), Decimal("1550.00"))

    def test_client_request_detail_does_not_select_missing_delivery_location(self):
        client = APIClient()
        client.force_authenticate(self.client_user)

        response = client.get(f"/api/client/requests/{self.quote_request.id}/")

        self.assertIn(response.status_code, {200, 403, 404})
        self.assertNotEqual(response.status_code, 500)

    def test_partner_quote_row_projects_financial_split_totals(self):
        partner = User.objects.create_user(
            email="serializer-partner@test.com",
            password="pass12345",
            role="partner",
            name="Serializer Partner",
        )
        self.quote_request.assigned_manager = partner
        self.quote_request.save(update_fields=["assigned_manager", "updated_at"])
        quote = self._quote(total="1000.00", snapshot_total="0.00")
        create_quote_financial_split(
            quote=quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1600.00"),
        )
        request = self.factory.get("/")
        request.user = partner

        row = PartnerQuoteListDetailView()._quote_row(self.quote_request, request=request)
        pricing = row["latest_response"]["response_snapshot"]["customer_pricing"]

        self.assertEqual(Decimal(str(pricing["final_client_price"])), Decimal("1600.00"))
        self.assertGreater(Decimal(str(pricing["broker_payout"])), Decimal("0.00"))
        self.assertNotIn("shop_payout", pricing)
