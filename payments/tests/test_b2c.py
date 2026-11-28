"""Prompt 7 PART 2: the B2C client classifies every outcome as sent, definitely
not sent (safe to retry) or ambiguous (never auto-retried). No network."""

from __future__ import annotations

from unittest import mock

import requests
from django.test import SimpleTestCase, override_settings

from config.test_settings import TEST_B2C_SETTINGS
from payments.b2c import B2CAmbiguous, B2CClient, B2CNotSent
from payments.services import MpesaDarajaError


class _Resp:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@override_settings(**TEST_B2C_SETTINGS)
class B2CClientTests(SimpleTestCase):
    def client_for(self):
        client = B2CClient()
        client.stub = False
        daraja = mock.Mock(base_url="https://sandbox.safaricom.co.ke", http_timeout=10)
        daraja.get_access_token.return_value = "token"
        client._daraja = lambda: daraja
        return client, daraja

    def send(self, client):
        return client.send(msisdn="254711000002", amount=800, originator_conversation_id="PRINTY-PO1-A1",
                           remarks="Printy payout", occasion="shop")

    def test_accepted_request_payload(self):
        client, _ = self.client_for()
        with mock.patch("payments.b2c.requests.post", return_value=_Resp(200, {
            "ConversationID": "AG_1", "OriginatorConversationID": "PRINTY-PO1-A1", "ResponseCode": "0",
        })) as post:
            self.assertEqual(self.send(client)["ConversationID"], "AG_1")
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/mpesa/b2c/v3/paymentrequest"))
        self.assertEqual(body["OriginatorConversationID"], "PRINTY-PO1-A1")
        self.assertEqual(body["Amount"], 800)
        self.assertEqual(body["PartyB"], "254711000002")
        self.assertEqual(body["PartyA"], TEST_B2C_SETTINGS["MPESA_B2C_SHORTCODE"])
        self.assertTrue(body["ResultURL"].startswith("https://"))
        from django.conf import settings

        self.assertTrue(settings.MPESA_CALLBACK_SECRET)
        self.assertIn(settings.MPESA_CALLBACK_SECRET, body["ResultURL"])
        self.assertIn(settings.MPESA_CALLBACK_SECRET, body["QueueTimeOutURL"])

    def test_definitely_not_sent(self):
        cases = {
            "oauth": None,
            "connect_timeout": requests.exceptions.ConnectTimeout("no route"),
            "http_400": _Resp(400, {"errorMessage": "Invalid Access Token"}),
            "response_code_nonzero": _Resp(200, {"ResponseCode": "1", "ResponseDescription": "Rejected"}),
        }
        for name, outcome in cases.items():
            with self.subTest(name):
                client, daraja = self.client_for()
                if name == "oauth":
                    daraja.get_access_token.side_effect = MpesaDarajaError("bad credentials")
                    with mock.patch("payments.b2c.requests.post") as post, self.assertRaises(B2CNotSent):
                        self.send(client)
                    post.assert_not_called()
                    continue
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with mock.patch("payments.b2c.requests.post", **kwargs), self.assertRaises(B2CNotSent):
                    self.send(client)

    def test_ambiguous(self):
        cases = {
            "read_timeout": requests.exceptions.ReadTimeout("slow"),
            "connection_dropped": requests.exceptions.ConnectionError("reset by peer"),
            "http_500": _Resp(500, {"errorMessage": "Internal"}),
            "http_503_html": _Resp(503, ValueError("not json")),
            "200_not_json": _Resp(200, ValueError("not json")),
        }
        for name, outcome in cases.items():
            with self.subTest(name):
                client, _ = self.client_for()
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with mock.patch("payments.b2c.requests.post", **kwargs), self.assertRaises(B2CAmbiguous):
                    self.send(client)

    @override_settings(MPESA_FORCE_STUB=True)
    def test_stub_mode_never_touches_the_network(self):
        client = B2CClient()
        self.assertTrue(client.stub)
        with mock.patch("payments.b2c.requests.post") as post:
            response = self.send(client)
        post.assert_not_called()
        self.assertTrue(response["ConversationID"].startswith("STUB-"))
