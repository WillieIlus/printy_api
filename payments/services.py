from __future__ import annotations

import logging
import base64
from datetime import datetime
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone
from requests.auth import HTTPBasicAuth

from notifications.models import Notification
from notifications.services import notify
from payments.models import MpesaSTKRequest, Payment


logger = logging.getLogger(__name__)


ACTIVE_PAYMENT_STATUSES = {
    Payment.STATUS_PENDING,
    Payment.STATUS_PROCESSING,
}


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _account_reference(quote) -> str:
    quote_ref = getattr(quote, "quote_reference", "") or quote.id
    return f"QUOTE-{quote_ref}"


def _quote_split(quote):
    split = getattr(quote, "financial_split", None)
    if split is None:
        raise ValidationError("Quote must have QuoteFinancialSplit before payment can be created.")
    return split


@transaction.atomic
def create_payment_for_quote(*, quote, payer, method: str = Payment.METHOD_MPESA, payer_phone: str = "") -> Payment:
    split = _quote_split(quote)
    amount = _money(split.client_total)

    paid_payment = Payment.objects.select_for_update().filter(
        quote=quote,
        status=Payment.STATUS_PAID,
    ).first()
    if paid_payment is not None:
        raise ValidationError("This quote has already been paid.")

    payment = Payment.objects.select_for_update().filter(
        quote=quote,
        method=method,
        status__in=ACTIVE_PAYMENT_STATUSES,
    ).order_by("-created_at", "-id").first()
    if payment is None:
        payment = Payment.objects.create(
            quote=quote,
            payer=payer,
            amount=amount,
            expected_amount=amount,
            currency="KES",
            method=method,
            provider="mpesa" if method == Payment.METHOD_MPESA else method,
            status=Payment.STATUS_PENDING,
            account_reference=_account_reference(quote),
            payer_phone=payer_phone or None,
        )
    else:
        changed = []
        if payment.amount != amount:
            payment.amount = amount
            changed.append("amount")
        if payment.expected_amount != amount:
            payment.expected_amount = amount
            changed.append("expected_amount")
        if payer is not None and payment.payer_id != getattr(payer, "id", None):
            payment.payer = payer
            changed.append("payer")
        if payer_phone and payment.payer_phone != payer_phone:
            payment.payer_phone = payer_phone
            changed.append("payer_phone")
        if changed:
            changed.append("updated_at")
            payment.save(update_fields=changed)
    return payment


def mark_payment_processing(payment: Payment, *, checkout_request_id=None, merchant_request_id=None) -> Payment:
    update_fields = ["status", "updated_at"]
    payment.status = Payment.STATUS_PROCESSING
    if checkout_request_id:
        payment.checkout_request_id = checkout_request_id
        update_fields.append("checkout_request_id")
    if merchant_request_id:
        payment.merchant_request_id = merchant_request_id
        update_fields.append("merchant_request_id")
    payment.save(update_fields=update_fields)
    return payment


def _payment_needs_managed_job_repair(payment: Payment) -> bool:
    if payment.managed_job_id is None:
        return True
    quote = getattr(payment, "quote", None)
    if quote is None:
        return False
    return not quote.managed_jobs.filter(pk=payment.managed_job_id).exists()


def _quote_request_title(quote_request) -> str:
    if quote_request is None:
        return "your print job"
    return (
        getattr(quote_request, "title", "")
        or getattr(quote_request, "project_title", "")
        or getattr(quote_request, "customer_name", "")
        or f"quote request #{quote_request.id}"
    )


def _notify_once(**kwargs) -> None:
    if Notification.objects.filter(
        user=kwargs["recipient"],
        notification_type=kwargs["notification_type"],
        object_type=kwargs.get("object_type", ""),
        object_id=kwargs.get("object_id"),
    ).exists():
        return
    notify(**kwargs)


def _notify_payment_confirmed(payment: Payment, managed_job) -> None:
    try:
        quote = getattr(payment, "quote", None)
        quote_request = getattr(quote, "quote_request", None)
        client_user = getattr(managed_job, "client", None) if managed_job else None
        client_user = client_user or getattr(quote_request, "on_behalf_of", None) or getattr(quote_request, "created_by", None)
        if not client_user:
            logger.warning("Skipping payment confirmed notification; client user could not be resolved payment_id=%s", payment.id)
            return

        title = _quote_request_title(quote_request)
        message = f'Your payment of KES {payment.amount} for "{title}" has been confirmed. Your job is being prepared.'
        _notify_once(
            recipient=client_user,
            notification_type=Notification.PAYMENT_CONFIRMED,
            message=message,
            object_type="payment",
            object_id=payment.id,
            actor=payment.payer,
            send_email_notification=True,
            email_subject="Printy - Payment Confirmed",
            email_message=f'Your payment of KES {payment.amount} for "{title}" has been confirmed. Your print job is now being prepared.',
        )
    except Exception as exc:
        logger.warning("Payment confirmed notification failed payment_id=%s: %s", payment.id, exc)


