from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from payments.models import MpesaSTKRequest, Payment
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.acceptance import accept_quote_for_payment
from quotes.choices import QuoteOfferStatus
from quotes.models import ProductionOption, Quote, QuoteRequest
from shops.models import Shop


User = get_user_model()


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
@override_settings(MPESA_ENV="sandbox")
class ClientResponsePaymentStatusTestCase(TestCase):
    """Buyer offers dashboard: each accepted offer exposes its payment state so
    the client can re-prompt M-Pesa when the first attempt failed."""

    def setUp(self):
        self.client = APIClient()
        self.client_user = User.objects.create_user(
            email="response-client@example.com",
            password="pass",
            role=User.Role.CLIENT,
        )
        self.broker = User.objects.create_user(
            email="response-broker@example.com",
            password="pass",
            role=User.Role.PARTNER,
        )
        self.shop_owner = User.objects.create_user(
            email="response-shop@example.com",
            password="pass",
            role=User.Role.PRODUCTION,
        )
        self.shop = Shop.objects.create(name="Response Shop", owner=self.shop_owner, is_active=True)
        self.policy = PlatformFeePolicy.objects.create(
            name="Response policy",
            is_active=True,
            printer_fee_rate=Decimal("0.10"),
            broker_margin_fee_rate=Decimal("0.50"),
            add_platform_fee_on_top=False,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            customer_name="Client",
            customer_email="client@example.com",
            customer_phone="+254700000000",
        )
        self.production_option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=self.broker,
            status=ProductionOption.SELECTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_option=self.production_option,
            created_by=self.broker,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1750.00"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )
        self.split = create_quote_financial_split(
            quote=self.quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1750.00"),
            production_option=self.production_option,
            policy=self.policy,
        )

    def _responses(self):
        response = self.client.get(reverse("client-response-list"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        return payload

    def test_response_list_payment_is_null_when_no_payment_was_started(self):
        self.quote.status = QuoteOfferStatus.ACCEPTED
        self.quote.accepted_at = timezone.now()
        self.quote.save(update_fields=["status", "accepted_at", "updated_at"])
        self.client.force_authenticate(user=self.client_user)

        payload = self._responses()

        self.assertEqual(len(payload), 1)
        self.assertIsNone(payload[0]["payment"])

    def test_response_list_exposes_failed_payment_so_the_card_can_offer_a_retry(self):
        self.client.force_authenticate(user=self.client_user)
        _quote, payment = accept_quote_for_payment(
            quote=self.quote,
            accepted_by=self.client_user,
            payer_phone="+254700000000",
        )
        payment.status = Payment.STATUS_FAILED
        payment.save(update_fields=["status", "updated_at"])

        payload = self._responses()

        self.assertEqual(payload[0]["status"], "accepted")
        self.assertEqual(
            payload[0]["payment"],
            {"id": payment.id, "status": "failed", "payer_phone": "+254700000000"},
        )

    def test_response_list_exposes_paid_payment_so_the_card_greys_out(self):
        self.client.force_authenticate(user=self.client_user)
        _quote, payment = accept_quote_for_payment(
            quote=self.quote,
            accepted_by=self.client_user,
            payer_phone="+254700000000",
        )
        payment.status = Payment.STATUS_PAID
        payment.save(update_fields=["status", "updated_at"])

        payload = self._responses()

        self.assertEqual(payload[0]["payment"]["status"], "paid")

    def test_retry_by_payment_id_reuses_the_failed_payment(self):
        self.client.force_authenticate(user=self.client_user)
        _quote, payment = accept_quote_for_payment(
            quote=self.quote,
            accepted_by=self.client_user,
            payer_phone="+254700000000",
        )
        payment.status = Payment.STATUS_FAILED
        payment.save(update_fields=["status", "updated_at"])

        with patch("payments.services.MpesaDarajaClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            mock_instance.initiate_stk_push.return_value = {
                "CheckoutRequestID": "RETRY_REAL_789",
                "MerchantRequestID": "RETRY_MR_789",
                "ResponseCode": "0",
                "ResponseDescription": "Success. Request accepted for processing",
                "CustomerMessage": "Success. Request accepted for processing",
            }
            response = self.client.post(
                reverse("payment-stk-push"),
                {
                    "payment_id": payment.id,
                    "quote_id": self.quote.id,
                    "phone_number": "+254700000000",
                },
                format="json",
            )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["payment_id"], payment.id)
        self.assertEqual(payload["status"], "processing")
        self.assertEqual(payload["checkout_request_id"], "RETRY_REAL_789")

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PROCESSING)
        stk_request = MpesaSTKRequest.objects.filter(payment=payment).order_by("-requested_at", "-id").first()
        self.assertIsNotNone(stk_request)
        self.assertEqual(stk_request.status, MpesaSTKRequest.STATUS_SENT)