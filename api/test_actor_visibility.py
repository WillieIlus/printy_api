from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from jobs.managed_job_actor_serializers import ManagedJobClientSerializer, ManagedJobShopSerializer
from jobs.models import JobAssignment, ManagedJob
from payments.models import Payment
from payments.payment_actor_serializers import PaymentClientSerializer
from pricing.models import PlatformFeePolicy
from quotes.financial_split_actor_serializers import QuoteFinancialSplitBrokerSerializer
from quotes.models import Quote, QuoteFinancialSplit, QuoteRequest
from quotes.quote_actor_serializers import QuoteBrokerSerializer, QuoteClientSerializer, QuoteShopSerializer
from quotes.quote_request_actor_serializers import QuoteRequestClientSerializer
from shops.models import Shop


class ActorVisibilityTestCase(TestCase):
    CLIENT_FORBIDDEN = {
        "production_cost",
        "production_base_price",
        "printy_fee",
        "printer_side_fee",
        "broker_margin_fee",
        "broker_payout",
        "broker_margin_amount",
        "broker_margin_percent",
        "platform_service_amount",
        "platform_service_percent",
        "shop_payout",
        "gross_margin",
        "internal_pricing_snapshot",
        "internal_sourcing_snapshot",
        "selected_shop_ids",
        "selected_shop",
        "selected_shop_id",
    }
    SHOP_FORBIDDEN = {
        "client_total",
        "broker_payout",
        "broker_margin",
        "broker_margin_amount",
        "broker_margin_percent",
        "gross_margin",
        "printy_fee",
        "platform_service_amount",
        "internal_pricing_snapshot",
        "client",
        "client_email",
        "client_phone",
    }
    BROKER_REQUIRED = {
        "production_cost",
        "printy_fee",
        "broker_payout",
        "client_total",
        "shop_payout",
        "gross_margin",
    }

    def setUp(self):
        self.client_user = User.objects.create_user(email="client@example.com", password="x", role=User.Role.CLIENT)
        self.broker_user = User.objects.create_user(email="broker@example.com", password="x", role=User.Role.PARTNER)
        self.shop_user = User.objects.create_user(email="shop@example.com", password="x", role=User.Role.PRODUCTION)
        self.shop = Shop.objects.create(name="Leak Test Shop", owner=self.shop_user)
        self.policy = PlatformFeePolicy.objects.create(name="Visibility Policy", is_active=True)
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            assigned_manager=self.broker_user,
            customer_name="Private Client",
            customer_email="private@example.com",
            customer_phone="+254700000000",
            request_snapshot={
                "selected_shop_ids": [self.shop.id],
                "selected_shop": {"id": self.shop.id, "name": self.shop.name},
                "production_cost": "1000.00",
                "printy_fee": "300.00",
                "calculator_inputs": {"quantity": 100},
            },
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            created_by=self.broker_user,
            status=Quote.SENT,
            total=Decimal("1500.00"),
            response_snapshot={
                "customer_pricing": {
                    "final_client_price": "1800.00",
                    "production_base_price": "1000.00",
                    "broker_margin_amount": "500.00",
                    "platform_service_amount": "300.00",
                },
                "internal_pricing_snapshot": {
                    "production_cost": "1000.00",
                    "printy_fee": "300.00",
                    "broker_payout": "200.00",
                    "shop_payout": "1000.00",
                },
            },
        )
        self.split = QuoteFinancialSplit.objects.create(
            quote=self.quote,
            policy_used=self.policy,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            gross_margin=Decimal("500.00"),
            printer_side_fee=Decimal("200.00"),
            broker_margin_fee=Decimal("150.00"),
            printy_fee=Decimal("350.00"),
            shop_payout=Decimal("1000.00"),
            broker_payout=Decimal("150.00"),
            client_total=Decimal("1500.00"),
            max_allowed_client_price=Decimal("4000.00"),
            applied_markup_multiple=Decimal("1.5000"),
        )
        self.job = ManagedJob.objects.create(
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.client_user,
            broker=self.broker_user,
            assigned_shop=self.shop,
            created_by=self.broker_user,
            title="Visibility Job",
            client_total=Decimal("1500.00"),
            printy_fee=Decimal("350.00"),
            broker_payout=Decimal("150.00"),
            operational_snapshot={"production_cost": "1000.00", "client_total": "1500.00"},
        )
        JobAssignment.objects.create(
            managed_job=self.job,
            assigned_shop=self.shop,
            source_quote=self.quote,
            shop_payout=Decimal("1000.00"),
            assignment_notes="Use supplied artwork.",
        )
        self.payment = Payment.objects.create(
            quote=self.quote,
            managed_job=self.job,
            payer=self.client_user,
            amount=Decimal("1500.00"),
            status=Payment.STATUS_PAID,
            mpesa_receipt_number="ABC123",
        )

    def _assert_no_forbidden(self, payload, forbidden, path="root"):
        if isinstance(payload, dict):
            for key, value in payload.items():
                self.assertNotIn(key, forbidden, f"{key} leaked at {path}")
                self._assert_no_forbidden(value, forbidden, f"{path}.{key}")
        elif isinstance(payload, list):
            for index, item in enumerate(payload):
                self._assert_no_forbidden(item, forbidden, f"{path}[{index}]")

    def test_client_quote_serializer_has_no_internal_fields(self):
        payload = QuoteClientSerializer(self.quote).data
        self._assert_no_forbidden(payload, self.CLIENT_FORBIDDEN)

    def test_client_quote_request_serializer_sanitizes_snapshot(self):
        payload = QuoteRequestClientSerializer(self.quote_request).data
        self._assert_no_forbidden(payload, self.CLIENT_FORBIDDEN)
        self.assertIn("public_draft_snapshot", payload)
        self.assertNotIn("internal_sourcing_snapshot", payload)

    def test_client_managed_job_has_no_broker_economics(self):
        payload = ManagedJobClientSerializer(self.job).data
        self._assert_no_forbidden(payload, self.CLIENT_FORBIDDEN)

    def test_shop_quote_and_job_have_no_client_economics(self):
        self._assert_no_forbidden(QuoteShopSerializer(self.quote).data, self.SHOP_FORBIDDEN)
        self._assert_no_forbidden(ManagedJobShopSerializer(self.job).data, self.SHOP_FORBIDDEN)

    def test_broker_quote_detail_includes_full_split(self):
        payload = QuoteBrokerSerializer(self.quote).data
        split = payload["financial_split"]
        for key in self.BROKER_REQUIRED:
            self.assertIn(key, split)
        direct_split = QuoteFinancialSplitBrokerSerializer(self.split).data
        for key in self.BROKER_REQUIRED:
            self.assertIn(key, direct_split)

    def test_payment_client_serializer_has_no_split_fields(self):
        payload = PaymentClientSerializer(self.payment).data
        self._assert_no_forbidden(payload, self.CLIENT_FORBIDDEN | self.SHOP_FORBIDDEN)

    def test_client_settlement_endpoint_excludes_internal_economics(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)

        response = api_client.get(reverse("managed-job-settlement", kwargs={"pk": self.job.id}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["managed_job_id"], self.job.id)
        self.assertIn("status", payload)
        for key in ("broker_payout", "printy_fee", "production_cost"):
            self.assertNotIn(key, payload)

    def test_shop_settlement_endpoint_excludes_internal_economics(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.shop_user)

        response = api_client.get(reverse("managed-job-settlement", kwargs={"pk": self.job.id}))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["managed_job_id"], self.job.id)
        self.assertEqual(payload["shop_payout"], "1000.00")
        for key in ("broker_payout", "printy_fee", "production_cost"):
            self.assertNotIn(key, payload)
