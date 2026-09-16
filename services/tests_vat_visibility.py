"""VAT visibility contract tests.

Asserts the pricing guarantee: VAT is always computed into ``grand_total`` by the
engine, but a standalone VAT figure (amount / rate / mode / line-item /
explanation) never reaches a buyer-facing response.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from api.serializers import PublicShopListSerializer
from api.visibility import project_production_intelligence, project_public_preview
from api.workflow_serializers import ProductionOptionReadSerializer
from quotes.models import ProductionOption, QuoteRequest
from services.pricing.engine import (
    PricingEngineResult,
    _resolve_vat_summary,
    strip_vat_detail,
)
from shops.models import Shop

User = get_user_model()


class StripVatDetailUnitTests(SimpleTestCase):
    def _payload(self):
        return {
            "vat": {"amount": "80.00", "rate": "16.00", "mode": "exclusive"},
            "totals": {
                "subtotal": "500.00",
                "vat_amount": "80.00",
                "vat": "80.00",
                "vat_mode": "exclusive",
                "grand_total": "580.00",
            },
            "breakdown": {
                "paper": {"label": "SRA3 300gsm gloss"},
                "vat": {"amount": "80.00", "rate": "16.00", "mode": "exclusive"},
            },
            "explanations": [
                "Paper: 42 sheets x 7.14.",
                "VAT: 80.00 (exclusive).",
            ],
            "explanation_lines": ["VAT: 80.00 (exclusive)."],
            "calculation_result": {
                "line_items": [
                    {"code": "paper", "label": "Paper", "amount": "500.00"},
                    {"code": "vat", "label": "VAT", "amount": "80.00"},
                ]
            },
        }

    def test_strip_vat_detail_keeps_grand_total_and_removes_separate_vat(self):
        stripped = strip_vat_detail(self._payload())
        self.assertEqual(stripped["totals"]["grand_total"], "580.00")
        self.assertNotIn("vat", stripped)
        self.assertNotIn("vat_amount", stripped["totals"])
        self.assertNotIn("vat", stripped["totals"])
        self.assertNotIn("vat_mode", stripped["totals"])
        self.assertNotIn("vat", stripped["breakdown"])
        self.assertEqual(stripped["explanations"], ["Paper: 42 sheets x 7.14."])
        self.assertEqual(stripped["explanation_lines"], [])
        codes = [line["code"] for line in stripped["calculation_result"]["line_items"]]
        self.assertEqual(codes, ["paper"])

    def test_strip_vat_detail_does_not_mutate_input(self):
        original = self._payload()
        strip_vat_detail(original)
        self.assertIn("vat", original)
        self.assertIn("vat_amount", original["totals"])

    def test_to_dict_expose_vat_detail_false_strips(self):
        result = PricingEngineResult(
            pricing_mode="SHEET",
            quantity=100,
            currency="KES",
            totals={
                "subtotal": "500.00",
                "vat_amount": "80.00",
                "vat": "80.00",
                "vat_mode": "exclusive",
                "grand_total": "580.00",
            },
            breakdown={"vat": {"mode": "exclusive"}},
            explanations=["VAT: 80.00 (exclusive)."],
            vat={"amount": "80.00", "mode": "exclusive"},
            explanation_lines=["VAT: 80.00 (exclusive)."],
        )
        full = result.to_dict()
        self.assertIn("vat_amount", full["totals"])
        stripped = result.to_dict(expose_vat_detail=False)
        self.assertNotIn("vat_amount", stripped["totals"])
        self.assertNotIn("vat", stripped["breakdown"])
        self.assertEqual(stripped["totals"]["grand_total"], full["totals"]["grand_total"])

    def test_resolve_vat_summary_still_folds_vat_into_grand_total(self):
        summary = _resolve_vat_summary(Shop(is_vat_enabled=True, vat_rate=Decimal("16.00"), vat_mode="exclusive"), Decimal("500"))
        self.assertEqual(summary["grand_total"], Decimal("580"))
        self.assertEqual(summary["vat_amount"], Decimal("80"))


class PublicPreviewVatUnitTests(SimpleTestCase):
    def test_project_public_preview_strips_vat_explanation_lines(self):
        projected = project_public_preview(
            {
                "quote_type": "flat",
                "quantity": 100,
                "warnings": ["VAT: 80.00 (exclusive)."],
                "explanations": [
                    "Paper: 42 sheets x 7.14.",
                    "VAT: 80.00 (exclusive).",
                ],
            }
        )
        self.assertEqual(projected["explanations"], ["Paper: 42 sheets x 7.14."])
        self.assertEqual(projected["warnings"], [])

    def test_project_production_intelligence_strips_vat_warnings(self):
        projected = project_production_intelligence(
            {
                "pieces_per_sheet": 24,
                "sheets_required": 42,
                "warnings": [
                    "VAT: 80.00 (exclusive).",
                    "Order will be cut.",
                ],
            }
        )
        self.assertEqual(projected["warnings"], ["Order will be cut."])


class ProductionOptionPrinterVatSerializerTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(email="vat-manager@test.com", password="pass12345", role="broker")
        self.printer_owner = User.objects.create_user(email="vat-printer@test.com", password="pass12345", role="shop_owner")
        self.printer_shop = Shop.objects.create(
            owner=self.printer_owner,
            name="Printer Shop",
            slug="vat-printer",
            currency="KES",
            is_active=True,
            is_vat_enabled=True,
            vat_rate=Decimal("16.00"),
            vat_mode="exclusive",
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=None,
            created_by=self.manager,
            customer_name="End Client",
            status=QuoteRequest.SUBMITTED,
        )

    def test_production_option_serializer_exposes_printer_vat_to_manager(self):
        option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.printer_shop,
            production_cost=Decimal("1000.00"),
            status=ProductionOption.CANDIDATE,
        )
        data = ProductionOptionReadSerializer(option).data
        self.assertTrue(data["printer_is_vat_enabled"])
        self.assertEqual(data["printer_vat_rate"], "16.00")
        self.assertEqual(data["printer_vat_mode"], "exclusive")

    def test_public_shop_serializer_excludes_vat_fields_from_buyers(self):
        fields = set(PublicShopListSerializer.Meta.fields)
        self.assertNotIn("is_vat_enabled", fields)
        self.assertNotIn("vat_rate", fields)
        self.assertNotIn("vat_mode", fields)