"""Safaricom Daraja client + STK Push orchestration.

All HTTP failures degrade to a typed exception the views can turn into a
safe 502. No exception in here may leak credentials or raw upstream bodies
into an API response.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import MpesaAccessToken, MpesaCallbackLog, MpesaPayment, MpesaPaymentStatus
from .signals import mpesa_payment_confirmed, mpesa_payment_failed

logger = logging.getLogger("payments")

SANDBOX_BASE = "https://sandbox.safaricom.co.ke"
PRODUCTION_BASE = "https://api.safaricom.co.ke"

# ResultCode semantics from Daraja's STK Push documentation.
RESULT_CODE_SUCCESS = "0"
RESULT_CODE_USER_CANCELLED = "1032"
RESULT_CODE_TIMEOUT = "1037"
RESULT_CODE_DS_TIMEOUT = "1032"  # some Daraja builds report 1032 for DS timeout too


class MpesaError(Exception):
    """Any failure talking to Daraja. Safe to show the message to a client."""


class MpesaConfigError(MpesaError):
    """Daraja credentials or callback URL are missing/unsafe for this env."""


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────

def _setting(name: str, default: str = "") -> str:
    value = getattr(settings, name, None) or default
    return value.strip() if isinstance(value, str) else str(value).strip()


def mpesa_base_url() -> str:
    override = _setting("MPESA_BASE_URL")
    if override:
        return override.rstrip("/")
    env = _setting("MPESA_ENV", "sandbox").lower()
    return PRODUCTION_BASE if env == "production" else SANDBOX_BASE


def validate_production_config() -> None:
    """Enforce the env rules in docs/env_vars.md before we ever call Daraja."""
    env = _setting("MPESA_ENV", "sandbox").lower()
    required = [
        "MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET",
        "MPESA_SHORTCODE", "MPESA_PASSKEY",
    ]
    missing = [name for name in required if not _setting(name)]
    if missing:
        raise MpesaConfigError(f"Missing Daraja settings: {', '.join(missing)}")

    callback = _setting("MPESA_CALLBACK_URL")
    if not callback:
        raise MpesaConfigError("MPESA_CALLBACK_URL is required.")

    if env == "production":
        if "localhost" in callback or "127.0.0.1" in callback:
            raise MpesaConfigError("MPESA_CALLBACK_URL cannot be localhost in production.")
        if not callback.startswith("https://"):
            raise MpesaConfigError("MPESA_CALLBACK_URL must be HTTPS in production.")


# ─────────────────────────────────────────────────────────────
# Phone normalization
# ─────────────────────────────────────────────────────────────

def normalize_msisdn(raw: str) -> str:
    """Accept 2547XXXXXXXX / 07XXXXXXXX / 7XXXXXXXX / +2547XXXXXXXX.

    Returns the 2547XXXXXXXX form Daraja requires. Raises ValidationError
    for anything that is not a plausible Kenyan mobile number.
    """
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        raise ValidationError({"phone_number": "Phone number is required."})

    if digits.startswith("254"):
        national = digits[3:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits

    if len(national) != 9 or not national.startswith(("7", "1")):
        raise ValidationError(
            {"phone_number": "Enter a valid Kenyan mobile number, e.g. 0712 345 678."}
        )
    return f"254{national}"


# ─────────────────────────────────────────────────────────────
# OAuth — token fetch + cache
# ─────────────────────────────────────────────────────────────

def _request_timeout() -> int:
    try:
        return int(_setting("MPESA_TIMEOUT_SECONDS", "30"))
    except ValueError:
        return 30


def get_access_token(force_refresh: bool = False) -> str:
    """Return a valid Daraja bearer token, caching until ~60s before expiry."""
    validate_production_config()

    if not force_refresh:
        cached = (
            MpesaAccessToken.objects.filter(expires_at__gt=timezone.now() + timezone.timedelta(seconds=60))
            .order_by("-expires_at")
            .first()
        )
        if cached:
            return cached.access_token

    key = _setting("MPESA_CONSUMER_KEY")
    secret = _setting("MPESA_CONSUMER_SECRET")
    response = requests.get(
        f"{mpesa_base_url()}/oauth/v1/generate?grant_type=client_credentials",
        auth=(key, secret),
        timeout=_request_timeout(),
    )
    if response.status_code != 200:
        logger.error("Daraja token request failed status=%s", response.status_code)
        raise MpesaError("Could not authenticate with M-Pesa. Please try again.")

    data = response.json()
    token = data.get("access_token")
    seconds = int(data.get("expires_in", "3599"))
    if not token:
        raise MpesaError("M-Pesa did not return an access token.")

    MpesaAccessToken.objects.create(
        access_token=token,
        expires_at=timezone.now() + timezone.timedelta(seconds=seconds),
    )
    return token


# ─────────────────────────────────────────────────────────────
# STK Push
# ─────────────────────────────────────────────────────────────

def _stk_password(shortcode: str, passkey: str, timestamp: str) -> str:
    """Daraja's Lipa na M-Pesa Online password = shortcode + passkey + timestamp."""
    return base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()


