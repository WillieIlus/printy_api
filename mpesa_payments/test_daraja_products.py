"""Daraja product (short code type) handling.

A Daraja app is provisioned for exactly one product. Sending the wrong
TransactionType is rejected as "Invalid TransactionType", and sending a short
code or password to a BuyGoodsOnline app is rejected as "Wrong credentials" —
so the product has to be config-driven rather than hardcoded to paybill.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from mpesa_payments.models import MpesaPayment, MpesaPaymentStatus
from mpesa_payments.services import (
    MpesaConfigError,
    initiate_stk_push,
    mpesa_product,
    validate_production_config,
)

PAYBILL = dict(
    MPESA_ENV="sandbox",
    MPESA_SHORTCODE_TYPE="paybill",
    MPESA_CONSUMER_KEY="test-key",
    MPESA_CONSUMER_SECRET="test-secret",
    MPESA_SHORTCODE="174379",
    MPESA_PASSKEY="test-passkey",
    MPESA_CALLBACK_URL="https://example.com/api/payments/mpesa/callback/",
)

BUYGOODS = dict(
    MPESA_ENV="sandbox",
    MPESA_SHORTCODE_TYPE="buygoodsonline",
    MPESA_CONSUMER_KEY="test-key",
    MPESA_CONSUMER_SECRET="test-secret",
    MPESA_SHORTCODE="",
    MPESA_PASSKEY="",
    MPESA_CALLBACK_URL="https://example.com/api/payments/mpesa/callback/",
)


def _payment(amount="2500.00") -> MpesaPayment:
    return MpesaPayment.objects.create(
        amount=amount,
        phone_number="254712345678",
        account_reference="PRINTY",
        description="Printy payment",
    )


def _sent_payload(**overrides):
    settings_dict = {**PAYBILL, **overrides}
    payment = _payment()
    with override_settings(**settings_dict), patch(
        "mpesa_payments.services.requests.post"
    ) as post, patch("mpesa_payments.services.get_access_token", return_value="tok"):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "MerchantRequestID": "m",
            "CheckoutRequestID": "c",
            "CustomerMessage": "ok",
        }
        initiate_stk_push(payment, timestamp="20260101235959")
    return post.call_args.kwargs["json"]


class ProductResolutionTests(TestCase):
    def test_defaults_to_paybill(self):
        with override_settings(MPESA_SHORTCODE_TYPE=""):
            self.assertEqual(mpesa_product(), "paybill")

    def test_resolves_buygoods_spellings(self):
        for value in ("buygoodsonline", "buy-goods-online", "buy_goods_online", "BuyGoodsOnline"):
            with override_settings(MPESA_SHORTCODE_TYPE=value):
                self.assertEqual(mpesa_product(), "buygoodsonline")

    def test_unknown_product_is_rejected(self):
        # "stk-push" is what a .env once carried: not a Daraja product, and
        # silently falling back to paybill would hide the mismatch.
        with override_settings(MPESA_SHORTCODE_TYPE="stk-push"):
            with self.assertRaises(MpesaConfigError):
                mpesa_product()


class StkPayloadTests(TestCase):
    def test_paybill_sends_shortcode_and_password(self):
        payload = _sent_payload()
        self.assertEqual(payload["TransactionType"], "CustomerPayBillOnline")
        self.assertEqual(payload["BusinessShortCode"], "174379")
        self.assertEqual(payload["PartyB"], "174379")
        self.assertTrue(payload["Password"])

    def test_buygoodsonline_sends_empty_credentials(self):
        payload = _sent_payload(
            MPESA_SHORTCODE_TYPE="buygoodsonline", MPESA_SHORTCODE="", MPESA_PASSKEY=""
        )
        self.assertEqual(payload["TransactionType"], "CustomerBuyGoodsOnline")
        self.assertEqual(payload["BusinessShortCode"], "")
        self.assertEqual(payload["Password"], "")
        self.assertEqual(payload["PartyB"], "")

    def test_common_fields_survive_the_product_switch(self):
        payload = _sent_payload(
            MPESA_SHORTCODE_TYPE="buygoodsonline", MPESA_SHORTCODE="", MPESA_PASSKEY=""
        )
        self.assertEqual(payload["PhoneNumber"], "254712345678")
        self.assertEqual(payload["Amount"], 2500)
        self.assertEqual(payload["CallBackURL"], "https://example.com/api/payments/mpesa/callback/")


class CredentialRequirementTests(TestCase):
    def test_paybill_still_requires_shortcode_and_passkey(self):
        with override_settings(**{**PAYBILL, "MPESA_PASSKEY": ""}):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()

    def test_buygoodsonline_needs_no_passkey(self):
        with override_settings(**BUYGOODS):
            validate_production_config()  # must not raise

    def test_buygoodsonline_rejects_a_leftover_passkey(self):
        with override_settings(**{**BUYGOODS, "MPESA_PASSKEY": "leftover-paybill-passkey"}):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()

    def test_buygoodsonline_rejects_a_leftover_shortcode(self):
        with override_settings(**{**BUYGOODS, "MPESA_SHORTCODE": "174379"}):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()

    def test_buygoodsonline_still_requires_consumer_credentials(self):
        with override_settings(**{**BUYGOODS, "MPESA_CONSUMER_SECRET": ""}):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()

    def test_buygoodsonline_still_requires_a_callback_url(self):
        with override_settings(**{**BUYGOODS, "MPESA_CALLBACK_URL": ""}):
            with self.assertRaises(MpesaConfigError):
                validate_production_config()


class PaymentsClientProductTests(TestCase):
    """The payments.services Daraja client is the one the API actually uses."""

    def _payload(self, **overrides):
        from payments.services import MpesaDarajaClient

        with override_settings(**{**PAYBILL, **overrides}), patch(
            "payments.services.requests.post"
        ) as post, patch(
            "payments.services.MpesaDarajaClient.get_access_token", return_value="tok"
        ):
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"ResponseCode": "0"}
            client = MpesaDarajaClient()
            client.initiate_stk_push(
                phone_number="254712345678",
                amount=2500,
                account_reference="PRINTY",
                transaction_desc="Printy payment",
            )
        return post.call_args.kwargs["json"]

    def test_paybill_payload(self):
        payload = self._payload()
        self.assertEqual(payload["TransactionType"], "CustomerPayBillOnline")
        self.assertEqual(payload["BusinessShortCode"], "174379")
        self.assertTrue(payload["Password"])

    def test_buygoodsonline_payload(self):
        payload = self._payload(
            MPESA_SHORTCODE_TYPE="buygoodsonline", MPESA_SHORTCODE="", MPESA_PASSKEY=""
        )
        self.assertEqual(payload["TransactionType"], "CustomerBuyGoodsOnline")
        self.assertEqual(payload["BusinessShortCode"], "")
        self.assertEqual(payload["Password"], "")
        self.assertEqual(payload["PartyB"], "")

    def test_buygoodsonline_password_is_empty(self):
        from payments.services import MpesaDarajaClient

        with override_settings(**BUYGOODS):
            password, _ = MpesaDarajaClient().generate_password()
        self.assertEqual(password, "")


class StartupCheckProductTests(TestCase):
    def _evaluate(self, **overrides):
        from config.settings import _evaluate_mpesa_production_config

        args = dict(
            env="production",
            consumer_key="k",
            consumer_secret="s",
            shortcode="",
            passkey="",
            callback_url="https://api.printy.ke/api/payments/mpesa/callback/",
            product="buygoodsonline",
        )
        return _evaluate_mpesa_production_config(**{**args, **overrides})

    def test_buygoodsonline_production_passes_without_shortcode(self):
        ids = [m.id for m in self._evaluate()]
        self.assertNotIn("printy.E015", ids)
        self.assertNotIn("printy.E016", ids)

    def test_buygoodsonline_production_flags_a_leftover_shortcode(self):
        ids = [m.id for m in self._evaluate(shortcode="174379")]
        self.assertIn("printy.E015", ids)

    def test_paybill_production_still_requires_shortcode_and_passkey(self):
        ids = [m.id for m in self._evaluate(product="paybill")]
        self.assertIn("printy.E015", ids)
        self.assertIn("printy.E016", ids)


class StubIndependenceTests(TestCase):
    @override_settings(**BUYGOODS)
    def test_product_config_does_not_disturb_stub_mode(self):
        # The stub path must not depend on which Daraja product is configured.
        from payments.services import _is_stub_mode

        with override_settings(MPESA_FORCE_STUB="1"):
            self.assertTrue(_is_stub_mode())
        with override_settings(MPESA_FORCE_STUB=""):
            self.assertFalse(_is_stub_mode())
        self.assertEqual(MpesaPayment.objects.count(), 0)
        self.assertEqual(MpesaPaymentStatus.PENDING, "pending")
