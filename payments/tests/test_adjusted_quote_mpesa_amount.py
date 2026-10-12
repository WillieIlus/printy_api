"""BUG 2 regression: the STK charge must follow the accepted, adjusted quote.

A quote is priced once (the financial split is created when it is sent) and a
manager then adjusts ``Quote.total``. Acceptance used to lock the stale split,
so M-Pesa charged the original estimate. These tests pin the required behaviour:
the persisted, accepted ``quote.total`` is authoritative through the split,
``Payment.amount``/``expected_amount``, the STK payload and callback
reconciliation.
"""

from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api.quote_serializers import QuoteUpdateSerializer
from jobs.models import ManagedJob
from payments.models import Payment
from payments.services import (
    create_payment_for_quote,
    handle_stk_callback,
    initiate_stk_push,
)
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import (
    create_quote_financial_split,
    ensure_quote_financial_split,
)
from quotes.acceptance import accept_quote_for_payment
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import ProductionOption, Quote, QuoteRequest
from quotes.services_workflow import update_quote_response
from shops.models import Shop

User = get_user_model()


class AdjustedQuoteMpesaAmountTestCase(TestCase):
    PRODUCTION_COST = Decimal("2000.00")
    ORIGINAL_TOTAL = Decimal("4000.00")

    def setUp(self):
        self.client_user = User.objects.create_user(
            email="adjust-client@test.local", password="pass", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="adjust-manager@test.local", password="pass", role=User.Role.PARTNER
        )
        self.shop_owner = User.objects.create_user(
            email="adjust-shop@test.local", password="pass", role=User.Role.PRODUCTION
        )
        self.shop = Shop.objects.create(
            name="Adjustment Shop", slug="adjustment-shop", owner=self.shop_owner, is_active=True
        )
        self.policy = PlatformFeePolicy.objects.create(
            name="Adjustment policy",
            is_active=True,
            add_platform_fee_on_top=False,
        )
        self.quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            customer_name="Adjustment Client",
            customer_email="adjust-client@test.local",
            customer_phone="+254700000000",
            status=QuoteStatus.SUBMITTED,
        )
        self.production_option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=self.PRODUCTION_COST,
            created_by=self.manager,
            status=ProductionOption.SELECTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_option=self.production_option,
            created_by=self.manager,
            status=QuoteOfferStatus.SENT,
            total=self.ORIGINAL_TOTAL,
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )
        self.split = create_quote_financial_split(
            quote=self.quote,
            production_cost=self.PRODUCTION_COST,
            broker_client_price=self.ORIGINAL_TOTAL,
            production_option=self.production_option,
            policy=self.policy,
        )
        self.split.refresh_from_db()
        self.assertEqual(self.split.client_total, self.ORIGINAL_TOTAL)

    def _adjust_total(self, total: Decimal) -> None:
        self.quote.total = Decimal(total)
        self.quote.save(update_fields=["total", "updated_at"])

    def test_lower_adjustment_charges_the_adjusted_total(self):
        self._adjust_total(Decimal("3500.00"))

        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        self.assertEqual(payment.amount, Decimal("3500.00"))
        self.assertEqual(payment.expected_amount, Decimal("3500.00"))
        self.split.refresh_from_db()
        self.assertEqual(self.split.client_total, Decimal("3500.00"))

    def test_higher_adjustment_charges_the_adjusted_total(self):
        self._adjust_total(Decimal("4500.00"))

        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        self.assertEqual(payment.amount, Decimal("4500.00"))
        self.assertEqual(payment.expected_amount, Decimal("4500.00"))
        self.split.refresh_from_db()
        self.assertEqual(self.split.client_total, Decimal("4500.00"))

    def test_reconcile_preserves_cost_and_policy_and_recomputes_allocation(self):
        self._adjust_total(Decimal("3500.00"))

        accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        self.split.refresh_from_db()
        self.assertEqual(self.split.production_cost, self.PRODUCTION_COST)
        self.assertEqual(self.split.policy_used_id, self.policy.id)
        self.assertEqual(
            self.split.shop_payout + self.split.manager_payout + self.split.printy_fee,
            self.split.client_total,
        )
        self.assertEqual(self.split.client_total, Decimal("3500.00"))

    def test_stk_push_amount_matches_the_adjusted_total(self):
        self._adjust_total(Decimal("3500.00"))
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        with override_settings(MPESA_ENVIRONMENT="sandbox"):
            with patch("payments.services.MpesaDarajaClient") as MockClient:
                MockClient.return_value.initiate_stk_push.return_value = {
                    "CheckoutRequestID": "ws_CO_ADJUST",
                    "MerchantRequestID": "mr_ADJUST",
                    "ResponseCode": "0",
                    "ResponseDescription": "Success",
                }
                initiate_stk_push(payment=payment, phone_number="+254700000000")
                charged = MockClient.return_value.initiate_stk_push.call_args[1]["amount"]

        self.assertEqual(Decimal(charged), Decimal("3500"))

    def test_workflow_revise_then_accept_charges_revised_total(self):
        update_quote_response(
            response=self.quote,
            status=QuoteOfferStatus.REVISED,
            total=Decimal("4500.00"),
            note="Adjusted by manager",
        )
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.total, Decimal("4500.00"))

        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        self.assertEqual(payment.amount, Decimal("4500.00"))

    def test_serializer_revise_then_accept_charges_revised_total(self):
        serializer = QuoteUpdateSerializer(
            instance=self.quote, data={"total": "3500.00"}, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        self.quote.refresh_from_db()
        self.assertEqual(self.quote.total, Decimal("3500.00"))
        # The snapshot must be JSON-safe (the plain JSONField) and hold the
        # revised price; DjangoJSONEncoder stores the Decimal as a string.
        self.assertEqual(self.quote.response_snapshot.get("price"), "3500.00")

        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        self.assertEqual(payment.amount, Decimal("3500.00"))
        self.assertEqual(payment.expected_amount, Decimal("3500.00"))

    def test_view_partial_update_revise_then_accept_charges_revised_total(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.shop_owner)

        response = api_client.patch(
            reverse("sent-quote-detail", args=[self.quote.id]),
            {"total": "3500.00"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)

        self.quote.refresh_from_db()
        self.assertEqual(self.quote.total, Decimal("3500.00"))
        self.assertEqual(self.quote.status, QuoteOfferStatus.REVISED)

        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        self.assertEqual(payment.amount, Decimal("3500.00"))
        self.assertEqual(payment.expected_amount, Decimal("3500.00"))

    def test_accepted_split_is_locked_and_immutable(self):
        self._adjust_total(Decimal("3500.00"))
        accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        self.split.refresh_from_db()
        self.assertTrue(self.split.locked)
        with self.assertRaises(ValidationError):
            create_quote_financial_split(
                quote=self.quote,
                production_cost=self.PRODUCTION_COST,
                broker_client_price=Decimal("3600.00"),
                production_option=self.production_option,
                policy=self.policy,
            )

    def test_ensure_is_idempotent_after_acceptance(self):
        self._adjust_total(Decimal("3500.00"))
        accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        again = ensure_quote_financial_split(quote=self.quote)

        self.assertEqual(again.client_total, Decimal("3500.00"))
        self.split.refresh_from_db()
        self.assertEqual(self.split.client_total, Decimal("3500.00"))

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_client_submitted_amount_is_ignored_by_stk_view(self):
        self._adjust_total(Decimal("3500.00"))
        self.quote.refresh_from_db()
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)

        response = api_client.post(
            reverse("payment-stk-push"),
            {
                "quote_id": self.quote.id,
                "phone_number": "+254700000000",
                "amount": "1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        payment = Payment.objects.get(quote=self.quote)
        self.assertEqual(payment.amount, Decimal("3500.00"))
        self.assertEqual(payment.expected_amount, Decimal("3500.00"))

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_duplicate_initiation_reuses_a_single_payment(self):
        self._adjust_total(Decimal("3500.00"))
        _quote, first = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        second = create_payment_for_quote(quote=self.quote, payer=self.client_user)

        self.assertEqual(first.id, second.id)
        self.assertEqual(Payment.objects.filter(quote=self.quote).count(), 1)
        self.assertEqual(second.amount, Decimal("3500.00"))

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_incorrect_callback_amount_does_not_mark_payment_paid(self):
        self._adjust_total(Decimal("3500.00"))
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")

        with self.assertRaises(ValidationError):
            handle_stk_callback(
                callback_payload={
                    "Body": {
                        "stkCallback": {
                            "CheckoutRequestID": stk.checkout_request_id,
                            "MerchantRequestID": stk.merchant_request_id,
                            "ResultCode": 0,
                            "ResultDesc": "Success",
                            "CallbackMetadata": {
                                "Item": [
                                    {"Name": "Amount", "Value": "3499.00"},
                                    {"Name": "MpesaReceiptNumber", "Value": "QGWRONG1"},
                                ]
                            },
                        }
                    }
                }
            )

        payment.refresh_from_db()
        self.assertNotEqual(payment.status, Payment.STATUS_PAID)

    @override_settings(MPESA_ENVIRONMENT="test")
    def test_duplicate_success_callbacks_apply_once(self):
        self._adjust_total(Decimal("3500.00"))
        _quote, payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)
        stk = initiate_stk_push(payment=payment, phone_number="+254700000000")
        payload = {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": stk.checkout_request_id,
                    "MerchantRequestID": stk.merchant_request_id,
                    "ResultCode": 0,
                    "ResultDesc": "Success",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": "3500.00"},
                            {"Name": "MpesaReceiptNumber", "Value": "QGDUP1"},
                        ]
                    },
                }
            }
        }

        handle_stk_callback(callback_payload=payload)
        handle_stk_callback(callback_payload=payload)

        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(ManagedJob.objects.filter(source_quote=self.quote).count(), 1)
