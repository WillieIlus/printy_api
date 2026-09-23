"""M-Pesa views.

Security rules that matter here:

1. The callback is AllowAny — Safaricom cannot authenticate. It is safe
   because it only ever acts on a checkout_request_id Daraja itself issued.
2. Every other view requires authentication and only touches payments the
   requester created.
3. The callback must always return 200, or Daraja retries a body we already
   handled. Errors are logged, never surfaced as a 4xx/5xx to Safaricom.
"""

from __future__ import annotations

import logging

from django.contrib.contenttypes.models import ContentType
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MpesaPayment
from .serializers import MpesaCallbackSerializer, MpesaPaymentReadSerializer, MpesaStkPushSerializer
from .services import MpesaError, create_payment, initiate_stk_push, process_callback, query_stk_status

logger = logging.getLogger("payments")

# How the client asked us to attach this payment.
PAYABLE_MODELS = {
    "managed_job_id": ("jobs", "managedjob"),
    "quote_id": ("quotes", "quote"),
    "quote_request_id": ("quotes", "quoterequest"),
}


def _resolve_payable(data: dict):
    for key, (app_label, model) in PAYABLE_MODELS.items():
        value = data.get(key)
        if not value:
            continue
        try:
            content_type = ContentType.objects.get_by_natural_key(app_label, model)
        except ContentType.DoesNotExist:
            return None
        return content_type.model_class().objects.filter(pk=value).first()
    return None


class MpesaStkPushView(APIView):
    """POST /api/payments/mpesa/stk-push/

    Sends the "Lipa na M-Pesa" prompt to the customer's phone. The response
    means "Daraja accepted the request", never "the payment succeeded".
    """

    permission_classes = [IsAuthenticated]
    throttle_scope = "mpesa_stk_push"

    def post(self, request):
        serializer = MpesaStkPushSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        payable = _resolve_payable(data)
        if data.get("managed_job_id") and payable is None:
            return Response({"detail": "Job not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            payment = create_payment(
                user=request.user,
                phone_number=data["phone_number"],
                amount=data["amount"],
                payable=payable,
                account_reference=data.get("account_reference", ""),
                description=data.get("description", ""),
            )
            initiate_stk_push(payment)
        except MpesaError as exc:
            # Payment row has already been marked failed where relevant.
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as exc:  # noqa: BLE001 — never leak internals
            logger.exception("Unexpected M-Pesa initiation failure user_id=%s", getattr(request.user, "id", None))
            return Response(
                {"detail": "Could not start the M-Pesa payment. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(MpesaPaymentReadSerializer(payment).data, status=status.HTTP_201_CREATED)


def _callback_checkout_request_id(payload: dict) -> str:
    body = payload.get("Body") if isinstance(payload, dict) else {}
    stk = body.get("stkCallback") if isinstance(body, dict) else {}
    return str(stk.get("CheckoutRequestID") or "")


class MpesaCallbackView(APIView):
    """POST /api/payments/mpesa/callback/

    Safaricom's webhook. Idempotent: replays are logged and ignored.

    This single URL is also the configured ``MPESA_CALLBACK_URL`` for the
    canonical payments system (``payments.services.initiate_stk_push``
    publishes the same setting), so a live Daraja callback for a canonical
    ``MpesaSTKRequest`` lands here too. Dispatch by ownership:
      * if a ``MpesaPayment`` owns the ``CheckoutRequestID`` -> this app;
      * otherwise hand the body to the canonical ``handle_stk_callback``,
        which reconciles ``payments.MpesaSTKRequest`` / ``Payment`` and
        creates the ManagedJob.
    Without the fallback, canonical quote payments hang in ``processing``
    forever in production (stub mode masks it because it fabricates ids).
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []  # no session/JWT evaluation on this route

    def post(self, request):
        serializer = MpesaCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=False)  # Daraja's shape is its own
        payload = serializer.validated_data.get("payload") or request.data
        try:
            checkout_id = _callback_checkout_request_id(payload)
            if checkout_id and MpesaPayment.objects.filter(checkout_request_id=checkout_id).exists():
                process_callback(payload)
            else:
                # Canonical quote payments (or an unknown request). Guarded:
                # handle_stk_callback is itself atomic + idempotent.
                from payments.services import handle_stk_callback

                handle_stk_callback(callback_payload=payload)
        except Exception:  # noqa: BLE001
            logger.exception("M-Pesa callback processing failed")
            # Still 200 — we logged it, and a retry would replay the same body.
        return Response({"ResultCode": 0, "ResultDesc": "Accepted"})


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