def _timestamp(now=None) -> str:
    now = now or timezone.now()
    return now.strftime("%Y%m%d%H%M%S")


def initiate_stk_push(payment: MpesaPayment, *, timestamp: str | None = None) -> MpesaPayment:
    """Send the STK Push request and record the Daraja identifiers.

    The payment row is only moved to `pending` if Daraja accepted the request.
    Accepting the request proves nothing about the money — only the callback does.
    """
    validate_production_config()

    shortcode = _setting("MPESA_SHORTCODE")
    passkey = _setting("MPESA_PASSKEY")
    callback_url = _setting("MPESA_CALLBACK_URL")
    stamp = timestamp or _timestamp()

    payload = {
        "BusinessShortCode": shortcode,
        "Password": _stk_password(shortcode, passkey, stamp),
        "Timestamp": stamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(payment.amount),  # Daraja expects whole shillings
        "PartyA": payment.phone_number,
        "PartyB": shortcode,
        "PhoneNumber": payment.phone_number,
        "CallBackURL": callback_url,
        "AccountReference": (payment.account_reference or "PRINTY")[:12],
        "TransactionDesc": (payment.description or "Printy payment")[:13],
    }

    try:
        response = requests.post(
            f"{mpesa_base_url()}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={"Authorization": f"Bearer {get_access_token()}"},
            timeout=_request_timeout(),
        )
    except requests.RequestException as exc:
        logger.error("Daraja STK push network error payment_id=%s error=%s", payment.id, exc)
        raise MpesaError("M-Pesa is unreachable right now. Please try again.") from exc

    data = _safe_json(response)

    if response.status_code != 200:
        # Daraja returns a human-readable errorMessage — surface it, it is
        # about the request, never about our credentials.
        message = data.get("errorMessage") or data.get("ResponseDescription") or "M-Pesa rejected the request."
        logger.warning("Daraja STK push rejected payment_id=%s status=%s msg=%s",
                       payment.id, response.status_code, message)
        payment.mark_failed(result_code=str(data.get("requestId", "")) or str(response.status_code),
                            result_desc=str(message), raw=data)
        raise MpesaError(str(message))

    checkout_request_id = data.get("CheckoutRequestID", "")
    merchant_request_id = data.get("MerchantRequestID", "")
    if not checkout_request_id or not merchant_request_id:
        logger.error("Daraja STK push missing identifiers payment_id=%s payload=%s", payment.id, data)
        raise MpesaError("M-Pesa returned an incomplete response.")

    payment.mark_push_sent(
        merchant_request_id=merchant_request_id,
        checkout_request_id=checkout_request_id,
        customer_message=str(data.get("CustomerMessage", "")),
    )
    logger.info(
        "STK push sent payment_id=%s checkout_request_id=%s merchant_request_id=%s amount=%s",
        payment.id, checkout_request_id, merchant_request_id, payment.amount,
    )
    return payment