@transaction.atomic
def mark_payment_paid(
    payment: Payment,
    *,
    receipt_number=None,
    received_amount=None,
    confirmed_at=None,
) -> Payment:
    if payment.status == Payment.STATUS_PAID:
        if _payment_needs_managed_job_repair(payment):
            logger.warning(
                "Repairing missing ManagedJob side effect for already-paid payment_id=%s quote_id=%s",
                payment.id,
                payment.quote_id,
            )
            from jobs.services.managed_job_creation import create_managed_job_from_payment

            managed_job = create_managed_job_from_payment(payment=payment)
            _notify_payment_confirmed(payment, managed_job)
        return payment

    expected = _money(payment.expected_amount or payment.amount)
    received = _money(received_amount if received_amount is not None else expected)
    if received != expected:
        raise ValidationError(f"Received amount {received} does not match expected amount {expected}.")

    payment.status = Payment.STATUS_PAID
    payment.received_amount = received
    payment.confirmed_at = confirmed_at or timezone.now()
    if receipt_number:
        payment.mpesa_receipt_number = receipt_number
    payment.save(
        update_fields=[
            "status",
            "received_amount",
            "confirmed_at",
            "mpesa_receipt_number",
            "updated_at",
        ]
    )

    from jobs.services.managed_job_creation import create_managed_job_from_payment

    managed_job = create_managed_job_from_payment(payment=payment)
    _notify_payment_confirmed(payment, managed_job)
    return payment


def confirm_successful_stk_request(
    *,
    stk_request: MpesaSTKRequest,
    callback_payload: dict[str, Any],
    result_desc: str = "",
    receipt_number=None,
    received_amount=None,
) -> Payment:
    stk_request.raw_callback = callback_payload
    stk_request.callback_received_at = timezone.now()
    stk_request.status = MpesaSTKRequest.STATUS_SUCCESS
    stk_request.completed_at = timezone.now()
    stk_request.response_code = "0"
    stk_request.response_description = result_desc or "Success"
    stk_request.save(
        update_fields=[
            "raw_callback",
            "callback_received_at",
            "completed_at",
            "status",
            "response_code",
            "response_description",
            "updated_at",
        ]
    )
    return mark_payment_paid(
        stk_request.payment,
        receipt_number=receipt_number,
        received_amount=received_amount,
        confirmed_at=stk_request.completed_at,
    )


def mark_payment_failed(payment: Payment, *, reason=None, status: str = Payment.STATUS_FAILED) -> Payment:
    if payment.status == Payment.STATUS_PAID:
        return payment
    payment.status = status
    payment.save(update_fields=["status", "updated_at"])
    return payment


def _mpesa_environment() -> str:
    return str(getattr(settings, "MPESA_ENVIRONMENT", "") or getattr(settings, "MPESA_ENV", "")).lower()


def _is_stub_mode() -> bool:
    return _mpesa_environment() in {"test", "testing", "disabled"}


def _test_mode_enabled() -> bool:
    return _is_stub_mode()


class MpesaDarajaError(ValueError):
    def __init__(self, message: str, *, response_code=None, response_payload=None):
        super().__init__(message)
        self.response_code = response_code
        self.response_payload = response_payload


