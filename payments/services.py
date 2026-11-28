from __future__ import annotations

import logging
import base64
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.utils import timezone
from requests.auth import HTTPBasicAuth

from common.money import money, require_whole_kes
from mpesa_payments.services import (
    DARAAJA_PRODUCTS,
    DARAAJA_PRODUCTS_WITHOUT_CREDENTIALS,
    mpesa_product,
)
from notifications.models import Notification
from notifications.services import notify
from payments.models import MpesaSTKRequest, Payment


logger = logging.getLogger(__name__)


ACTIVE_PAYMENT_STATUSES = {
    Payment.STATUS_PENDING,
    Payment.STATUS_PROCESSING,
    # Audit #1c: an expired payment (its prompt timed out with no outcome) is
    # retried on the SAME row, so a late success for the old prompt and a new
    # push can never settle two Payment rows for one quote.
    Payment.STATUS_EXPIRED,
}


def _money(value) -> Decimal:
    return money(value)

def normalize_mpesa_phone(phone_number: str) -> str:
    digits = "".join(ch for ch in str(phone_number or "") if ch.isdigit())
    if digits.startswith("0") and len(digits) == 10:
        digits = f"254{digits[1:]}"
    elif len(digits) == 9 and digits[0:1] in {"7", "1"}:
        digits = f"254{digits}"
    if len(digits) != 12 or not digits.startswith(("2547", "2541")):
        raise ValidationError("Enter a valid Kenyan M-Pesa phone number, for example 2547XXXXXXXX.")
    return digits


# Daraja hard limits on the STK Push request. Exceeding either one makes
# Safaricom reject the whole request (ResponseCode != "0", errorCode 20003)
# before the customer is ever prompted, so both are enforced where the value is
# generated and again where the payload is built.
DARAJA_ACCOUNT_REFERENCE_MAX = 12
DARAJA_TRANSACTION_DESC_MAX = 13


def _account_reference(quote) -> str:
    """Build a Daraja-legal AccountReference (at most 12 characters).

    ``quote.quote_reference`` is "Q-YYYYMMDD-NNNN" — 15 characters on its own —
    so prefixing it with "QUOTE-" produced 21 and Daraja rejected every
    canonical quote payment before the customer saw a prompt.

    Slicing that string down to 12 is not an option: it would keep
    "QUOTE-Q-2026" and collide every quote in the same year. The reference is
    therefore rebuilt from its parts and only the redundant century is dropped.

    The quote id is globally unique, so it is the part that must survive whole.
    The date is decoration and is dropped in one piece rather than trimmed,
    because trimming it is what would let two quotes collide::

        "Q261003-0001"    id below 10000 — dated, 12 chars
        "Q-10000"         id of 5+ digits — undated, id intact

    The result is a pure function of the quote (no clock read, no randomness),
    so retrying a payment reproduces the same reference. Ids are exact up to 10
    digits, which is the ceiling of a 32-bit AutoField.
    """
    quote_id = int(getattr(quote, "id", 0) or 0)
    created_at = getattr(quote, "created_at", None)
    if created_at is None:
        date_digits = ""
    elif timezone.is_aware(created_at):
        date_digits = timezone.localtime(created_at).strftime("%y%m%d")
    else:
        date_digits = created_at.strftime("%y%m%d")
    tail = f"-{quote_id:04d}"
    dated = f"Q{date_digits}{tail}"
    if len(dated) <= DARAJA_ACCOUNT_REFERENCE_MAX:
        return dated
    return f"Q{tail}"[:DARAJA_ACCOUNT_REFERENCE_MAX]


def _quote_split(quote):
    # Read the persisted split rather than whatever the quote instance happens
    # to have cached. Acceptance reconciles and locks the split through a
    # separate locked row instance, and a stale cached relation would otherwise
    # make us charge the pre-adjustment amount.
    from quotes.models import QuoteFinancialSplit

    split = QuoteFinancialSplit.objects.filter(quote=quote).first()
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
    if Payment.objects.filter(quote=quote, status=Payment.STATUS_NEEDS_REVIEW).exists():
        raise ValidationError(
            "A previous payment for this quote is being reviewed. Printy will contact you before you pay again."
        )

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
    amount_override_reason: str = "",
) -> Payment:
    """The single canonical "paid" transition (creates/unlocks the ManagedJob).

    ``amount_override_reason`` is only passed by the audited super-admin
    needs_review resolution (payments.review_resolution): it lets an operator
    accept a received amount that differs from ``expected_amount`` after
    reconciling it by hand. Every automated caller must leave it empty.
    """
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
    if received != expected and not str(amount_override_reason or "").strip():
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


