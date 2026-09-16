from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIClient

from accounts.models import User

from api.workflow_serializers import (
    CalculatorDraftCreateSerializer,
    CalculatorDraftReadSerializer,
    CalculatorDraftUpdateSerializer,
    GuestCalculatorDraftSerializer,
)
from quotes.models import CalculatorDraft, CalculatorDraftContext, CalculatorDraftIntent, QuoteRequest, QuoteStatus
from quotes.services_workflow import _draft_pricing_snapshot


class Step10FrontendApiSurfaceTestCase(TestCase):
    def test_frontend_contract_compatibility_urls_resolve(self):
        expected_names = {
            "/api/auth/login/": "login",
            "/api/jobs/": "client-jobs-compat",
            "/api/dashboard/manager/requests/": "dashboard-manager-requests",
            "/api/dashboard/partner/quotes/create/": "dashboard-partner-quote-create",
            "/api/pricing/quote-financials/preview/": "quote-financial-preview",
            "/api/pricing/quotes/1/financials/": "quote-financial-detail",
            "/api/shop/assignments/1/accept/": "shop-assignment-accept-compat",
            "/api/shop/assignments/1/complete/": "shop-assignment-complete-compat",
        }

        for path, expected_name in expected_names.items():
            with self.subTest(path=path):
                self.assertEqual(resolve(path).url_name, expected_name)

    def test_public_calculator_preview_get_creates_no_draft(self):
        client = APIClient()

        response = client.get(
            "/api/calculator/public-preview/",
            {"product_type": "business_card", "quantity": 100},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CalculatorDraft.objects.count(), 0)

    def test_register_can_claim_guest_draft_by_session_and_id(self):
        guest_draft = CalculatorDraft.objects.create(
            guest_session_key="step10-session",
            title="Guest draft",
            calculator_context=CalculatorDraftContext.PUBLIC_GUEST,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot={"product_type": "business_card", "quantity": 100},
        )

        response = APIClient().post(
            "/api/auth/register/",
            {
                "email": "step10-client@example.com",
                "password": "pass12345",
                "name": "Step 10 Client",
                "role": "client",
                "session_key": "step10-session",
                "guest_draft_id": guest_draft.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        guest_draft.refresh_from_db()
        self.assertIsNotNone(guest_draft.user_id)
        self.assertEqual(guest_draft.guest_session_key, "")
        self.assertEqual(response.json()["claimed_guest_draft_id"], guest_draft.id)


class DashboardCalculatorPricingSnapshotTestCase(TestCase):
    def test_draft_serializers_preserve_pricing_snapshot(self):
        calculator_payload = {"product_type": "business_card", "quantity": 100}
        pricing_snapshot = {"currency": "KES", "min_price": 1200, "pricing_preview": {"total": 1200}}

        create_serializer = CalculatorDraftCreateSerializer(
            data={
                "calculator_inputs_snapshot": calculator_payload,
                "pricing_snapshot": pricing_snapshot,
            }
        )
        self.assertTrue(create_serializer.is_valid(), create_serializer.errors)
        self.assertEqual(create_serializer.validated_data["pricing_snapshot"], pricing_snapshot)

        update_serializer = CalculatorDraftUpdateSerializer(data={"pricing_snapshot": pricing_snapshot})
        self.assertTrue(update_serializer.is_valid(), update_serializer.errors)
        self.assertEqual(update_serializer.validated_data["pricing_snapshot"], pricing_snapshot)

        guest_serializer = GuestCalculatorDraftSerializer(
            data={
                "session_key": "guest-session",
                "calculator_inputs_snapshot": calculator_payload,
                "pricing_snapshot": pricing_snapshot,
            }
        )
        self.assertTrue(guest_serializer.is_valid(), guest_serializer.errors)
        self.assertEqual(guest_serializer.validated_data["pricing_snapshot"], pricing_snapshot)

    def test_draft_read_and_workflow_use_saved_pricing_snapshot(self):
        pricing_snapshot = {"currency": "KES", "min_price": 1200, "pricing_preview": {"total": 1200}}
        draft = CalculatorDraft.objects.create(
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
            calculator_inputs_snapshot={"product_type": "business_card", "quantity": 100},
            pricing_snapshot=pricing_snapshot,
        )

        self.assertEqual(_draft_pricing_snapshot(draft), pricing_snapshot)
        self.assertEqual(CalculatorDraftReadSerializer(draft).data["pricing_snapshot"], pricing_snapshot)


class QuoteRequestManagerResponseDefaultsTestCase(TestCase):
    def test_quote_request_insert_uses_non_null_manager_response_defaults(self):
        quote_request = QuoteRequest.objects.create(
            customer_name="Print Buyer",
            customer_email="print@buyer.ke",
            status=QuoteStatus.SUBMITTED,
        )

        self.assertEqual(quote_request.manager_response_note, "")
        self.assertEqual(quote_request.manager_response_state, "pending")


class PricingFinancialApiSurfaceTestCase(TestCase):
    def test_quote_financial_preview_returns_frontend_contract(self):
        user = User.objects.create_user(
            email="pricing-preview@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            "/api/pricing/quote-financials/preview/",
            {"production_cost": "1000.00", "manager_markup": "250.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        for field in (
            "currency",
            "production_cost",
            "manager_markup",
            "production_fee_component",
            "markup_fee_component",
            "printy_fee",
            "shop_payout",
            "manager_payout",
            "client_total",
            "pricing_tier",
            "policy_version",
        ):
            self.assertIsInstance(payload[field], str)
            self.assertNotEqual(payload[field], "")
