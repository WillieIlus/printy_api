from decimal import Decimal
from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from payments.services import MpesaDarajaClient, MpesaDarajaError


def _daraja_settings():
    return {
        "MPESA_ENV": "sandbox",
        "MPESA_ENVIRONMENT": "sandbox",
        "MPESA_SHORTCODE_TYPE": "paybill",
        "MPESA_SHORTCODE": "174379",
        "MPESA_PASSKEY": "passkey",
        "MPESA_CONSUMER_KEY": "consumer-key",
        "MPESA_CONSUMER_SECRET": "consumer-secret",
        "MPESA_CALLBACK_URL": "https://api.printy.ke/api/payments/mpesa/callback/",
    }


@override_settings(**_daraja_settings())
class StkPushNetworkErrorTests(SimpleTestCase):
    @patch("payments.services.requests.get", side_effect=requests.exceptions.ConnectTimeout("token timeout"))
    def test_access_token_network_error_is_a_controlled_daraja_error(self, mock_get):
        client = MpesaDarajaClient()
        with self.assertRaises(MpesaDarajaError) as ctx:
            client.get_access_token()
        self.assertIn("M-Pesa access token request failed", str(ctx.exception))
        self.assertEqual(ctx.exception.response_code, "400")

    def test_stk_push_network_error_is_a_controlled_daraja_error_and_uses_capped_timeout(self):
        client = MpesaDarajaClient()
        with patch.object(client, "get_access_token", return_value="token"), patch(
            "payments.services.requests.post",
            side_effect=requests.exceptions.ConnectionError("sandbox unreachable"),
        ) as post:
            with self.assertRaises(MpesaDarajaError) as ctx:
                client.initiate_stk_push(
                    phone_number="254712345678",
                    amount=Decimal("3704"),
                    account_reference="PRINTY-1",
                    transaction_desc="Printy payment",
                )
            self.assertIn("M-Pesa STK push request failed", str(ctx.exception))
            self.assertEqual(ctx.exception.response_code, "400")
        timeout = post.call_args[1]["timeout"]
        self.assertIsInstance(timeout, tuple)
        self.assertLess(timeout[0], 10)
        self.assertLess(timeout[1], 30)