def daraja_timestamp(now=None) -> str:
    """Daraja's YYYYMMDDHHMMSS timestamp, in Kenyan local time."""
    return timezone.localtime(now or timezone.now()).strftime("%Y%m%d%H%M%S")


def stk_in_flight_window():
    """How long an unanswered STK prompt counts as genuinely in flight.

    The handset prompt times out after about a minute and Daraja normally
    calls back within a couple of minutes, so a prompt older than this that
    never got a callback (lost callback, crashed request) is stale.
    """
    from datetime import timedelta

    return timedelta(seconds=int(getattr(settings, "MPESA_STK_IN_FLIGHT_SECONDS", 180) or 180))


def expire_stale_stk_requests(payment: Payment, *, now=None) -> int:
    """Mark in-flight prompts older than the in-flight window EXPIRED.

    A late callback for an expired prompt is still applied (see
    handle_stk_callback): expiry is our own bookkeeping, not Daraja's.
    """
    now = now or timezone.now()
    cutoff = now - stk_in_flight_window()
    return MpesaSTKRequest.objects.filter(
        payment_id=payment.pk,
        status__in=MpesaSTKRequest.IN_FLIGHT_STATUSES,
        requested_at__lt=cutoff,
    ).update(
        status=MpesaSTKRequest.STATUS_EXPIRED,
        completed_at=now,
        response_description="Expired: no callback within the in-flight window.",
        updated_at=now,
    )


def _mpesa_environment() -> str:
    return str(getattr(settings, "MPESA_ENVIRONMENT", "") or getattr(settings, "MPESA_ENV", "")).lower()


