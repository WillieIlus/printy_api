"""M-Pesa Daraja API service — STK push."""
import logging
import re
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def normalize_phone(phone: str) -> str:
    """Normalize Kenyan phone to 2547XXXXXXXX format."""
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif digits.startswith("254") and len(digits) == 12:
        pass
    elif len(digits) == 9:
        digits = "254" + digits
    else:
        raise ValueError(f"Invalid phone: {phone}")
    return digits


def get_access_token() -> str:
    """Get OAuth access token from Daraja."""
    base = (getattr(settings, "MPESA_BASE_URL", "") or "https://sandbox.safaricom.co.ke").rstrip("/")
    url = f"{base}/oauth/v1/generate?grant_type=client_credentials"
    key = getattr(settings, "MPESA_CONSUMER_KEY", "") or ""
    secret = getattr(settings, "MPESA_CONSUMER_SECRET", "") or ""
    if not key or not secret:
        raise ValueError("MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET required")
    resp = requests.get(url, auth=(key, secret), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"]


def initiate_stk_push(
    phone: str,
    amount: Decimal | float,
    account_ref: str,
    description: str = "Printy subscription",
) -> dict[str, Any]:
    """
    Initiate M-Pesa STK push. Returns Daraja response with CheckoutRequestID.
    """
    base = (getattr(settings, "MPESA_BASE_URL", "") or "https://sandbox.safaricom.co.ke").rstrip("/")
    callback_url = getattr(settings, "MPESA_STK_CALLBACK_URL", "") or ""
    shortcode = getattr(settings, "MPESA_SHORTCODE", "") or ""
    passkey = getattr(settings, "MPESA_PASSKEY", "") or ""

    if not all([callback_url, shortcode, passkey]):
        raise ValueError("MPESA_STK_CALLBACK_URL, MPESA_SHORTCODE, MPESA_PASSKEY required")

    token = get_access_token()
    url = f"{base}/mpesa/stkpush/v1/processrequest"

    # Lipa Na M-Pesa timestamp format: YYYYMMDDHHmmss
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    password = f"{shortcode}{passkey}{timestamp}"
    import base64
    password_b64 = base64.b64encode(password.encode()).decode()

    payload = {
        "BusinessShortCode": int(shortcode),
        "Password": password_b64,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(Decimal(str(amount))),
        "PartyA": int(normalize_phone(phone)),
        "PartyB": int(shortcode),
        "PhoneNumber": int(normalize_phone(phone)),
        "CallBackURL": callback_url,
        "AccountReference": account_ref[:12],
        "TransactionDesc": description[:13],
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()
