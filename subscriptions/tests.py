"""Subscription and payment tests."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from shops.models import Shop

from .models import MpesaStkRequest, Payment, Subscription, SubscriptionPlan


def _make_user(email="seller@test.com"):
    return User.objects.create_user(email=email, password="testpass123")


class SubscriptionPlanTests(TestCase):
    """Subscription plan model and API."""

    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name="Starter",
            price=Decimal("500"),
            billing_period=SubscriptionPlan.MONTHLY,
        )

    def test_days_in_period(self):
        self.assertEqual(self.plan.days_in_period(), 30)

    def test_plans_list_public(self):
        client = APIClient()
        r = client.get("/api/subscription/plans/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["name"], "Starter")
        self.assertEqual(data["results"][0]["price"], "500.00")
        self.assertEqual(data["results"][0]["days_in_period"], 30)


class StkPushTests(TestCase):
    """STK push initiates and stores request."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user()
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Test Shop",
            slug="test-shop",
            is_active=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            name="Pro",
            price=Decimal("1000"),
            billing_period=SubscriptionPlan.MONTHLY,
        )

    @patch("subscriptions.views.initiate_stk_push")
    def test_stk_initiate_stores_request(self, mock_stk):
        mock_stk.return_value = {"CheckoutRequestID": "ws_CO_123456"}
        self.client.force_authenticate(user=self.owner)

        r = self.client.post(
            f"/api/shops/{self.shop.slug}/payments/mpesa/stk-push/",
            {"phone": "254712345678", "plan_id": self.plan.id},
            format="json",
        )

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["checkout_request_id"], "ws_CO_123456")

        req = MpesaStkRequest.objects.get(checkout_request_id="ws_CO_123456")
        self.assertEqual(req.shop, self.shop)
        self.assertEqual(req.plan, self.plan)
        self.assertEqual(req.amount, Decimal("1000"))
        self.assertEqual(req.phone, "254712345678")
        self.assertEqual(req.status, MpesaStkRequest.INITIATED)

    @patch("subscriptions.views.initiate_stk_push")
    def test_stk_normalizes_phone(self, mock_stk):
        mock_stk.return_value = {"CheckoutRequestID": "ws_CO_789"}
        self.client.force_authenticate(user=self.owner)

        self.client.post(
            f"/api/shops/{self.shop.slug}/payments/mpesa/stk-push/",
            {"phone": "0712345678", "plan_id": self.plan.id},
            format="json",
        )

        mock_stk.assert_called_once()
        call_kwargs = mock_stk.call_args
        self.assertEqual(call_kwargs[1]["phone"], "254712345678")

    def test_stk_unauthorized(self):
        r = self.client.post(
            f"/api/shops/{self.shop.slug}/payments/mpesa/stk-push/",
            {"phone": "254712345678", "plan_id": self.plan.id},
            format="json",
        )
        self.assertEqual(r.status_code, 401)

    def test_stk_non_owner_forbidden(self):
        other = _make_user("other@test.com")
        self.client.force_authenticate(user=other)
        r = self.client.post(
            f"/api/shops/{self.shop.slug}/payments/mpesa/stk-push/",
            {"phone": "254712345678", "plan_id": self.plan.id},
            format="json",
        )
        self.assertEqual(r.status_code, 403)


class MpesaCallbackTests(TestCase):
    """Callback success updates subscription; idempotent on replay."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user()
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Test Shop",
            slug="test-shop",
            is_active=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            name="Pro",
            price=Decimal("1000"),
            billing_period=SubscriptionPlan.MONTHLY,
        )
        self.stk_req = MpesaStkRequest.objects.create(
            shop=self.shop,
            plan=self.plan,
            phone="254712345678",
            amount=Decimal("1000"),
            checkout_request_id="ws_CO_callback_test",
            status=MpesaStkRequest.INITIATED,
        )

    def _success_callback_payload(self):
        return {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": "ws_CO_callback_test",
                    "ResultCode": 0,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 1000},
                            {"Name": "MpesaReceiptNumber", "Value": "QGH12345"},
                            {"Name": "TransactionDate", "Value": 20250302120000},
                            {"Name": "PhoneNumber", "Value": 254712345678},
                        ]
                    },
                }
            }
        }

    def test_callback_success_updates_subscription(self):
        payload = self._success_callback_payload()
        r = self.client.post(
            "/api/payments/mpesa/callback/",
            payload,
            format="json",
        )

        self.assertEqual(r.status_code, 200)

        self.stk_req.refresh_from_db()
        self.assertEqual(self.stk_req.status, MpesaStkRequest.SUCCESS)
        self.assertEqual(self.stk_req.receipt_number, "QGH12345")

        sub = Subscription.objects.get(shop=self.shop)
        self.assertEqual(sub.plan, self.plan)
        self.assertEqual(sub.status, Subscription.ACTIVE)
        self.assertIsNotNone(sub.period_start)
        self.assertIsNotNone(sub.period_end)
        self.assertIsNotNone(sub.next_billing_date)
        self.assertIsNotNone(sub.last_payment_date)

        payment = Payment.objects.get(subscription=sub)
        self.assertEqual(payment.amount, Decimal("1000"))
        self.assertEqual(payment.receipt_number, "QGH12345")
        self.assertEqual(payment.status, Payment.COMPLETED)
        self.assertEqual(payment.request_id, "ws_CO_callback_test")

    def test_callback_idempotency_replay_safe(self):
        payload = self._success_callback_payload()

        r1 = self.client.post("/api/payments/mpesa/callback/", payload, format="json")
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.post("/api/payments/mpesa/callback/", payload, format="json")
        self.assertEqual(r2.status_code, 200)

        payment_count = Payment.objects.filter(subscription__shop=self.shop).count()
        self.assertEqual(payment_count, 1, "Replay must not create duplicate Payment")

        self.stk_req.refresh_from_db()
        self.assertEqual(self.stk_req.status, MpesaStkRequest.SUCCESS)

    def test_callback_failed_result_code(self):
        payload = {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": "ws_CO_callback_test",
                    "ResultCode": 1032,
                    "ResultDesc": "Request cancelled by user.",
                }
            }
        }

        r = self.client.post("/api/payments/mpesa/callback/", payload, format="json")
        self.assertEqual(r.status_code, 200)

        self.stk_req.refresh_from_db()
        self.assertEqual(self.stk_req.status, MpesaStkRequest.FAILED)
        self.assertEqual(Payment.objects.count(), 0)

    def test_callback_unknown_checkout_id(self):
        payload = {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": "ws_CO_unknown",
                    "ResultCode": 0,
                    "ResultDesc": "OK",
                }
            }
        }

        r = self.client.post("/api/payments/mpesa/callback/", payload, format="json")
        self.assertEqual(r.status_code, 200)
        self.stk_req.refresh_from_db()
        self.assertEqual(self.stk_req.status, MpesaStkRequest.INITIATED)


class ShopSubscriptionViewTests(TestCase):
    """GET shop subscription."""

    def setUp(self):
        self.client = APIClient()
        self.owner = _make_user()
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Test Shop",
            slug="test-shop",
            is_active=True,
        )

    def test_get_subscription_creates_trial(self):
        self.client.force_authenticate(user=self.owner)
        r = self.client.get(f"/api/shops/{self.shop.slug}/subscription/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "TRIAL")
        self.assertIn("plan", r.json())

    def test_get_subscription_unauthorized(self):
        r = self.client.get(f"/api/shops/{self.shop.slug}/subscription/")
        self.assertEqual(r.status_code, 401)
