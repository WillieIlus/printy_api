"""M-Pesa views.

Security rules that matter here:

1. The callbacks carry no Django auth (Safaricom cannot log in), so every
   callback must pass payments.callback_security: the secret URL segment
   (hmac.compare_digest), the Safaricom IP allowlist (trusted-proxy aware),
   and fail-closed behaviour in production. Failure -> 403, no state change.
2. A verified callback must reference a CheckoutRequestID we persisted when
   Daraja accepted our own push. Unknown -> 404 + log; already-final -> 200
   no-op. Only then is the state machine applied.
3. Every other view requires authentication and only touches payments the
   requester created (and, for a ManagedJob, only the job's client may pay).
4. The amount charged for a payable is derived server-side
   (QuoteFinancialSplit.client_total); a client-supplied amount is ignored.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.serializers import get_or_create_profile
from payments.callback_security import reject_unverified_callback

from .models import MpesaCallbackLog, MpesaPayment
from .serializers import MpesaCallbackSerializer, MpesaPaymentReadSerializer, MpesaStkPushSerializer
from .services import (
    MpesaError,
    create_payment,
    initiate_stk_push,
    process_callback,
    process_transaction_status_result,
    query_stk_status,
)

logger = logging.getLogger("payments")

def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _save_profile_phone(request, phone: str) -> None:
    """Persist the paying number to the user's profile so the next checkout
    can be prefilled. Explicit consent gate keeps the payment flow unchanged
    for callers that do not ask to remember the number."""
    if not _truthy(request.data.get("save_phone_to_profile")):
        return
    profile = get_or_create_profile(request.user)
    if profile.phone != phone:
        # Audit #3: the one canonical phone writer (normalises, resets verification).
        from accounts.services.phone import set_profile_phone

        set_profile_phone(request.user, phone, verified=False)


# ManagedJob payment states that mean "nothing left to collect".
_SETTLED_JOB_PAYMENT_STATES = {"confirmed", "release_ready", "payout_on_hold", "released", "refunded"}


def _split_client_total(quote):
    """QuoteFinancialSplit.client_total — the authoritative amount (CANONICAL_MODELS)."""
    if quote is None:
        return None
    from quotes.models import QuoteFinancialSplit

    split = QuoteFinancialSplit.objects.filter(quote=quote).first()
    return split.client_total if split is not None else None


def _managed_job_expected_amount(managed_job):
    """Server-side amount for a ManagedJob. Never taken from the request body.

    The source quote's split is authoritative. A job created without a quote
    (manager-created) has no split; its frozen server-side client_total is the
    only figure, and a job with neither cannot be charged at all.
    """
    total = _split_client_total(getattr(managed_job, "source_quote", None))
    if total is None:
        total = managed_job.client_total
    return total


def _blocked(detail: str, http_status: int, **extra) -> Response:
    return Response({"detail": detail, **extra}, status=http_status)


class MpesaStkPushView(APIView):
    """POST /api/payments/mpesa/stk-push/

    Sends the "Lipa na M-Pesa" prompt to the customer's phone. The response
    means "Daraja accepted the request", never "the payment succeeded".

    * ``managed_job_id``: only the job's client may pay; the artwork gate must
      be clear; the amount is the server-side split total (any ``amount`` in
      the body is ignored).
    * ``quote_id``: routed through ``accept_quote_for_payment`` and the
      canonical ``payments`` STK flow, so a confirmed callback settles the
      quote and creates its ManagedJob (no "unhandled payable").
    * no payable: an ad-hoc payment with nothing to settle; the body amount is
      what the payer chose to send.
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "mpesa_stk_push"

    def post(self, request):
        serializer = MpesaStkPushSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data.get("quote_id"):
            return self._pay_quote(request, data)

        payable = None
        amount = data.get("amount")
        if data.get("managed_job_id"):
            from jobs.models import ManagedJob

            managed_job = (
                ManagedJob.objects.select_related("source_quote", "source_quote_request")
                .filter(pk=data["managed_job_id"])
                .first()
            )
            if managed_job is None:
                return _blocked("Job not found.", status.HTTP_404_NOT_FOUND)
            from api.payment_views import _can_trigger_managed_job_payment, _managed_job_artwork_block

            if not _can_trigger_managed_job_payment(request.user, managed_job):
                logger.warning(
                    "Refused M-Pesa stk-push on managed_job_id=%s by non-client user_id=%s",
                    managed_job.id, getattr(request.user, "id", None),
                )
                return _blocked("Only the client on this job can pay for it.", status.HTTP_403_FORBIDDEN)
            if str(managed_job.payment_status or "").lower() in _SETTLED_JOB_PAYMENT_STATES:
                return _blocked("This job is already paid.", status.HTTP_409_CONFLICT)
            block_reason = _managed_job_artwork_block(managed_job)
            if block_reason:
                return _blocked(
                    block_reason, status.HTTP_400_BAD_REQUEST, artwork_payment_block_reason=block_reason
                )
            amount = _managed_job_expected_amount(managed_job)
            if amount is None:
                return _blocked(
                    "This job has no confirmed price yet, so it cannot be paid.", status.HTTP_409_CONFLICT
                )
            if data.get("amount") is not None and data["amount"] != amount:
                logger.info(
                    "Ignoring client-supplied amount=%s for managed_job_id=%s; charging server amount=%s",
                    data["amount"], managed_job.id, amount,
                )
            payable = managed_job
        elif amount is None:
            return _blocked("amount is required when no job or quote is given.", status.HTTP_400_BAD_REQUEST)

        try:
            payment = create_payment(
                user=request.user,
                phone_number=data["phone_number"],
                amount=amount,
                payable=payable,
                account_reference=data.get("account_reference", ""),
                description=data.get("description", ""),
            )
            initiate_stk_push(payment)
            _save_profile_phone(request, data["phone_number"])
        except MpesaError as exc:
            # Payment row has already been marked failed where relevant.
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception:  # noqa: BLE001 — never leak internals
            logger.exception("Unexpected M-Pesa initiation failure user_id=%s", getattr(request.user, "id", None))
            return Response(
                {"detail": "Could not start the M-Pesa payment. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(MpesaPaymentReadSerializer(payment).data, status=status.HTTP_201_CREATED)

    def _pay_quote(self, request, data) -> Response:
        """Accept + pay a quote through the canonical flow so it settles."""
        from api.payment_views import _request_artwork_block
        from payments.models import Payment
        from payments.services import initiate_stk_push as canonical_initiate_stk_push
        from quotes.acceptance import _can_accept, accept_quote_for_payment
        from quotes.models import Quote

        quote = Quote.objects.select_related("quote_request").filter(pk=data["quote_id"]).first()
        if quote is None:
            return _blocked("Quote not found.", status.HTTP_404_NOT_FOUND)
        if not _can_accept(quote, request.user):
            return _blocked("You cannot pay for this quote.", status.HTTP_403_FORBIDDEN)
        block_reason = _request_artwork_block(quote.quote_request)
        if block_reason:
            return _blocked(block_reason, status.HTTP_400_BAD_REQUEST, artwork_payment_block_reason=block_reason)

        phone = data["phone_number"]
        try:
            quote, payment = accept_quote_for_payment(quote=quote, accepted_by=request.user, payer_phone=phone)
            stk_request = canonical_initiate_stk_push(payment=payment, phone_number=phone)
        except DjangoValidationError as exc:
            return _blocked("; ".join(exc.messages), status.HTTP_400_BAD_REQUEST)
        except Exception:  # noqa: BLE001 — Daraja/network; never leak internals
            logger.exception("Canonical STK push failed for quote_id=%s", quote.id)
            return _blocked("Could not start the M-Pesa payment. Please try again.", status.HTTP_502_BAD_GATEWAY)
        _save_profile_phone(request, phone)
        payment.refresh_from_db()
        return Response(
            {
                "payment_id": payment.id,
                "quote_id": quote.id,
                "quote_status": quote.status,
                "amount": str(payment.amount),
                "currency": payment.currency,
                "status": payment.status,
                "state": payment.state,
                "state_label": payment.state_label,
                "is_paid": payment.status == Payment.STATUS_PAID,
                "stk_status": stk_request.status,
                "customer_message": stk_request.customer_message or "",
            },
            status=status.HTTP_201_CREATED,
        )


def _callback_stk(payload: dict) -> dict:
    body = payload.get("Body") if isinstance(payload, dict) else {}
    stk = body.get("stkCallback") if isinstance(body, dict) else {}
    return stk if isinstance(stk, dict) else {}


def _callback_checkout_request_id(payload: dict) -> str:
    return str(_callback_stk(payload).get("CheckoutRequestID") or "").strip()


def _is_status_query_result(payload: dict) -> bool:
    """True for the ``Result``-shaped body of a Transaction Status Query."""
    return isinstance(payload, dict) and isinstance(payload.get("Result"), dict)


ACCEPTED = {"ResultCode": 0, "ResultDesc": "Accepted"}


def _log_rejected(payload: dict, reason: str) -> None:
    stk = _callback_stk(payload)
    try:
        MpesaCallbackLog.objects.create(
            checkout_request_id=str(stk.get("CheckoutRequestID") or "")[:64],
            merchant_request_id=str(stk.get("MerchantRequestID") or "")[:64],
            result_code=str(stk.get("ResultCode") if stk.get("ResultCode") is not None else "")[:12],
            result_desc=f"REJECTED: {reason}",
            payload=payload if isinstance(payload, dict) else {},
        )
    except Exception:  # noqa: BLE001 — auditing must never break the response
        logger.exception("Could not record rejected M-Pesa callback")


def dispatch_verified_stk_callback(payload: dict, *, canonical_response: bool = False) -> Response:
    """Apply an STK callback that already passed the authenticity gate.

    Every callback must reference a CheckoutRequestID we persisted when Daraja
    accepted one of OUR pushes (an MpesaPayment or a canonical MpesaSTKRequest).
    Unknown -> 404 + log, never applied. Final transactions are no-ops inside
    the processors (select_for_update + terminal guard). ``canonical_response``
    keeps the legacy /api/payments/mpesa-callback/ response body.
    """
    from payments.models import MpesaSTKRequest
    from payments.services import handle_stk_callback

    stk = _callback_stk(payload)
    checkout_id = _callback_checkout_request_id(payload)
    if not checkout_id:
        logger.warning("Rejected M-Pesa callback without CheckoutRequestID")
        _log_rejected(payload, "missing CheckoutRequestID")
        return Response({"ResultCode": 1, "ResultDesc": "Missing CheckoutRequestID"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        int(str(stk.get("ResultCode")).strip())
    except (TypeError, ValueError):
        logger.warning("Rejected M-Pesa callback with invalid ResultCode checkout_request_id=%s", checkout_id)
        _log_rejected(payload, "invalid ResultCode")
        return Response({"ResultCode": 1, "ResultDesc": "Invalid ResultCode"}, status=status.HTTP_400_BAD_REQUEST)

    owns_mpesa_payment = MpesaPayment.objects.filter(checkout_request_id=checkout_id).exists()
    owns_canonical = (
        not owns_mpesa_payment and MpesaSTKRequest.objects.filter(checkout_request_id=checkout_id).exists()
    )
    if not owns_mpesa_payment and not owns_canonical:
        logger.warning("Rejected M-Pesa callback for unknown checkout_request_id=%s", checkout_id)
        _log_rejected(payload, "unknown CheckoutRequestID")
        return Response({"ResultCode": 1, "ResultDesc": "Unknown CheckoutRequestID"}, status=status.HTTP_404_NOT_FOUND)

    try:
        if owns_mpesa_payment:
            process_callback(payload)
            return Response(ACCEPTED)
        result = handle_stk_callback(callback_payload=payload)
        return Response(result if canonical_response else ACCEPTED)
    except DjangoValidationError as exc:
        logger.warning("M-Pesa callback not applied checkout_request_id=%s: %s", checkout_id, exc)
        if canonical_response:
            return Response({"status": "failed", "detail": "; ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception:  # noqa: BLE001
        logger.exception("M-Pesa callback processing failed checkout_request_id=%s", checkout_id)
    # Logged; a Daraja retry would replay the same body, so do not ask for one.
    return Response(ACCEPTED)


class MpesaTransactionStatusView(APIView):
    """POST /api/payments/mpesa/result/  (and /result/<secret>/)

    Daraja's ``ResultURL``/``QueueTimeOutURL`` for a Transaction Status Query.
    Answers asynchronously, unlike the STK callback, and is the only Daraja
    notification that can confirm a payment whose STK callback was lost.

    Must pass the callback authenticity gate (403 otherwise). A verified body
    is matched to a persisted payment by receipt; unknown receipts are logged
    and never applied.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def post(self, request, callback_token: str | None = None):
        rejection = reject_unverified_callback(request, callback_token, route="mpesa-result")
        if rejection is not None:
            return rejection
        payload = request.data if isinstance(request.data, dict) else {}
        try:
            process_transaction_status_result(payload)
        except Exception:  # noqa: BLE001
            logger.exception("M-Pesa status-query result processing failed")
        return Response(ACCEPTED)


class MpesaCallbackView(APIView):
    """POST /api/payments/mpesa/callback/  (and /callback/<secret>/)

    Safaricom's webhook. Must pass the callback authenticity gate (secret +
    IP allowlist; 403 otherwise), then is dispatched by ownership of the
    persisted CheckoutRequestID:
      * an ``MpesaPayment`` owns it -> this app's ``process_callback``;
      * a canonical ``payments.MpesaSTKRequest`` owns it -> ``handle_stk_callback``
        (this URL is also the configured ``MPESA_CALLBACK_URL`` for the
        canonical quote flow);
      * nobody owns it -> 404, logged, never applied.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []  # no session/JWT evaluation on this route

    def post(self, request, callback_token: str | None = None):
        rejection = reject_unverified_callback(request, callback_token, route="mpesa-callback")
        if rejection is not None:
            return rejection
        serializer = MpesaCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=False)  # Daraja's shape is its own
        payload = serializer.validated_data.get("payload") or request.data
        if _is_status_query_result(payload):
            # A Transaction Status Query pointed at this URL: its shape must not
            # be read as an STK callback.
            try:
                process_transaction_status_result(payload)
            except Exception:  # noqa: BLE001
                logger.exception("M-Pesa status-query result processing failed")
            return Response(ACCEPTED)
        return dispatch_verified_stk_callback(payload if isinstance(payload, dict) else {})


class MpesaPaymentDetailView(APIView):
    """GET /api/payments/mpesa/{id}/ — poll for the frontend card."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk: int):
        payment = get_object_or_404(MpesaPayment, pk=pk, user=request.user)
        return Response(MpesaPaymentReadSerializer(payment).data)


class MpesaPaymentQueryView(APIView):
    """POST /api/payments/mpesa/{id}/query/ — ask Daraja directly.

    For pushes whose callback never arrived. Can never mark a payment paid
    on its own; that requires the callback's receipt number.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk: int):
        payment = get_object_or_404(MpesaPayment, pk=pk, user=request.user)
        if payment.is_terminal:
            return Response(MpesaPaymentReadSerializer(payment).data)
        try:
            query_stk_status(payment)
        except MpesaError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response(MpesaPaymentReadSerializer(payment).data)


class MpesaPaymentListView(APIView):
    """GET /api/payments/mpesa/transactions/ — the client's payment history."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = MpesaPayment.objects.filter(user=request.user).order_by("-initiated_at")[:50]
        return Response(MpesaPaymentReadSerializer(queryset, many=True).data)
