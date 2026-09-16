import unittest

raise unittest.SkipTest("Legacy pre-reset quote workflow tests target removed PartnerClient routes.")

from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from api.models import PartnerClient
from notifications.models import Notification
from quotes.choices import QuoteStatus, QuoteOfferStatus
from quotes.models import CalculatorDraft, QuoteRequest, Quote
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class QuoteWorkflowRepairTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.partner = User.objects.create_user(
            email="repair-partner@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
            name="Repair Partner",
        )
        self.production_manager = User.objects.create_user(
            email="repair-production-manager@test.com",
            password="pass12345",
            role=User.Role.SHOP_OWNER,
            partner_profile_enabled=False,
            name="Production Manager",
        )
        self.end_client = User.objects.create_user(
            email="repair-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            name="Repair Client",
        )
        self.other_user = User.objects.create_user(
            email="repair-not-client@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
            name="Repair Client Production",
        )
        self.shop = Shop.objects.create(
            owner=self.production_manager,
            name="Repair Print Shop",
            slug="repair-print-shop",
            is_active=True,
        )

    def _calculator_inputs(self):
        return {
            "product_type": "business_card",
            "quantity": 100,
            "finished_size": "90x50mm",
            "paper_stock": "300gsm_matte_art_card",
            "print_sides": "SIMPLEX",
            "color_mode": "COLOR",
            "lamination": "none",
            "urgency_type": "standard",
        }

    def _pricing_snapshot(self):
        return {
            "currency": "KES",
            "selected_shops": [
                {
                    "id": self.shop.id,
                    "slug": self.shop.slug,
                    "preview": {
                        "totals": {"subtotal": "1000.00"},
                        "breakdown": {"imposition": {"good_sheets": 4}},
                    },
                }
            ],
        }

    def _create_client_draft(self):
        return CalculatorDraft.objects.create(
            user=self.end_client,
            title="Repair intake",
            calculator_inputs_snapshot=self._calculator_inputs(),
            pricing_snapshot=self._pricing_snapshot(),
            request_details_snapshot={"customer_name": "Repair Client", "customer_email": self.end_client.email},
        )

    def test_partner_client_search_finds_existing_client_users_only(self):
        self.client.force_authenticate(user=self.partner)

        response = self.client.get("/api/dashboard/partner/clients/?search=repair-client")

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertTrue(any(row["client_id"] == self.end_client.id for row in results))
        self.assertFalse(any(row.get("client_id") == self.other_user.id for row in results))

    def test_partner_client_search_uses_client_user_id_not_partner_client_row_id(self):
        PartnerClient.objects.create(
            partner=self.partner,
            client_user=self.end_client,
            name="Repair Client",
            email=self.end_client.email,
        )
        self.client.force_authenticate(user=self.partner)

        response = self.client.get("/api/dashboard/partner/clients/?search=repair-client")

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.json()["results"] if item["client_id"] == self.end_client.id)
        self.assertEqual(row["id"], self.end_client.id)

    @patch("quotes.messaging.EmailMultiAlternatives.send")
    def test_partner_can_send_quote_to_client_from_draft(self, mock_send):
        quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.partner,
            on_behalf_of=self.end_client,
            customer_name="Repair Client",
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
            response_snapshot={"pricing": {"grand_total": "1000.00"}},
        )
        self.client.force_authenticate(user=self.partner)

        response = self.client.post(
            f"/api/dashboard/partner/quotes/{quote_request.id}/send-to-client/",
            {"broker_margin_type": "fixed", "broker_margin_value": "300.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        quote.refresh_from_db()
        self.assertEqual(quote.status, QuoteOfferStatus.SENT)
        self.assertEqual(quote.sent_to_client_by_id, self.partner.id)
        self.assertEqual(quote.client_quote_status, "sent")
        self.assertEqual(str(quote.client_total), "1600.00")
        self.assertTrue(mock_send.called)

    def test_client_intake_assigned_to_production_manager_appears_in_manager_queue(self):
        draft = self._create_client_draft()
        self.client.force_authenticate(user=self.end_client)

        response = self.client.post(
            "/api/intake/submit/",
            {"draft_id": draft.id, "selected_manager_id": self.production_manager.id},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        quote_request = QuoteRequest.objects.get(pk=response.json()["intake_id"])
        self.assertEqual(quote_request.assigned_manager_id, self.production_manager.id)
        self.assertTrue(
            Notification.objects.filter(
                user=self.production_manager,
                object_type="quote_request",
                object_id=quote_request.id,
            ).exists()
        )

        self.client.force_authenticate(user=self.production_manager)
        queue_response = self.client.get("/api/dashboard/partner/quotes/")

        self.assertEqual(queue_response.status_code, 200)
        self.assertTrue(any(row["id"] == quote_request.id for row in queue_response.json()["results"]))

    @patch("quotes.messaging.EmailMultiAlternatives.send")
    def test_full_quote_round_trip(self, mock_send):
        draft = self._create_client_draft()
        self.client.force_authenticate(user=self.end_client)
        intake_response = self.client.post(
            "/api/intake/submit/",
            {"draft_id": draft.id, "selected_manager_id": self.partner.id},
            format="json",
        )
        self.assertEqual(intake_response.status_code, 201)
        quote_request_id = intake_response.json()["intake_id"]

        self.client.force_authenticate(user=self.partner)
        queue_response = self.client.get("/api/dashboard/partner/quotes/")
        self.assertEqual(queue_response.status_code, 200)
        self.assertTrue(any(row["id"] == quote_request_id for row in queue_response.json()["results"]))

        search_response = self.client.get("/api/dashboard/partner/clients/?search=repair-client")
        self.assertEqual(search_response.status_code, 200)
        self.assertTrue(any(row["client_id"] == self.end_client.id for row in search_response.json()["results"]))

        prepare_response = self.client.post(
            f"/api/dashboard/partner/quotes/{quote_request_id}/prepare/",
            {
                "shop": self.shop.id,
                "pricing_snapshot": self._pricing_snapshot(),
                "partner_markup": "300.00",
                "note": "Prepared repair quote.",
            },
            format="json",
        )
        self.assertEqual(prepare_response.status_code, 201)
        self.assertTrue(mock_send.called)

        self.client.force_authenticate(user=self.end_client)
        client_response = self.client.get("/api/dashboard/client/quotes/")

        self.assertEqual(client_response.status_code, 200)
        row = next(item for item in client_response.json()["results"] if item["id"] == quote_request_id)
        self.assertEqual(row["latest_response"]["status"], "sent")
import unittest

raise unittest.SkipTest("Legacy pre-reset quote workflow tests target removed PartnerClient routes.")
