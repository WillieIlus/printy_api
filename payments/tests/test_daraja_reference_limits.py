"""Daraja field limits on the canonical STK Push payload.

Safaricom refuses an entire STK Push request when ``AccountReference`` is longer
than 12 characters or ``TransactionDesc`` is longer than 13 — ``ResponseCode``
comes back non-zero with ``errorCode 20003``, so the customer is never prompted.
The canonical quote payment path used to send ``QUOTE-`` prefixed to the quote
reference, which is ``Q-YYYYMMDD-NNNN`` and therefore 21 characters. Every
canonical quote payment was rejected at Daraja.

These tests pin the two limits, and the shape properties the reference still has
to satisfy afterwards. Nothing in the codebase looks a payment up by
``account_reference`` — ``handle_stk_callback`` matches on
``checkout_request_id``/``merchant_request_id`` — but operators and customers
both read the value, so two quotes must never be indistinguishable on an
M-Pesa statement, and it must not change between retries of one payment.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from payments.models import MpesaSTKRequest, Payment
from payments.services import (
    DARAJA_ACCOUNT_REFERENCE_MAX,
    DARAJA_TRANSACTION_DESC_MAX,
    MpesaDarajaClient,
    _account_reference,
    handle_stk_callback,
    initiate_stk_push,
)
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.acceptance import accept_quote_for_payment
from quotes.choices import QuoteOfferStatus
from quotes.models import ProductionOption, Quote, QuoteRequest
from shops.models import Shop

User = get_user_model()


def _quote_stub(quote_id, *, created_at=None):
    """The only two attributes _account_reference() reads off a quote."""
    return SimpleNamespace(id=quote_id, created_at=created_at)


def _at(year, month, day):
    return timezone.make_aware(datetime(year, month, day, 12, 0, 0))


def _accepted_response():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"ResponseCode": "0"}
    return response


class AccountReferenceShapeTests(SimpleTestCase):
    """_account_reference() must be short, deterministic and collision-free."""

    def test_reference_is_within_the_daraja_limit(self):
        reference = _account_reference(_quote_stub(1, created_at=_at(2026, 10, 3)))

        self.assertLessEqual(len(reference), DARAJA_ACCOUNT_REFERENCE_MAX)
        self.assertEqual(reference, "Q261003-0001")

    def test_reference_is_deterministic_across_retries(self):
        """No clock read, no randomness — a retry must reproduce the value.

        The reference is written once when the payment is created, but a client
        retrying a quote acceptance must never see the statement line change.
        """
        quote = _quote_stub(42, created_at=_at(2026, 10, 3))

        first = _account_reference(quote)
        second = _account_reference(quote)

        self.assertEqual(first, second)
        self.assertLessEqual(len(first), DARAJA_ACCOUNT_REFERENCE_MAX)

    def test_quotes_created_on_the_same_day_get_distinct_references(self):
        created = _at(2026, 10, 3)

        references = [_account_reference(_quote_stub(quote_id, created_at=created)) for quote_id in range(1, 25)]

        self.assertEqual(len(set(references)), len(references))
        for reference in references:
            self.assertLessEqual(len(reference), DARAJA_ACCOUNT_REFERENCE_MAX)

    def test_only_the_quote_decides_the_reference(self):
        """This is the property a naive ``[:12]`` would have lost.

        Slicing ``QUOTE-Q-20261003-0001`` keeps ``QUOTE-Q-2026``, which every
        quote in the same year would share.
        """
        same_day = _at(2026, 10, 3)

        first = _account_reference(_quote_stub(1, created_at=same_day))
        second = _account_reference(_quote_stub(2, created_at=same_day))

        self.assertNotEqual(first, second)

    def test_ids_longer_than_four_digits_stay_legal_and_distinct(self):
        """A long id drops the date rather than being cut itself.

        Trimming the id would be the dangerous direction: 12345 and 1234567890
        share a leading "1234", so a cut id can collide with a longer one. The
        whole date is given up instead, which keeps every id exact.
        """
        created = _at(2026, 10, 3)

        references = [
            _account_reference(_quote_stub(quote_id, created_at=created))
            for quote_id in (9999, 10000, 12345, 1234567890)
        ]

        for reference in references:
            self.assertLessEqual(len(reference), DARAJA_ACCOUNT_REFERENCE_MAX)
        self.assertEqual(len(set(references)), len(references))

    def test_reference_without_a_created_at_is_still_legal(self):
        """A quote with no timestamp must not produce an oversized reference."""
        reference = _account_reference(_quote_stub(7))

        self.assertLessEqual(len(reference), DARAJA_ACCOUNT_REFERENCE_MAX)
        self.assertTrue(reference.endswith("-0007"))

    def test_naive_created_at_is_accepted(self):
        reference = _account_reference(_quote_stub(8, created_at=datetime(2026, 10, 3, 12, 0, 0)))

        self.assertLessEqual(len(reference), DARAJA_ACCOUNT_REFERENCE_MAX)
        self.assertEqual(reference, "Q261003-0008")


class CanonicalStkPayloadLimitTests(SimpleTestCase):
    """The payload itself must never carry an over-length field."""

    def _payload(self, *, account_reference, transaction_desc):
        with override_settings(
            MPESA_ENV="sandbox",
            MPESA_ENVIRONMENT="sandbox",
            MPESA_SHORTCODE_TYPE="paybill",
            MPESA_SHORTCODE="174379",
            MPESA_PASSKEY="passkey",
            MPESA_CONSUMER_KEY="consumer-key",
            MPESA_CONSUMER_SECRET="consumer-secret",
            MPESA_CALLBACK_URL="https://api.printy.ke/api/payments/mpesa/callback/",
        ):
            client = MpesaDarajaClient()
            with patch.object(client, "get_access_token", return_value="token"):
                with patch("payments.services.requests.post") as post:
                    post.return_value = _accepted_response()
                    client.initiate_stk_push(
                        phone_number="254712345678",
                        amount=Decimal("3704"),
                        account_reference=account_reference,
                        transaction_desc=transaction_desc,
                    )
                    return post.call_args[1]["json"]

    def test_reference_written_before_the_fix_is_clamped(self):
        """Rows stored with the old 21-character value must not be sent as-is."""
        payload = self._payload(
            account_reference="QUOTE-Q-20261003-0001",
            transaction_desc="Printy payment",
        )

        self.assertLessEqual(len(payload["AccountReference"]), DARAJA_ACCOUNT_REFERENCE_MAX)

    def test_generated_reference_reaches_the_payload_unchanged(self):
        """The clamp is a safety net — a legal reference is passed through as-is."""
        reference = _account_reference(_quote_stub(1, created_at=_at(2026, 10, 3)))

        payload = self._payload(
            account_reference=reference,
            transaction_desc="Printy payment",
        )

        self.assertEqual(payload["AccountReference"], reference)

    def test_transaction_desc_is_clamped_to_thirteen_characters(self):
        payload = self._payload(
            account_reference="Q261003-0001",
            transaction_desc="Printy payment for quote 12345",
        )

        self.assertEqual(len(payload["TransactionDesc"]), DARAJA_TRANSACTION_DESC_MAX)

    def test_configured_transaction_desc_default_fits_the_limit(self):
        """The shipped default must fit on its own, before any clamping.

        It used to ship as "Printy payment" — 14 characters, one over Daraja's
        limit, so the operator's statement text was being cut mid-word.
        """
        self.assertLessEqual(
            len(settings.MPESA_TRANSACTION_DESC_DEFAULT),
            DARAJA_TRANSACTION_DESC_MAX,
        )

    def test_blank_fields_fall_back_to_legal_defaults(self):
        """Daraja rejects an empty AccountReference, so the fallback must fit."""
        payload = self._payload(account_reference="", transaction_desc="")

        self.assertTrue(payload["AccountReference"])
        self.assertLessEqual(len(payload["AccountReference"]), DARAJA_ACCOUNT_REFERENCE_MAX)
        self.assertTrue(payload["TransactionDesc"])
        self.assertLessEqual(len(payload["TransactionDesc"]), DARAJA_TRANSACTION_DESC_MAX)


class QuotePaymentReferenceEndToEndTests(TestCase):
    """The reference a client actually pays against must be Daraja-legal."""

    def setUp(self):
        self.client_user = User.objects.create_user(email="client@example.com", password="pass", role=User.Role.CLIENT)
        self.broker = User.objects.create_user(email="broker@example.com", password="pass", role=User.Role.PARTNER)
        self.shop_owner = User.objects.create_user(email="shop@example.com", password="pass", role=User.Role.PRODUCTION)
        self.shop = Shop.objects.create(name="Reference Shop", owner=self.shop_owner, is_active=True)
        self.policy = PlatformFeePolicy.objects.create(
            name="Reference policy",
            is_active=True,
            printer_fee_rate=Decimal("0.10"),
            broker_margin_fee_rate=Decimal("0.50"),
            add_platform_fee_on_top=False,
        )

    def _new_quote(self):
        """Each quote needs its own request: accepting one closes the request."""
        quote_request = QuoteRequest.objects.create(
            created_by=self.client_user,
            customer_name="Client",
            customer_email="client@example.com",
            customer_phone="+254700000000",
        )
        production_option = ProductionOption.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=self.broker,
            status=ProductionOption.SELECTED,
        )
        quote = Quote.objects.create(
            quote_request=quote_request,
            shop=self.shop,
            production_option=production_option,
            created_by=self.broker,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1750.00"),
            sent_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=7),
        )
        split = create_quote_financial_split(
            quote=quote,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1750.00"),
            production_option=production_option,
            policy=self.policy,
        )
        return quote, split

    def _success_callback(self, stk, amount):
        return {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": stk.checkout_request_id,
                    "MerchantRequestID": stk.merchant_request_id,
                    "ResultCode": 0,
                    "ResultDesc": "Success",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": str(amount)},
                            {"Name": "MpesaReceiptNumber", "Value": "QGH7REF1"},
                        ]
                    },
                }
            }
        }

    def test_payment_and_stk_request_carry_a_legal_reference(self):
        quote, _split = self._new_quote()
        _quote, payment = accept_quote_for_payment(quote=quote, accepted_by=self.client_user)

        self.assertLessEqual(len(payment.account_reference), DARAJA_ACCOUNT_REFERENCE_MAX)
        self.assertTrue(payment.account_reference.endswith(f"-{quote.id:04d}"))

        with override_settings(MPESA_ENVIRONMENT="test"):
            stk = initiate_stk_push(payment=payment, phone_number="+254700000000")

        stk.refresh_from_db()
        self.assertEqual(stk.status, MpesaSTKRequest.STATUS_SENT)
        self.assertLessEqual(len(stk.account_reference), DARAJA_ACCOUNT_REFERENCE_MAX)

    def test_existing_quote_payment_flow_still_settles(self):
        """The behaviour the reference change must not disturb."""
        quote, split = self._new_quote()
        _quote, payment = accept_quote_for_payment(quote=quote, accepted_by=self.client_user)

        with override_settings(MPESA_ENVIRONMENT="test"):
            stk = initiate_stk_push(payment=payment, phone_number="+254700000000")

        result = handle_stk_callback(callback_payload=self._success_callback(stk, split.client_total))

        self.assertEqual(result["status"], "success")
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_PAID)
        self.assertEqual(payment.received_amount, split.client_total)
        self.assertEqual(payment.mpesa_receipt_number, "QGH7REF1")
        self.assertIsNotNone(payment.managed_job)

    def test_two_quote_payments_get_distinct_references(self):
        """Concurrent quote payments must not share an M-Pesa statement line."""
        first_quote, _first_split = self._new_quote()
        second_quote, _second_split = self._new_quote()

        _quote, first_payment = accept_quote_for_payment(quote=first_quote, accepted_by=self.client_user)
        _quote, second_payment = accept_quote_for_payment(quote=second_quote, accepted_by=self.client_user)

        self.assertNotEqual(first_payment.account_reference, second_payment.account_reference)
