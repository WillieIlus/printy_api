from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIClient

from quotes.models import CalculatorDraft, CalculatorDraftContext, CalculatorDraftIntent


class Step10FrontendApiSurfaceTestCase(TestCase):
    def test_frontend_contract_compatibility_urls_resolve(self):
        expected_names = {
            "/api/auth/login/": "login",
            "/api/jobs/": "client-jobs-compat",
            "/api/dashboard/manager/requests/": "dashboard-manager-requests",
            "/api/dashboard/partner/quotes/create/": "dashboard-partner-quote-create",
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
