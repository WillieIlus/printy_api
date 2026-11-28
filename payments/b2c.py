"""Daraja B2C (business-to-customer) client for automatic payouts -- Prompt 7 PART 2.

Only ``jobs.auto_payouts`` calls this. It sends one B2C payment request and
classifies the outcome so the caller can never pay twice:

- :class:`B2CNotSent` -- Daraja definitely did not accept the request (OAuth
  failed, connection never opened, HTTP 4xx, ResponseCode != "0"). Safe to
  retry with a NEW OriginatorConversationID.
- :class:`B2CAmbiguous` -- the request may have reached Daraja (read timeout,
  connection dropped mid-request, HTTP 5xx, unparseable body). Never retried
  automatically; the payout goes to needs_review.

Stub mode (``MPESA_FORCE_STUB`` / test settings, see
payments.services._is_stub_mode) never touches the network: the request is
"accepted" with a ``STUB-`` ConversationID and no result ever arrives, so the
payout stays processing and is swept to needs_review -- stub mode can never
fake a released payout.
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger("payments")

B2C_PATH = "/mpesa/b2c/v3/paymentrequest"


class B2CError(Exception):
    def __init__(self, message: str, *, payload=None):
        super().__init__(message)
        self.payload = payload


class B2CNotSent(B2CError):
    """Daraja did not accept the request; nothing can have moved."""


class B2CAmbiguous(B2CError):
    """Daraja may have accepted the request; outcome unknown."""


def _is_stub() -> bool:
    from payments.services import _is_stub_mode

    return bool(_is_stub_mode())


class B2CClient:
    def __init__(self):
        from payments.callback_security import signed_callback_url

        self.shortcode = str(getattr(settings, "MPESA_B2C_SHORTCODE", "") or "")
        self.initiator_name = str(getattr(settings, "MPESA_B2C_INITIATOR_NAME", "") or "")
        self.security_credential = str(getattr(settings, "MPESA_B2C_SECURITY_CREDENTIAL", "") or "")
        self.command_id = str(getattr(settings, "MPESA_B2C_COMMAND_ID", "") or "BusinessPayment")
        self.result_url = signed_callback_url(str(getattr(settings, "MPESA_B2C_RESULT_URL", "") or ""))
        self.timeout_url = signed_callback_url(str(getattr(settings, "MPESA_B2C_TIMEOUT_URL", "") or ""))
        self.stub = _is_stub()

    def _daraja(self):
        from payments.services import MpesaDarajaClient

        return MpesaDarajaClient()

    def send(self, *, msisdn: str, amount: int, originator_conversation_id: str, remarks: str, occasion: str = "") -> dict:
        payload = {
            "OriginatorConversationID": originator_conversation_id,
            "InitiatorName": self.initiator_name,
            "SecurityCredential": self.security_credential,
            "CommandID": self.command_id,
            "Amount": int(amount),
            "PartyA": self.shortcode,
            "PartyB": msisdn,
            "Remarks": (remarks or "Printy payout")[:100],
            "QueueTimeOutURL": self.timeout_url,
            "ResultURL": self.result_url,
            "Occasion": (occasion or "")[:100],
        }
        if self.stub:
            logger.info("B2C stub: not sending payout %s", originator_conversation_id)
            return {
                "ConversationID": f"STUB-{originator_conversation_id}"[:64],
                "OriginatorConversationID": originator_conversation_id,
                "ResponseCode": "0",
                "ResponseDescription": "Stub mode: no request sent to Daraja",
                "stub": True,
            }

        from django.core.exceptions import ImproperlyConfigured

        from payments.services import MpesaDarajaError

        try:
            client = self._daraja()
            token = client.get_access_token()
        except (ImproperlyConfigured, MpesaDarajaError) as exc:
            raise B2CNotSent(f"Daraja OAuth failed: {exc}") from exc
        url = f"{client.base_url}{B2C_PATH}"
        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                timeout=client.http_timeout,
            )
        except requests.exceptions.ConnectTimeout as exc:
            raise B2CNotSent(f"B2C connection was never opened: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise B2CAmbiguous(f"B2C request outcome unknown: {exc}") from exc
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code >= 500 or not isinstance(data, dict):
            raise B2CAmbiguous(
                f"B2C answered HTTP {response.status_code} without a usable body", payload=data or response.text[:500]
            )
        if response.status_code != 200 or str(data.get("ResponseCode")) != "0":
            raise B2CNotSent(
                str(data.get("errorMessage") or data.get("ResponseDescription") or f"HTTP {response.status_code}"),
                payload=data,
            )
        return data


def get_b2c_client() -> B2CClient:
    return B2CClient()
