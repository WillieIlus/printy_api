import unittest

raise unittest.SkipTest("Legacy pre-reset partner flow tests target removed settlement models/routes.")

from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User, UserProfile
from jobs.models import JobAssignment, JobFile, JobPayment, JobSettlementSplit, ManagedJob
from pricing.models import PlatformFeePolicy
from quotes.models import QuoteRequest, Quote
from shops.models import Shop


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    MPESA_BASE_URL="https://sandbox.safaricom.co.ke",
    MPESA_CALLBACK_URL="https://api.printy.ke/api/payments/mpesa/callback/",
    MPESA_CONSUMER_KEY="test-key",
    MPESA_CONSUMER_SECRET="test-secret",
    MPESA_SHORTCODE="174379",
    MPESA_PASSKEY="test-passkey",
    MPESA_ENV="sandbox",
)
class PartnerMediatedManagedJobFlowTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.partner = User.objects.create_user(
            email="e2e-partner@test.com",
            password="pass12345",
            role="broker",
            partner_profile_enabled=True,
            name="Flow Partner",
        )
        UserProfile.objects.update_or_create(
            user=self.partner,
            defaults={"default_markup_rate": PlatformFeePolicy().broker_margin_fee_rate},
        )
        self.end_client = User.objects.create_user(
            email="e2e-client@test.com",
            password="pass12345",
            role="client",
            name="Flow Client",
        )
        self.production_user = User.objects.create_user(
            email="e2e-production@test.com",
            password="pass12345",
            role="production",
            name="Flow Production",
        )
        self.ops = User.objects.create_user(
            email="e2e-ops@test.com",
            password="pass12345",
            is_staff=True,
            is_superuser=True,
        )
        self.shop = Shop.objects.create(
            owner=self.production_user,
            name="Flow Print Shop",
            slug="flow-print-shop",
            is_active=True,
        )

    def _pricing_snapshot(self):
        return {
            "currency": "KES",
            "selected_shops": [
                {
                    "id": self.shop.id,
                    "slug": self.shop.slug,
                    "preview": {
                        "totals": {"subtotal": "2000.00"},
                        "breakdown": {"imposition": {"good_sheets": 8}},
                    },
                }
            ],
        }

    def _calculator_inputs_snapshot(self):
        return {
            "product_type": "business_card",
            "quantity": 500,
            "finished_size": "90x50mm",
            "paper_stock": "300gsm_matte_art_card",
            "print_sides": "SIMPLEX",
            "color_mode": "COLOR",
            "pricing_mode": "SHEET",
        }

    def _stk_success_response(self):
        response = Mock()
        response.json.return_value = {
            "ResponseCode": "0",
            "ResponseDescription": "Success. Request accepted for processing",
            "CustomerMessage": "Success",
            "MerchantRequestID": "29115-34620561-1",
            "CheckoutRequestID": "ws_CO_123456789",
        }
        response.raise_for_status.return_value = None
        return response

    def _stk_callback_payload(self, amount="3200.00"):
        return {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "29115-34620561-1",
                    "CheckoutRequestID": "ws_CO_123456789",
                    "ResultCode": 0,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": float(amount)},
                            {"Name": "MpesaReceiptNumber", "Value": "TIH8QNX7PY"},
                            {"Name": "TransactionDate", "Value": 20260519120000},
                            {"Name": "PhoneNumber", "Value": 254712345678},
                        ]
                    },
                }
            }
        }

    def test_partner_can_create_client_then_create_quote(self):
        self.client.force_authenticate(user=self.partner)

        client_response = self.client.post(
            "/api/dashboard/partner/clients/",
            {
                "name": "Fresh Partner Client",
                "phone": "+254711222333",
                "email": "fresh-client@test.com",
                "company": "Fresh Co",
            },
            format="json",
        )
        self.assertEqual(client_response.status_code, 201)
        created_client_id = client_response.json()["client_id"]

        create_response = self.client.post(
            "/api/partner/quotes/create/",
            {
                "shop": self.shop.id,
                "title": "Partner managed flow with created client",
                "client_id": created_client_id,
                "client_name": "Fresh Partner Client",
                "client_email": "fresh-client@test.com",
                "client_phone": "+254711222333",
                "calculator_inputs_snapshot": self._calculator_inputs_snapshot(),
                "pricing_snapshot": self._pricing_snapshot(),
                "partner_markup": "600.00",
                "note": "Partner-originated managed quote.",
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        quote_request = QuoteRequest.objects.get(pk=create_response.json()["quote_request_id"])
        self.assertEqual(quote_request.on_behalf_of_id, created_client_id)

    @patch("jobs.payment_services.requests.post")
    @patch("jobs.payment_services.get_mpesa_token", return_value="test-token")
    def test_full_partner_mediated_job_flow(self, _token, mock_post):
        mock_post.return_value = self._stk_success_response()

        self.client.force_authenticate(user=self.partner)
        create_response = self.client.post(
            "/api/partner/quotes/create/",
            {
                "shop": self.shop.id,
                "title": "Partner managed flow",
                "client_id": self.end_client.id,
                "client_name": "Flow Client",
                "client_email": "e2e-client@test.com",
                "calculator_inputs_snapshot": self._calculator_inputs_snapshot(),
                "pricing_snapshot": self._pricing_snapshot(),
                "partner_markup": "600.00",
                "note": "Partner-originated managed quote.",
            },
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        quote_request = QuoteRequest.objects.get(pk=create_response.json()["quote_request_id"])
        quote = Quote.objects.get(pk=create_response.json()["quote"]["id"])
        self.assertEqual(quote_request.created_by_id, self.partner.id)
        self.assertEqual(quote_request.on_behalf_of_id, self.end_client.id)
        self.assertEqual(str(quote.total), "2000.00")

        send_response = self.client.post(
            f"/api/dashboard/partner/quotes/{quote_request.id}/send-to-client/",
            {
                "broker_margin_type": "percent",
                "broker_margin_value": "30",
                "platform_service_percent": "30",
            },
            format="json",
        )
        self.assertEqual(send_response.status_code, 200)
        quote.refresh_from_db()
        self.assertEqual(str(quote.client_total), "3200.00")
        self.assertEqual(str(quote.broker_margin_amount), "600.00")
        self.assertEqual(str(quote.platform_service_amount), "600.00")

        self.client.force_authenticate(user=self.end_client)
        quote_detail = self.client.get(f"/api/dashboard/client/quotes/{quote_request.id}/")
        self.assertEqual(quote_detail.status_code, 200)
        quote_payload = quote_detail.json()["quote"]["responses"][0]
        self.assertEqual(Decimal(str(quote_payload["total"])), Decimal("3200.00"))
        self.assertNotIn("production_base_price", str(quote_payload["response_snapshot"]))
        self.assertNotIn("broker_margin_amount", str(quote_payload["response_snapshot"]))
        self.assertEqual(quote_payload["shop_slug"], "partner")

        accept_response = self.client.post(f"/api/client/responses/{quote.id}/accept/", {}, format="json")
        self.assertEqual(accept_response.status_code, 200)
        managed_job = ManagedJob.objects.get(source_quote=quote)
        self.assertEqual(managed_job.client_id, self.end_client.id)
        self.assertEqual(str(managed_job.client_total), "3200.00")
        self.assertEqual(str(managed_job.production_total), "2000.00")
        self.assertEqual(str(managed_job.broker_payout), "600.00")
        self.assertEqual(str(managed_job.printy_fee), "600.00")

        stk_response = self.client.post(
            f"/api/managed-jobs/{managed_job.id}/payments/mpesa/stk-push/",
            {"phone_number": "0712345678", "amount": "1000.00"},
            format="json",
        )
        self.assertEqual(stk_response.status_code, 201)
        payment = JobPayment.objects.get(managed_job=managed_job)
        self.assertEqual(payment.expected_amount, Decimal("3200.00"))
        self.assertEqual(payment.amount, Decimal("3200.00"))
        self.assertEqual(payment.raw_gateway_payload["request"]["TransactionType"], "CustomerPayBillOnline")
        self.assertEqual(payment.raw_gateway_payload["request"]["BusinessShortCode"], "174379")
        self.assertEqual(payment.raw_gateway_payload["request"]["AccountReference"], f"MJ-{managed_job.id}")

        callback_response = self.client.post(
            "/api/payments/mpesa/callback/",
            self._stk_callback_payload(),
            format="json",
        )
        self.assertEqual(callback_response.status_code, 200)
        payment.refresh_from_db()
        managed_job.refresh_from_db()
        self.assertEqual(payment.payment_status, "paid")
        self.assertEqual(str(payment.received_amount), "3200.00")
        self.assertEqual(managed_job.payment_status, "confirmed")

        JobFile.objects.create(
            managed_job=managed_job,
            uploaded_by=self.end_client,
            original_filename="partner-flow-artwork.pdf",
            file_type="artwork",
            visibility="client",
            status="uploaded",
        )

        self.client.force_authenticate(user=self.partner)
        dispatch_response = self.client.post(f"/api/dashboard/partner/jobs/{managed_job.id}/dispatch/", {}, format="json")
        self.assertEqual(dispatch_response.status_code, 200)
        managed_job.refresh_from_db()
        assignment = JobAssignment.objects.get(managed_job=managed_job)
        self.assertIsNotNone(managed_job.dispatched_at)
        self.assertEqual(managed_job.dispatched_by_id, self.partner.id)
        self.assertEqual(managed_job.assigned_shop_id, self.shop.id)
        self.assertEqual(str(assignment.shop_payout), "2000.00")

        self.client.force_authenticate(user=self.production_user)
        self.assertEqual(self.client.post(f"/api/job-assignments/{assignment.id}/accept/", {}, format="json").status_code, 200)
        self.assertEqual(self.client.post(f"/api/job-assignments/{assignment.id}/mark-in-production/", {}, format="json").status_code, 200)
        self.assertEqual(self.client.post(f"/api/job-assignments/{assignment.id}/mark-finishing/", {}, format="json").status_code, 200)
        self.assertEqual(self.client.post(f"/api/job-assignments/{assignment.id}/mark-ready/", {}, format="json").status_code, 200)
        completed_response = self.client.post(f"/api/job-assignments/{assignment.id}/mark-completed/", {}, format="json")
        self.assertEqual(completed_response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, "completed")

        production_detail = self.client.get(f"/api/dashboard/production/jobs/{managed_job.id}/")
        self.assertEqual(production_detail.status_code, 200)
        production_pricing = production_detail.json()["job"]["pricing"]
        self.assertEqual(production_pricing["production_total"], "2000.00")
        self.assertIsNone(production_pricing["client_total"])
        self.assertIsNone(production_pricing["partner_commission"])

        self.client.force_authenticate(user=self.ops)
        settlement_response = self.client.get(f"/api/managed-jobs/{managed_job.id}/settlement/")
        self.assertEqual(settlement_response.status_code, 200)
        settlement = JobSettlementSplit.objects.get(managed_job=managed_job)
        self.assertEqual(str(settlement.production_amount), "2000.00")
        self.assertEqual(str(settlement.partner_commission), "600.00")
        self.assertEqual(str(settlement.platform_fee), "600.00")

        self.client.force_authenticate(user=None)
        tracking_response = self.client.get(f"/api/public/managed-jobs/track/{managed_job.tracking_token}/")
        self.assertEqual(tracking_response.status_code, 200)
        tracking_payload = tracking_response.json()
        self.assertIn("job_status", tracking_payload)
        self.assertNotIn("shop_name", tracking_payload)
        self.assertNotIn("production_total", tracking_payload)
        self.assertNotIn("broker_commission", tracking_payload)
        self.assertNotIn("platform_fee", tracking_payload)
import unittest

raise unittest.SkipTest("Legacy pre-reset partner flow tests target removed settlement models/routes.")