def _is_stub_mode() -> bool:
    # An explicit force flag wins over everything, including .env, so the
    # documented stub mode is actually reachable. Previously .env pinned
    # MPESA_ENVIRONMENT=sandbox and overrode any process-level request to use
    # it, making the stub path dead code in any deployment that has a .env.
    if str(getattr(settings, "MPESA_FORCE_STUB", "") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
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
        # The URL handed to Daraja carries MPESA_CALLBACK_SECRET (audit #1).
        from payments.callback_security import signed_callback_url

        self.callback_url = signed_callback_url(getattr(settings, "MPESA_CALLBACK_URL", ""))
        self.product = mpesa_product()
        self.transaction_type = DARAAJA_PRODUCTS[self.product]
        self.environment = _mpesa_environment() or "sandbox"
        self.timeout_seconds = int(getattr(settings, "MPESA_TIMEOUT_SECONDS", 30) or 30)
        # Cap the per-request budget well below the gunicorn worker timeout so a
        # slow/hung Daraja never turns into an upstream nginx 502: the view can
        # then answer with a controlled JSON error the frontend understands.
        self.http_timeout = (4, min(self.timeout_seconds, 8))

        if not self.consumer_key or not self.consumer_secret:
            raise ImproperlyConfigured("MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET are required.")
        if self.product in DARAAJA_PRODUCTS_WITHOUT_CREDENTIALS and (self.shortcode or self.passkey):
            raise ImproperlyConfigured(
                f"MPESA_SHORTCODE_TYPE={self.product} is authenticated by the OAuth token alone: "
                "MPESA_SHORTCODE and MPESA_PASSKEY must be empty."
            )
        if self.environment == "production":
            self.base_url = self.PRODUCTION_BASE_URL
        else:
            self.base_url = self.SANDBOX_BASE_URL

    # Audit #8: Daraja tokens live ~1 hour (expires_in=3599). They used to be
    # fetched on every STK push; now they are cached until shortly before
    # expiry, per environment and consumer key.
    TOKEN_EXPIRY_MARGIN_SECONDS = 60

    def _token_cache_key(self) -> str:
        import hashlib

        digest = hashlib.sha256(f"{self.base_url}|{self.consumer_key}".encode()).hexdigest()[:24]
        return f"payments:daraja:oauth:{digest}"

    def get_access_token(self) -> str:
        from django.core.cache import cache

        cache_key = self._token_cache_key()
        cached = cache.get(cache_key)
        if cached:
            return cached
        token, expires_in = self._fetch_access_token()
        ttl = expires_in - self.TOKEN_EXPIRY_MARGIN_SECONDS
        if ttl > 0:
            cache.set(cache_key, token, timeout=ttl)
        return token

    def _fetch_access_token(self) -> tuple[str, int]:
        url = f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials"
        logger.debug("Requesting M-Pesa Daraja access token from %s", url)
        try:
            response = requests.get(
                url,
                auth=HTTPBasicAuth(self.consumer_key, self.consumer_secret),
                timeout=self.http_timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise MpesaDarajaError(
                f"M-Pesa access token request failed: {exc}",
                response_code="400",
                response_payload=str(exc),
            ) from exc
        if response.status_code != 200:
            raise MpesaDarajaError(
                f"M-Pesa access token request failed: {response.text}",
                response_payload=_response_json_or_text(response),
            )
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise MpesaDarajaError("M-Pesa access token response did not include access_token.", response_payload=data)
        try:
            expires_in = int(str(data.get("expires_in") or "3599").strip())
        except (TypeError, ValueError):
            expires_in = 3599
        return token, expires_in

    def generate_password(self) -> tuple[str, str]:
        # Audit #8: Daraja wants the timestamp in Kenyan local time (EAT). Use
        # Django's clock (timezone.now(), TIME_ZONE=Africa/Nairobi) instead of
        # the naive OS clock, so it is correct whatever the server's TZ is.
        timestamp = daraja_timestamp()
        if self.product in DARAAJA_PRODUCTS_WITHOUT_CREDENTIALS:
            return "", timestamp
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
        # BuyGoodsOnline is authorised by the bearer token alone and rejects a
        # short code or password, so both are sent empty.
        business_short_code = "" if self.product in DARAAJA_PRODUCTS_WITHOUT_CREDENTIALS else self.shortcode
        payload = {
            "BusinessShortCode": business_short_code,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": self.transaction_type,
            # Daraja only accepts whole shillings. The amount is rounded once
            # upstream (pricing.services.platform_fee_policy) and arrives here
            # already whole, so this asserts rather than truncates — truncating
            # would silently under-charge the customer.
            "Amount": int(require_whole_kes(amount, "STK amount")),
            "PartyA": phone_number,
            "PartyB": business_short_code,
            "PhoneNumber": phone_number,
            "CallBackURL": self.callback_url,
            # Daraja rejects the entire request if either field is over its
            # limit, so clamp here as well as at generation time: this is the
            # single canonical payload site, so every caller of this client is
            # covered, and rows written before _account_reference() was fixed
            # are clamped rather than sent as a 21-character reference.
            "AccountReference": (account_reference or "PRINTY")[:DARAJA_ACCOUNT_REFERENCE_MAX],
            "TransactionDesc": (transaction_desc or "Printy payment")[:DARAJA_TRANSACTION_DESC_MAX],
        }
        try:
            response = requests.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                timeout=self.http_timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise MpesaDarajaError(
                f"M-Pesa STK push request failed: {exc}",
                response_code="400",
                response_payload=str(exc),
            ) from exc
        data = _response_json_or_text(response)
        if response.status_code != 200 or not isinstance(data, dict) or data.get("ResponseCode") != "0":
            logger.error("M-Pesa STK push failed status=%s body=%s", response.status_code, data)
            message = data.get("ErrorMessage") or data.get("ResponseDescription") if isinstance(data, dict) else str(data)
            response_code = data.get("errorCode") or data.get("ResponseCode") if isinstance(data, dict) else None
            raise MpesaDarajaError(message or response.text, response_code=response_code, response_payload=data)
        return data


    # Audit #1c: STK Query (/mpesa/stkpushquery/v1/query). Daraja answers a
    # finished prompt with HTTP 200 and a ResultCode; a prompt it is still
    # working on comes back as an error body with errorCode 500.001.1001
    # ("The transaction is being processed"). This method never changes state:
    # it only classifies the answer so payments.recovery can feed it through
    # the same state machine as a callback.
    STK_QUERY_PROCESSING_ERROR_CODES = ("500.001.1001",)

    def query_stk_status(self, *, checkout_request_id: str) -> dict:
        """Return {"state": "final" | "processing", "data": <Daraja body>}.

        Raises MpesaDarajaError (``http_status`` set) for anything else: network
        failures, auth errors, rate limiting (429) and malformed answers.
        """
        token = self.get_access_token()
        password, timestamp = self.generate_password()
        business_short_code = "" if self.product in DARAAJA_PRODUCTS_WITHOUT_CREDENTIALS else self.shortcode
        payload = {
            "BusinessShortCode": business_short_code,
            "Password": password,
            "Timestamp": timestamp,
            "CheckoutRequestID": checkout_request_id,
        }
        try:
            response = requests.post(
                f"{self.base_url}/mpesa/stkpushquery/v1/query",
                json=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                timeout=self.http_timeout,
            )
        except requests.exceptions.RequestException as exc:
            error = MpesaDarajaError(f"M-Pesa STK query failed: {exc}", response_payload=str(exc))
            error.http_status = None
            raise error from exc
        data = _response_json_or_text(response)
        if isinstance(data, dict):
            error_code = str(data.get("errorCode") or "").strip()
            error_message = str(data.get("errorMessage") or "")
            if error_code in self.STK_QUERY_PROCESSING_ERROR_CODES or "being processed" in error_message.lower():
                return {"state": "processing", "data": data}
            if response.status_code == 200 and str(data.get("ResultCode", "")).strip() != "":
                return {"state": "final", "data": data}
        error = MpesaDarajaError(
            f"M-Pesa STK query returned an unusable answer (HTTP {response.status_code}).",
            response_code=(data.get("errorCode") if isinstance(data, dict) else None),
            response_payload=data,
        )
        error.http_status = response.status_code
        raise error

def _response_json_or_text(response):
    try:
        return response.json()
    except ValueError:
        return response.text


def initiate_stk_push(*, payment: Payment, phone_number: str) -> MpesaSTKRequest:
    """Send (or reuse) the STK prompt for ``payment``.

    Audit #8: the check-then-create is done under ``select_for_update()`` on
    the payment row, so two concurrent requests cannot both decide "no prompt
    in flight" and push twice. An in-flight prompt still blocks a conflicting
    one, but only while it is genuinely in flight: prompts older than
    MPESA_STK_IN_FLIGHT_SECONDS with no callback are expired first. The
    Daraja HTTP call happens after the lock is released; the PENDING row it
    leaves behind is what blocks a concurrent second push meanwhile.
    """
    caller_payment = payment
    try:
        return _initiate_stk_push_locked(payment=payment, phone_number=phone_number)
    finally:
        # Callers keep using the instance they passed in; keep its status and
        # Daraja ids in step with the locked copy that was actually updated.
        try:
            caller_payment.refresh_from_db(fields=["status", "checkout_request_id", "merchant_request_id", "updated_at"])
        except Exception:  # pragma: no cover - never mask the real outcome
            pass


def _initiate_stk_push_locked(*, payment: Payment, phone_number: str) -> MpesaSTKRequest:
    phone_number = normalize_mpesa_phone(phone_number)
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        expected = _money(payment.expected_amount or payment.amount)
        amount = _money(payment.amount)
        if amount != expected:
            raise ValidationError("Payment amount must match expected_amount before STK initiation.")
        if payment.status == Payment.STATUS_PAID:
            raise ValidationError("This payment is already paid.")
        if payment.status == Payment.STATUS_NEEDS_REVIEW:
            raise ValidationError("This payment is being reviewed and cannot be retried.")

        expired = expire_stale_stk_requests(payment)
        if expired:
            logger.info("Expired %s stale STK prompt(s) for payment_id=%s", expired, payment.id)

        existing = payment.mpesa_stk_requests.filter(
            status__in=MpesaSTKRequest.IN_FLIGHT_STATUSES,
        ).order_by("-requested_at", "-id").first()
        if existing is not None:
            mark_payment_processing(
                payment,
                checkout_request_id=existing.checkout_request_id,
                merchant_request_id=existing.merchant_request_id,
            )
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
        stk_request.response_description = "SIMULATED STUB (MPESA_FORCE_STUB): no Daraja call was made"
        stk_request.customer_message = "Success. Request accepted for processing"
        stk_request.status = MpesaSTKRequest.STATUS_SENT
        # The id prefix (TEST-CR-) and the explicit marker below label every
        # stub row, so an operator can always tell a simulated push from a real
        # one. A successful push here still means "awaiting confirmation": the
        # payment is only marked paid by a verified callback.
        stk_request.raw_response = {
            "CheckoutRequestID": checkout_request_id,
            "MerchantRequestID": merchant_request_id,
            "ResponseCode": "0",
            "ResponseDescription": stk_request.response_description,
            "Simulated": True,
            "SimulationSource": "payments.services.initiate_stk_push",
            "SimulationReason": "MPESA_FORCE_STUB stub mode",
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
                amount=require_whole_kes(amount, "STK amount"),
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
            if payment.status != Payment.STATUS_PENDING:
                payment.status = Payment.STATUS_PENDING
                payment.save(update_fields=["status", "updated_at"])
            logger.error("M-Pesa STK push initiation failed payment_id=%s stk_request_id=%s: %s", payment.id, stk_request.id, exc)
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


def _flag_stk_needs_review(
    *,
    stk_request: MpesaSTKRequest,
    callback_payload: dict[str, Any],
    result_desc: str,
    reason: str,
    receipt_number=None,
    received_amount=None,
) -> dict[str, str]:
    """Record an unverifiable success: money may have moved, never mark it paid."""
    now = timezone.now()
    stk_request.raw_callback = callback_payload
    stk_request.callback_received_at = now
    stk_request.completed_at = now
    stk_request.status = MpesaSTKRequest.STATUS_NEEDS_REVIEW
    stk_request.response_code = "0"
    stk_request.response_description = f"{reason} ({result_desc})" if result_desc else reason
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
    payment = stk_request.payment
    if payment.status != Payment.STATUS_PAID:
        payment.status = Payment.STATUS_NEEDS_REVIEW
        update_fields = ["status", "updated_at"]
        received = None
        if received_amount not in (None, ""):
            try:
                received = _money(received_amount)
            except (InvalidOperation, TypeError, ValueError):
                received = None
        if received is not None:
            payment.received_amount = received
            update_fields.append("received_amount")
        if receipt_number:
            payment.mpesa_receipt_number = str(receipt_number)
            update_fields.append("mpesa_receipt_number")
        payment.save(update_fields=update_fields)
    logger.error(
        "M-Pesa STK success NOT applied, flagged needs_review payment_id=%s stk_request_id=%s reason=%s",
        payment.id,
        stk_request.id,
        reason,
    )
    return {"status": "needs_review", "payment_id": str(payment.id), "reason": reason}


STK_SOURCE_CALLBACK = "callback"
STK_SOURCE_QUERY = "stk_query"


@transaction.atomic
def handle_stk_callback(*, callback_payload: dict[str, Any], source: str = STK_SOURCE_CALLBACK) -> dict[str, str]:
    """Apply a Daraja STK callback to the persisted canonical STK request.

    Only ever acts on an ``MpesaSTKRequest`` we persisted (matched by
    CheckoutRequestID) that is still in flight; a final request is an
    idempotent no-op. A success is applied only with a receipt AND an Amount
    equal to the server-stored expected amount; anything else is flagged
    ``needs_review`` and never paid. A failure only fails the payment when no
    other prompt for it is still in flight, so a late/replayed callback for an
    old prompt can never cancel the customer's current one.

    ``source`` is ``"stk_query"`` when payments.recovery feeds a Daraja STK
    Query answer through here (audit #1c). The checks are identical; only the
    review reason differs, because STK Query never returns a receipt/amount.
    """
    callback = _callback(callback_payload)
    checkout_request_id = str(callback.get("CheckoutRequestID") or "").strip()
    if not checkout_request_id:
        raise ValidationError("Callback payload missing CheckoutRequestID.")
    try:
        result_code = int(str(callback.get("ResultCode")).strip())
    except (TypeError, ValueError):
        raise ValidationError("Callback payload has an invalid ResultCode.")

    stk_request = (
        MpesaSTKRequest.objects.select_for_update()
        .select_related("payment")
        .filter(checkout_request_id=checkout_request_id)
        .first()
    )
    if stk_request is None:
        logger.warning("M-Pesa STK callback for unknown checkout_request_id=%s", checkout_request_id)
        raise ValidationError("No matching M-Pesa STK request was found.")
    # Lock the payment too: two prompts for one payment must not race.
    payment = Payment.objects.select_for_update().get(pk=stk_request.payment_id)
    stk_request.payment = payment

    result_desc = callback.get("ResultDesc") or ""

    # Audit #8: EXPIRED is our own "no callback in time" bookkeeping. A late
    # callback for such a prompt is real: a success still has to be applied
    # (or flagged), so it is not treated as a duplicate.
    late_for_expired = stk_request.status == MpesaSTKRequest.STATUS_EXPIRED
    if stk_request.status not in MpesaSTKRequest.IN_FLIGHT_STATUSES and not late_for_expired:
        logger.warning(
            "Duplicate/late M-Pesa STK callback ignored payment_id=%s checkout_request_id=%s stk_status=%s result_code=%s",
            stk_request.payment_id,
            checkout_request_id,
            stk_request.status,
            result_code,
        )
        if (
            result_code == 0
            and stk_request.status == MpesaSTKRequest.STATUS_SUCCESS
            and payment.status == Payment.STATUS_PAID
        ):
            # Replayed success: no state change, but keep the side-effect repair
            # (missing ManagedJob) that mark_payment_paid performs on paid rows.
            mark_payment_paid(payment)
            return {"status": "success", "payment_id": str(stk_request.payment_id), "duplicate": "true"}
        return {"status": "duplicate", "payment_id": str(stk_request.payment_id)}

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
        if not receipt or amount in (None, ""):
            return _flag_stk_needs_review(
                stk_request=stk_request,
                callback_payload=callback_payload,
                result_desc=result_desc,
                reason=(
                    "Daraja STK Query reports the prompt was completed but returns no "
                    "receipt or amount; reconcile against the M-Pesa statement, then resolve."
                    if source == STK_SOURCE_QUERY
                    else "Success callback missing MpesaReceiptNumber or Amount."
                ),
                receipt_number=receipt,
                received_amount=amount,
            )
        try:
            received = _money(amount)
        except (InvalidOperation, TypeError, ValueError):
            received = None
        expected = _money(payment.expected_amount or payment.amount)
        if received is None or received != expected or received != _money(stk_request.amount):
            return _flag_stk_needs_review(
                stk_request=stk_request,
                callback_payload=callback_payload,
                result_desc=result_desc,
                reason=f"Callback amount {amount} does not match expected amount {expected}.",
                receipt_number=receipt,
                received_amount=amount,
            )
        if payment.status == Payment.STATUS_PAID:
            # Another prompt already settled this payment: a second debit is
            # real money that needs a refund decision, not a silent "success".
            return _flag_stk_needs_review(
                stk_request=stk_request,
                callback_payload=callback_payload,
                result_desc=result_desc,
                reason="Payment was already paid by another STK request.",
                receipt_number=receipt,
                received_amount=amount,
            )
        if payment.status == Payment.STATUS_NEEDS_REVIEW:
            return _flag_stk_needs_review(
                stk_request=stk_request,
                callback_payload=callback_payload,
                result_desc=result_desc,
                reason="Payment is under review; success not auto-applied.",
                receipt_number=receipt,
                received_amount=amount,
            )
        confirm_successful_stk_request(
            stk_request=stk_request,
            callback_payload=callback_payload,
            result_desc=result_desc,
            receipt_number=receipt,
            received_amount=received,
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
    other_in_flight = (
        MpesaSTKRequest.objects.filter(payment_id=payment.id, status__in=MpesaSTKRequest.IN_FLIGHT_STATUSES)
        .exclude(pk=stk_request.pk)
        .exists()
    )
    if late_for_expired:
        # The prompt was already given up on; its late failure must not touch
        # the payment (a newer prompt may be running or may have paid it).
        return {"status": "failed", "reason": result_desc, "late": "true"}
    if other_in_flight or payment.status == Payment.STATUS_NEEDS_REVIEW:
        logger.info(
            "Failure callback for stk_request_id=%s left payment_id=%s unchanged (status=%s, other prompt in flight=%s)",
            stk_request.id,
            payment.id,
            payment.status,
            other_in_flight,
        )
        return {"status": "failed", "reason": result_desc}
    mark_payment_failed(
        payment,
        reason=result_desc,
        status=Payment.STATUS_CANCELLED if failed_status == MpesaSTKRequest.STATUS_CANCELLED else Payment.STATUS_FAILED,
    )
    return {"status": "failed", "reason": result_desc}


def initiate_mpesa_stk_request(*args, **kwargs):
    return initiate_stk_push(*args, **kwargs)