class MpesaDarajaClient:
    SANDBOX_BASE_URL = "https://sandbox.safaricom.co.ke"
    PRODUCTION_BASE_URL = "https://api.safaricom.co.ke"

    def __init__(self):
        self.consumer_key = getattr(settings, "MPESA_CONSUMER_KEY", "")
        self.consumer_secret = getattr(settings, "MPESA_CONSUMER_SECRET", "")
        self.shortcode = getattr(settings, "MPESA_SHORTCODE", "")
        self.passkey = getattr(settings, "MPESA_PASSKEY", "")
        self.callback_url = getattr(settings, "MPESA_CALLBACK_URL", "")
        self.transaction_type = getattr(settings, "MPESA_TRANSACTION_TYPE", "CustomerPayBillOnline")
        self.environment = _mpesa_environment() or "sandbox"
        self.timeout_seconds = int(getattr(settings, "MPESA_TIMEOUT_SECONDS", 30) or 30)

        if not self.consumer_key or not self.consumer_secret:
            raise ImproperlyConfigured("MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET are required.")
        if self.environment == "production":
            self.base_url = self.PRODUCTION_BASE_URL
        else:
            self.base_url = self.SANDBOX_BASE_URL

    def get_access_token(self) -> str:
        url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        logger.debug("Requesting M-Pesa Daraja access token from %s", url)
        response = requests.get(
            url,
            auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
            timeout=self.timeout_seconds,
        )
        if response.status_code != 200:
            raise MpesaDarajaError(
                f"M-Pesa access token request failed: {response.text}",
                response_payload=_response_json_or_text(response),
            )
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise MpesaDarajaError("M-Pesa access token response did not include access_token.", response_payload=data)
        return token

    def generate_password(self) -> tuple[str, str]:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        password_str = f"{self.shortcode}{self.passkey}{timestamp}"
        password = base64.b64encode(password_str.encode()).decode()
        return password, timestamp

    def initiate_stk_push(
        self,
        *,
        phone_number: str,
        amount: int,
        account_reference: str,
        transaction_desc: str,
    ) -> dict:
        token = self.get_access_token()
        password, timestamp = self.generate_password()
        url = f"{self.base_url}/mpesa/stkpush/v1/processrequest"
        payload = {
            "BusinessShortCode": self.shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": self.transaction_type,
            "Amount": int(amount),
            "PartyA": phone_number,
            "PartyB": self.shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": self.callback_url,
            "AccountReference": account_reference,
            "TransactionDesc": transaction_desc,
        }
        response = requests.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            timeout=self.timeout_seconds,
        )
        data = _response_json_or_text(response)
        if response.status_code != 200 or not isinstance(data, dict) or data.get("ResponseCode") != "0":
            logger.error("M-Pesa STK push failed status=%s body=%s", response.status_code, data)
            message = data.get("ErrorMessage") or data.get("ResponseDescription") if isinstance(data, dict) else str(data)
            response_code = data.get("errorCode") or data.get("ResponseCode") if isinstance(data, dict) else None
            raise MpesaDarajaError(message or response.text, response_code=response_code, response_payload=data)
        return data


def _response_json_or_text(response):
    try:
        return response.json()
    except ValueError:
        return response.text


@transaction.atomic
def initiate_stk_push(*, payment: Payment, phone_number: str) -> MpesaSTKRequest:
    expected = _money(payment.expected_amount or payment.amount)
    amount = _money(payment.amount)
    if amount != expected:
        raise ValidationError("Payment amount must match expected_amount before STK initiation.")
    if payment.status == Payment.STATUS_PAID:
        raise ValidationError("This payment is already paid.")

    existing = payment.mpesa_stk_requests.filter(
        status__in=[MpesaSTKRequest.STATUS_PENDING, MpesaSTKRequest.STATUS_SENT],
    ).order_by("-requested_at", "-id").first()
    if existing is not None:
        return existing

    stk_request = MpesaSTKRequest.objects.create(
        payment=payment,
        phone_number=phone_number,
        amount=amount,
        account_reference=payment.account_reference,
        status=MpesaSTKRequest.STATUS_PENDING,
    )

    if _is_stub_mode():
        checkout_request_id = f"TEST-CR-{stk_request.id}"
        merchant_request_id = f"TEST-MR-{stk_request.id}"
        stk_request.checkout_request_id = checkout_request_id
        stk_request.merchant_request_id = merchant_request_id
        stk_request.response_code = "0"
        stk_request.response_description = "Success. Request accepted for processing"
        stk_request.customer_message = "Success. Request accepted for processing"
        stk_request.status = MpesaSTKRequest.STATUS_SENT
        stk_request.raw_response = {
            "CheckoutRequestID": checkout_request_id,
            "MerchantRequestID": merchant_request_id,
            "ResponseCode": "0",
            "ResponseDescription": stk_request.response_description,
        }
        stk_request.save(
            update_fields=[
                "checkout_request_id",
                "merchant_request_id",
                "response_code",
                "response_description",
                "customer_message",
                "status",
                "raw_response",
                "updated_at",
            ]
        )
    else:
        try:
            response = MpesaDarajaClient().initiate_stk_push(
                phone_number=phone_number,
                amount=int(amount),
                account_reference=payment.account_reference,
                transaction_desc=getattr(settings, "MPESA_TRANSACTION_DESC_DEFAULT", "Printy payment"),
            )
        except Exception as exc:
            stk_request.response_code = getattr(exc, "response_code", None) or "500"
            stk_request.response_description = str(exc)
            stk_request.status = MpesaSTKRequest.STATUS_FAILED
            response_payload = getattr(exc, "response_payload", None)
            if response_payload is not None:
                stk_request.raw_response = response_payload
                update_fields = ["response_code", "response_description", "status", "raw_response", "updated_at"]
            else:
                update_fields = ["response_code", "response_description", "status", "updated_at"]
            stk_request.save(update_fields=update_fields)
            logger.error("M-Pesa STK push initiation failed payment_id=%s: %s", payment.id, exc)
            raise

        checkout_request_id = response.get("CheckoutRequestID")
        merchant_request_id = response.get("MerchantRequestID")
        stk_request.checkout_request_id = checkout_request_id
        stk_request.merchant_request_id = merchant_request_id
        stk_request.response_code = response.get("ResponseCode")
        stk_request.response_description = response.get("ResponseDescription")
        stk_request.customer_message = response.get("CustomerMessage")
        stk_request.raw_response = response
        stk_request.status = MpesaSTKRequest.STATUS_SENT
        stk_request.save(
            update_fields=[
                "checkout_request_id",
                "merchant_request_id",
                "response_code",
                "response_description",
                "customer_message",
                "raw_response",
                "status",
                "updated_at",
            ]
        )

    mark_payment_processing(
        payment,
        checkout_request_id=checkout_request_id,
        merchant_request_id=merchant_request_id,
    )
    return stk_request


