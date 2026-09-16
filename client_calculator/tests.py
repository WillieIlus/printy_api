"""Contract tests for the client calculator adapter."""

from unittest.mock import patch

from django.urls import include, path
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .serializers import ClientCalculatorInputSerializer


urlpatterns = [
    path("api/client-calculator/", include("client_calculator.urls")),
]


@override_settings(ROOT_URLCONF=__name__)
class ClientCalculatorSerializerTests(APITestCase):
    def test_react_business_card_payload_normalizes_for_engine(self):
        serializer = ClientCalculatorInputSerializer(
            data={
                "productId": "business-cards",
                "quantity": 1000,
                "sizeId": "standard",
                "paperId": "board350",
                "colorMode": "COLOR",
                "sides": "DUPLEX",
                "finishingIds": ["lam-matt", "roundcorner"],
                "designId": "none",
                "deliveryId": "cbd",
                "rushId": "standard",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        payload = serializer.to_engine_payload()
        self.assertEqual(payload["product_type"], "business_card")
        self.assertEqual(payload["width_mm"], 90)
        self.assertEqual(payload["height_mm"], 55)
        self.assertEqual(payload["paper_gsm"], 350)
        self.assertEqual(payload["print_sides"], "DUPLEX")
        self.assertEqual(
            payload["finishing_slugs"], ["corner_rounding", "lamination"]
        )

    def test_booklet_pages_must_be_divisible_by_four(self):
        serializer = ClientCalculatorInputSerializer(
            data={
                "product_type": "booklets",
                "quantity": 250,
                "size_id": "a5",
                "pages": 30,
                "paper_key": "matt100",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("pages", serializer.errors)

    @patch("client_calculator.views.project_public_marketplace_response")
    @patch("client_calculator.views.build_public_calculator_preview")
    def test_preview_returns_buyer_contract(self, preview_mock, projection_mock):
        engine_result = {
            "currency": "KES",
            "exact_or_estimated": True,
            "can_calculate": True,
            "quantity": 1000,
            "totals": {
                "subtotal": "5000.00",
                "vat_amount": "800.00",
                "grand_total": "5800.00",
                "unit_price": "5.80",
            },
            "copies_per_sheet": 24,
            "good_sheets": 42,
            "parent_sheet_name": "SRA3",
            "calculation_result": {
                "line_items": [
                    {"code": "paper", "label": "Paper", "amount": "3000.00", "formula": "42 sheets x 71.43"},
                    {"code": "vat", "label": "VAT", "amount": "800.00", "formula": "VAT exclusive"},
                ],
                "warnings": [],
            },
        }
        preview_mock.return_value = engine_result
        projection_mock.side_effect = lambda value: value

        response = self.client.post(
            "/api/client-calculator/preview/",
            {
                "product_type": "business-cards",
                "quantity": 1000,
                "size_id": "standard",
                "paper_key": "board350",
                "color_mode": "COLOR",
                "sides": "DUPLEX",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["currency"], "KES")
        self.assertEqual(response.data["total"]["grand_total"], "5800.00")
        self.assertNotIn("vat", response.data["total"])
        line_codes = [line["code"] for line in response.data["line_items"]]
        self.assertIn("paper", line_codes)
        self.assertNotIn("vat", line_codes)
        self.assertEqual(response.data["production"]["copies_per_sheet"], 24)