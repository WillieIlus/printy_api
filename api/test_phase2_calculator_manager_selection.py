import json

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from shops.models import Shop


class Phase2CalculatorManagerSelectionAPITestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client_user = User.objects.create_user(
            email="phase2-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
            name="Phase 2 Client",
        )
        self.shop_owner_only = User.objects.create_user(
            email="phase2-shop-owner@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
            name="Shop Owner Only",
        )
        Shop.objects.create(
            owner=self.shop_owner_only,
            name="Private Production Shop",
            slug="private-production-shop",
            is_active=True,
        )
        self.manager = User.objects.create_user(
            email="phase2-manager@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            name="Genuine Manager",
            partner_profile_enabled=True,
        )
        self.client.force_authenticate(user=self.client_user)

    def test_intake_submit_missing_source_returns_field_errors(self):
        response = self.client.post(
            "/api/intake/submit/",
            {"manager_selection_mode": "printy_auto"},
            format="json",
            HTTP_X_PRINTY_ACTIVE_ROLE="client",
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["code"], "VALIDATION_ERROR")
        self.assertIn("field_errors", payload)
        self.assertIn("non_field_errors", payload["field_errors"])
        self.assertIn("draft_id or calculator_inputs_snapshot is required", payload["field_errors"]["non_field_errors"][0])

    def test_recommended_managers_exclude_shop_owner_only_accounts_and_hide_shop_data(self):
        response = self.client.get(
            "/api/intake/recommended-managers/",
            {
                "product_type": "business_card",
                "quantity": 500,
                "paper_gsm": 300,
                "size": "90x55mm",
                "client_id": self.client_user.id,
            },
            HTTP_X_PRINTY_ACTIVE_ROLE="client",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        result_ids = [row["id"] for row in payload["results"]]
        self.assertIn(self.manager.id, result_ids)
        self.assertNotIn(self.shop_owner_only.id, result_ids)
        payload_text = json.dumps(payload)
        self.assertNotIn("Private Production Shop", payload_text)
        self.assertNotIn("private-production-shop", payload_text)
        self.assertNotIn("production_cost", payload_text)
        self.assertNotIn("platform_service_amount", payload_text)
        self.assertNotIn("broker_margin_amount", payload_text)
        self.assertNotIn("printy_fee", payload_text.lower())