def _callback(payload: dict[str, Any]) -> dict[str, Any]:
    return ((payload or {}).get("Body") or {}).get("stkCallback") or {}


def _callback_item(callback: dict[str, Any], name: str):
    items = ((callback.get("CallbackMetadata") or {}).get("Item") or [])
    for item in items:
        if item.get("Name") == name:
            return item.get("Value")
    return None


@transaction.atomic
def handle_stk_callback(*, callback_payload: dict[str, Any]) -> dict[str, str]:
    callback = _callback(callback_payload)
    checkout_request_id = callback.get("CheckoutRequestID")
    merchant_request_id = callback.get("MerchantRequestID")
    if not checkout_request_id and not merchant_request_id:
        raise ValidationError("Callback payload missing checkout or merchant request id.")

    query = {}
    if checkout_request_id:
        query["checkout_request_id"] = checkout_request_id
    else:
        query["merchant_request_id"] = merchant_request_id
    stk_request = MpesaSTKRequest.objects.select_for_update().select_related("payment").filter(**query).first()
    if stk_request is None:
        raise ValidationError("No matching M-Pesa STK request was found.")

    result_code = int(callback.get("ResultCode", 1))
    result_desc = callback.get("ResultDesc") or ""
    if result_code == 0:
        receipt = _callback_item(callback, "MpesaReceiptNumber")
        amount = _callback_item(callback, "Amount")
        transaction_date = _callback_item(callback, "TransactionDate")
        callback_phone_number = _callback_item(callback, "PhoneNumber")
        logger.info(
            "M-Pesa STK callback succeeded payment_id=%s checkout_request_id=%s receipt=%s amount=%s transaction_date=%s phone_number=%s",
            stk_request.payment_id,
            checkout_request_id,
            receipt,
            amount,
            transaction_date,
            callback_phone_number,
        )
        confirm_successful_stk_request(
            stk_request=stk_request,
            callback_payload=callback_payload,
            result_desc=result_desc,
            receipt_number=receipt,
            received_amount=amount,
        )
        return {"status": "success", "payment_id": str(stk_request.payment_id)}

    failed_status = MpesaSTKRequest.STATUS_CANCELLED if result_code == 1032 else MpesaSTKRequest.STATUS_FAILED
    logger.warning(
        "M-Pesa STK callback failed payment_id=%s checkout_request_id=%s result_code=%s result_desc=%s",
        stk_request.payment_id,
        checkout_request_id,
        result_code,
        result_desc,
    )
    stk_request.raw_callback = callback_payload
    stk_request.callback_received_at = timezone.now()
    stk_request.status = failed_status
    stk_request.completed_at = timezone.now()
    stk_request.response_code = str(result_code)
    stk_request.response_description = result_desc
    stk_request.save(
        update_fields=[
            "raw_callback",
            "callback_received_at",
            "completed_at",
            "status",
            "response_code",
            "response_description",
            "updated_at",
        ]
    )
    mark_payment_failed(
        stk_request.payment,
        reason=result_desc,
        status=Payment.STATUS_CANCELLED if failed_status == MpesaSTKRequest.STATUS_CANCELLED else Payment.STATUS_FAILED,
    )
    return {"status": "failed", "reason": result_desc}


def initiate_mpesa_stk_request(*args, **kwargs):
    return initiate_stk_push(*args, **kwargs)