def query_stk_status(payment: MpesaPayment) -> MpesaPayment:
    """Ask Daraja directly about a push whose callback never arrived.

    Used by the reconciliation command and the manual "check status" action.
    Only ever transitions OUT of `pending` — it can never invent a success.
    """
    if not payment.checkout_request_id:
        raise MpesaError("This payment has no M-Pesa session to query.")
    if payment.is_terminal:
        return payment

    validate_production_config()
    shortcode = _setting("MPESA_SHORTCODE")
    passkey = _setting("MPESA_PASSKEY")
    stamp = _timestamp()

    try:
        response = requests.post(
            f"{mpesa_base_url()}/mpesa/stkpushquery/v1/query",
            json={
                "BusinessShortCode": shortcode,
                "Password": _stk_password(shortcode, passkey, stamp),
                "Timestamp": stamp,
                "CheckoutRequestID": payment.checkout_request_id,
            },
            headers={"Authorization": f"Bearer {get_access_token()}"},
            timeout=_request_timeout(),
        )
    except requests.RequestException as exc:
        logger.error("Daraja query network error payment_id=%s error=%s", payment.id, exc)
        raise MpesaError("Could not reach M-Pesa to check this payment.") from exc

    data = _safe_json(response)
    code = str(data.get("ResultCode", ""))
    description = str(data.get("ResultDesc", ""))

    if code == RESULT_CODE_SUCCESS:
        # A query result of 0 means the user completed the prompt. The
        # receipt number arrives in the callback, so mark pending-review if
        # the callback has not landed yet — never `paid` on query alone.
        payment.status = MpesaPaymentStatus.NEEDS_REVIEW
        payment.result_code = code
        payment.result_desc = description or "Query succeeded; awaiting callback receipt."
        payment.save(update_fields=["status", "result_code", "result_desc", "updated_at"])
        return payment

    if code in {RESULT_CODE_USER_CANCELLED, RESULT_CODE_TIMEOUT, "1031", "1037"}:
        payment.mark_cancelled(result_code=code, result_desc=description, raw=data)
        return payment

    # 500.001 / "The transaction is being processed" is normal while pending.
    payment.result_code = code
    payment.result_desc = description
    payment.save(update_fields=["result_code", "result_desc", "updated_at"])
    return payment


# ─────────────────────────────────────────────────────────────
# Callback processing
# ─────────────────────────────────────────────────────────────

def _safe_json(response) -> dict:
    try:
        return response.json() or {}
    except ValueError:
        return {}


