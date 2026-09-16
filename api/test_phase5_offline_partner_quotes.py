from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from payments.models import Payment
from pricing.services.platform_fee_policy import calculate_financial_split
from quotes.models import QuoteRequest
from shops.models import Shop


class OfflinePartnerQuoteFlowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user(
            username="phase5-manager",
            email="phase5-manager@example.com",
            password="pw",
            role=User.Role.PARTNER,
        )
        self.shop_owner = User.objects.create_user(
            username="phase5-shop",
            email="phase5-shop@example.com",
            password="pw",
            role=User.Role.PRODUCTION,
        )
        self.client_user = User.objects.create_user(
            username="phase5-client",
            email="phase5-client@example.com",
            password="pw",
            role=User.Role.CLIENT,
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner,
            name="Phase 5 Eligible Shop",
            city="Nairobi",
            service_area="CBD",
            is_active=True,
            is_public=True,
        )
        self.api = APIClient()

    def _pricing_snapshot(self):
        return {
            "currency": "KES",
            "selected_shops": [
                {
                    "id": self.shop.id,
                    "slug": self.shop.slug,
                    "preview": {
                        "totals": {
                            "subtotal": "1000.00",
                            "grand_total": "1000.00",
                        },
                        "breakdown": {
                            "imposition": {
                                "good_sheets": 10,
                            },
                            "paper": {"total": "400.00", "quantity": 10},
                            "printing": {"total": "600.00", "quantity": 10},
                        },
                    },
                }
            ],
        }

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_manager_creates_sends_pays_and_client_claims_phone_only_offline_quote(self):
        self.api.force_authenticate(self.manager)

        client_response = self.api.post(
            "/api/dashboard/partner/clients/",
            {
                "name": "Walk In Client",
                "phone": "254700000001",
                "company": "Walk In Co",
            },
            format="json",
        )
        self.assertEqual(client_response.status_code, 201, client_response.data)
        self.assertIsNone(client_response.data["client_id"])
        self.assertTrue(client_response.data["is_offline"])

        draft_response = self.api.post(
            "/api/dashboard/partner/quotes/create/",
            {
                "shop": self.shop.id,
                "title": "Business cards for walk-in client",
                "client_name": "Walk In Client",
                "client_phone": "254700000001",
                "client_company": "Walk In Co",
                "calculator_inputs_snapshot": {
                    "product_type": "business_card",
                    "quantity": 100,
                    "finished_size": "90x50mm",
                },
                "pricing_snapshot": self._pricing_snapshot(),
                "partner_markup": "250.00",
                "save_as_draft": True,
            },
            format="json",
        )
        self.assertEqual(draft_response.status_code, 201, draft_response.data)

        quote_request_id = draft_response.data["quote_request_id"]
        send_response = self.api.post(
            f"/api/dashboard/partner/quotes/{quote_request_id}/send-to-client/",
            {
                "broker_margin_type": "fixed",
                "broker_margin_value": "250.00",
                "phone_number": "254700000001",
                "note": "Prepared for walk-in payment.",
            },
            format="json",
        )
        self.assertEqual(send_response.status_code, 200, send_response.data)
        self.assertTrue(send_response.data["offline_client"])
        self.assertIsNotNone(send_response.data["claim_token"])
        self.assertIsNotNone(send_response.data["payment"])

        quote_request = QuoteRequest.objects.get(pk=quote_request_id)
        quote = quote_request.quotes.get(pk=send_response.data["quote_id"])
        split = quote.financial_split
        expected = calculate_financial_split(
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1250.00"),
        )
        self.assertEqual(split.production_cost, expected.production_cost)
        self.assertEqual(split.client_total, expected.client_total)
        self.assertEqual(quote.total, expected.client_total)

        payment = Payment.objects.get(pk=send_response.data["payment"]["id"])
        self.assertIsNone(payment.payer_id)
        self.assertEqual(payment.quote_id, quote.id)
        self.assertEqual(payment.amount, expected.client_total)
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)
        self.assertEqual(payment.payer_phone, "254700000001")
        self.assertTrue(payment.checkout_request_id.startswith("TEST-CR-"))

        second_send = self.api.post(
            f"/api/dashboard/partner/quotes/{quote_request_id}/send-to-client/",
            {
                "broker_margin_type": "fixed",
                "broker_margin_value": "250.00",
                "phone_number": "254700000001",
            },
            format="json",
        )
        self.assertEqual(second_send.status_code, 200, second_send.data)
        self.assertEqual(second_send.data["payment"]["id"], payment.id)
        self.assertEqual(second_send.data["payment"]["checkout_request_id"], payment.checkout_request_id)

        self.api.force_authenticate(self.client_user)
        forbidden = self.api.post(
            "/api/dashboard/partner/clients/",
            {"name": "Client should not create partner CRM rows", "phone": "254700000002"},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 403)

        claim_response = self.api.post(
            "/api/quotes/offline-claim/",
            {"claim_token": send_response.data["claim_token"]},
            format="json",
        )
        self.assertEqual(claim_response.status_code, 200, claim_response.data)
        quote_request.refresh_from_db()
        self.assertEqual(quote_request.on_behalf_of_id, self.client_user.id)
        self.assertNotIn("pricing", claim_response.data)
        self.assertNotIn("payment", claim_response.data)

        client_quotes = self.api.get("/api/dashboard/client/quotes/")
        self.assertEqual(client_quotes.status_code, 200, client_quotes.data)
        claimed_row = next(row for row in client_quotes.data["results"] if row["id"] == quote_request_id)
        self.assertNotIn("response_snapshot", claimed_row["latest_response"])
        quote.refresh_from_db()
        customer_pricing = quote.response_snapshot["customer_pricing"]
        self.assertEqual(customer_pricing["final_client_price"], str(expected.client_total))
        self.assertNotIn("printy_fee", customer_pricing)
        self.assertNotIn("broker_payout", customer_pricing)
        self.assertNotIn("shop_payout", customer_pricing)
