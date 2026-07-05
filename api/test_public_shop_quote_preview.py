from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from pricing.services.platform_fee_policy import calculate_financial_split
from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent
from quotes.models import CalculatorDraft
from services.production_matching import price_single_shop_for_submission
from shops.models import Shop


@override_settings(DIRECT_SHOP_STANDARD_MARKUP_RATE=Decimal("0.20"))
class PublicShopQuotePreviewTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.shop = self._create_shop_with_pricing()
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Direct shop preview policy",
            is_active=True,
            printer_fee_rate=Decimal("0.05"),
            broker_margin_fee_rate=Decimal("0.10"),
            add_platform_fee_on_top=False,
        )

    def _create_shop_with_pricing(self):
        owner = User.objects.create_user(
            email="direct-shop-owner@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        shop = Shop.objects.create(
            owner=owner,
            name="Direct Preview Shop",
            slug="direct-preview-shop",
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        machine = Machine.objects.create(
            shop=shop,
            name="Direct Preview Press",
            max_width_mm=320,
            max_height_mm=450,
            is_active=True,
        )
        Paper.objects.create(
            shop=shop,
            name="300gsm Gloss",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting",
            slug="cutting-direct-preview-shop",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )
        return shop

    def _payload(self):
        return {
            "session_key": "guest-direct-shop-session",
            "product_type": "business_card",
            "quantity": 100,
            "width_mm": 85,
            "height_mm": 55,
            "paper_gsm": 300,
            "paper_type": "gloss",
            "print_sides": "SIMPLEX",
            "colour_mode": "COLOR",
        }

    def _walk_keys(self, value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from self._walk_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_keys(child)

    def test_guest_gets_direct_shop_priced_preview_and_scoped_draft(self):
        response = self.client.post(
            "/api/public/shops/direct-preview-shop/quote-preview/",
            self._payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "priced")
        self.assertTrue(payload["can_price"])
        self.assertEqual(payload["shop"]["name"], "Direct Preview Shop")
        self.assertEqual(payload["shop"]["slug"], "direct-preview-shop")
        self.assertEqual(payload["price"]["currency"], "KES")
        self.assertIsNotNone(payload["price"]["total"])
        shop_row = price_single_shop_for_submission(shop=self.shop, payload=self._payload())
        production_cost = Decimal(str(shop_row["production_cost"]))
        expected_broker_client_price = (production_cost * Decimal("1.20")).quantize(Decimal("0.01"))
        expected_split = calculate_financial_split(
            production_cost=production_cost,
            broker_client_price=expected_broker_client_price,
        )
        self.assertEqual(Decimal(str(payload["price"]["total"])), expected_split["client_total"])
        self.assertEqual(payload["draft"]["calculator_context"], CalculatorDraftContext.PUBLIC_GUEST)
        self.assertEqual(payload["draft"]["intent"], CalculatorDraftIntent.PUBLIC_PREVIEW)
        self.assertEqual(payload["draft"]["shop_slug"], "direct-preview-shop")

        forbidden = {"production_cost", "estimated_production_cost", "estimated_shop_payout", "gross_margin", "shop_payout", "broker_payout", "printy_fee"}
        self.assertTrue(forbidden.isdisjoint(set(self._walk_keys(payload))))

        draft = CalculatorDraft.objects.get(pk=payload["draft"]["id"])
        self.assertIsNone(draft.user)
        self.assertEqual(draft.guest_session_key, "guest-direct-shop-session")
        self.assertEqual(draft.calculator_context, CalculatorDraftContext.PUBLIC_GUEST)
        self.assertEqual(draft.intent, CalculatorDraftIntent.PUBLIC_PREVIEW)
        self.assertEqual(draft.direct_intake_shop_id, self.shop.id)
        self.assertEqual(draft.intake_mode, CalculatorDraft.INTAKE_MODE_DIRECT_SHOP)
        self.assertEqual(draft.request_details_snapshot["direct_shop_intake"], True)
        self.assertEqual(draft.request_details_snapshot["shop_slug"], "direct-preview-shop")

    def test_guest_claim_preserves_direct_shop_scope_columns(self):
        preview_response = self.client.post(
            "/api/public/shops/direct-preview-shop/quote-preview/",
            self._payload(),
            format="json",
        )
        self.assertEqual(preview_response.status_code, 200)

        client_user = User.objects.create_user(
            email="direct-shop-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.client.force_authenticate(user=client_user)

        claim_response = self.client.post(
            "/api/calculator/drafts/claim/",
            {"session_key": "guest-direct-shop-session"},
            format="json",
        )

        self.assertEqual(claim_response.status_code, 200)
        payload = claim_response.json()
        self.assertEqual(payload["direct_intake_shop_id"], self.shop.id)
        self.assertEqual(payload["intake_mode"], CalculatorDraft.INTAKE_MODE_DIRECT_SHOP)

        draft = CalculatorDraft.objects.get(pk=preview_response.json()["draft"]["id"])
        self.assertEqual(draft.user_id, client_user.id)
        self.assertEqual(draft.guest_session_key, "")
        self.assertEqual(draft.direct_intake_shop_id, self.shop.id)
        self.assertEqual(draft.intake_mode, CalculatorDraft.INTAKE_MODE_DIRECT_SHOP)
        self.assertEqual(draft.request_details_snapshot["shop_slug"], "direct-preview-shop")

    def test_non_direct_client_draft_has_no_direct_shop_scope(self):
        client_user = User.objects.create_user(
            email="ordinary-draft-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.client.force_authenticate(user=client_user)

        response = self.client.post(
            "/api/calculator/drafts/",
            {
                "title": "Ordinary draft",
                "calculator_inputs_snapshot": {"quantity": 100, "custom_title": "Ordinary job"},
                "request_details_snapshot": {"customer_name": "Ordinary Client"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIsNone(payload["direct_intake_shop_id"])
        self.assertEqual(payload["intake_mode"], "")

        draft = CalculatorDraft.objects.get(pk=payload["id"])
        self.assertIsNone(draft.direct_intake_shop_id)
        self.assertEqual(draft.intake_mode, "")

    def test_existing_anonymized_shop_preview_still_hides_shop_identity(self):
        response = self.client.post(
            "/api/public/shops/direct-preview-shop/calculator-preview/",
            self._payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        forbidden = {"shop_id", "shop_name", "shop_slug", "shop", "slug", "name"}
        for collection_name in ("matches", "shops", "selected_shops", "shop_matches"):
            for item in payload.get(collection_name) or []:
                self.assertTrue(forbidden.isdisjoint(item.keys()))
        fixed = payload.get("fixed_shop_preview")
        if isinstance(fixed, dict):
            self.assertTrue(forbidden.isdisjoint(fixed.keys()))