def _parse_transaction_date(raw) -> datetime | None:
    """Daraja sends a transaction date as yyyymmddhhmmss in the Africa/Nairobi zone."""
    if not raw:
        return None
    try:
        naive = datetime.strptime(str(raw)[:14], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    from django.utils.timezone import make_aware
    try:
        # Django 5 removed django.utils.timezone.utc; make_aware without a tz
        # interprets the naive value in the configured zone (Africa/Nairobi).
        return make_aware(naive)
    except Exception:
        return naive


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def process_callback(payload: dict) -> MpesaCallbackLog:
    """Idempotently apply one Daraja STK callback.

    Always returns 200-worthy log entry so Safaricom stops retrying, even on
    unknown payloads — otherwise Daraja hammers us with the same bad body.
    """
    body = payload.get("Body") if isinstance(payload, dict) else {}
    stk = body.get("stkCallback") if isinstance(body, dict) else {}

    checkout_request_id = str(stk.get("CheckoutRequestID") or "")
    merchant_request_id = str(stk.get("MerchantRequestID") or "")
    result_code = str(stk.get("ResultCode") if stk.get("ResultCode") is not None else "")
    result_desc = str(stk.get("ResultDesc") or "")

    logger.info(
        "Received billing M-Pesa callback checkout_request_id=%s merchant_request_id=%s result_code=%s",
        checkout_request_id, merchant_request_id, result_code,
    )

    entry = MpesaCallbackLog.objects.create(
        checkout_request_id=checkout_request_id,
        merchant_request_id=merchant_request_id,
        result_code=result_code,
        result_desc=result_desc,
        payload=payload,
    )

    if not checkout_request_id:
        logger.warning("M-Pesa callback without CheckoutRequestID — logged only.")
        return entry

    with transaction.atomic():
        # select_for_update makes a concurrent Safaricom retry wait its turn,
        # which is what prevents double-settling a confirmed payment.
        payment = (
            MpesaPayment.objects.select_for_update()
            .filter(checkout_request_id=checkout_request_id)
            .first()
        )

        if payment is None:
            logger.warning("M-Pesa callback for unknown checkout_request_id=%s", checkout_request_id)
            return entry

        if payment.is_terminal:
            entry.duplicate = True
            entry.save(update_fields=["duplicate"])
            logger.warning(
                "Duplicate M-Pesa callback ignored checkout_request_id=%s status=%s",
                checkout_request_id, payment.status,
            )
            return entry

        metadata = stk.get("CallbackMetadata") or {}
        items = {str(i.get("Name")): i.get("Value") for i in (metadata.get("Item") or []) if isinstance(i, dict)}

        if result_code == RESULT_CODE_SUCCESS:
            receipt = str(items.get("MpesaReceiptNumber") or "")
            amount = _decimal(items.get("Amount"))
            if not receipt or amount is None:
                # Success code but no usable metadata — a human must look.
                payment.mark_needs_review("Success callback missing receipt or amount.")
                entry.processed = True
                entry.save(update_fields=["processed"])
                return entry

            changed = payment.mark_confirmed(
                receipt_number=receipt,
                paid_amount=amount,
                transaction_date=_parse_transaction_date(items.get("TransactionDate")),
                result_code=result_code,
                result_desc=result_desc,
                raw=payload,
            )
            entry.processed = changed
            entry.save(update_fields=["processed"])

            if changed and payment.is_paid:
                logger.info(
                    "M-Pesa payment confirmed payment_id=%s receipt=%s amount=%s",
                    payment.id, receipt, amount,
                )
                # Let the existing custody/settlement code react without this
                # app needing to import it.
                transaction.on_commit(lambda p=payment: mpesa_payment_confirmed.send(sender=MpesaPayment, payment=p))
            return entry

        if result_code in {RESULT_CODE_USER_CANCELLED, RESULT_CODE_TIMEOUT, "1031", "1037"}:
            changed = payment.mark_cancelled(result_code=result_code, result_desc=result_desc, raw=payload)
        else:
            changed = payment.mark_failed(result_code=result_code, result_desc=result_desc, raw=payload)

        entry.processed = changed
        entry.save(update_fields=["processed"])
        if changed:
            transaction.on_commit(lambda p=payment: mpesa_payment_failed.send(sender=MpesaPayment, payment=p))
        return entry


# ─────────────────────────────────────────────────────────────
# Creation helper — the only thing views should call
# ─────────────────────────────────────────────────────────────

def create_payment(
    *,
    user=None,
    phone_number: str,
    amount,
    payable=None,
    account_reference: str = "",
    description: str = "",
) -> MpesaPayment:
    """Create an `initiated` payment row with a normalized MSISDN."""
    normalized = normalize_msisdn(phone_number)
    value = _decimal(amount)
    if value is None or value < Decimal("1.00"):
        raise ValidationError({"amount": "Amount must be at least KES 1.00."})

    return MpesaPayment.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        phone_number=normalized,
        amount=value,
        payable=payable,
        account_reference=(account_reference or _setting("MPESA_ACCOUNT_REFERENCE_DEFAULT", "PRINTY"))[:12],
        description=(description or _setting("MPESA_TRANSACTION_DESC_DEFAULT", "Printy payment"))[:100],
        status=MpesaPaymentStatus.INITIATED,
    )
