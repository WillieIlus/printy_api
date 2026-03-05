"""Subscription and payment API views."""
import logging
from datetime import date, timedelta

from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet

from api.permissions import IsShopOwner
from shops.models import Shop

from .models import MpesaStkRequest, Payment, Subscription, SubscriptionPlan
from .serializers import StkPushSerializer, SubscriptionPlanSerializer, SubscriptionSerializer
from .services.mpesa import initiate_stk_push, normalize_phone

logger = logging.getLogger("payments")


class SubscriptionPlanViewSet(ReadOnlyModelViewSet):
    """GET /api/subscription/plans/ — list plans."""

    queryset = SubscriptionPlan.objects.all()
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [AllowAny]


class ShopSubscriptionView(APIView):
    """GET /api/shops/<shop_slug>/subscription/ — shop's subscription."""

    permission_classes = [IsAuthenticated, IsShopOwner]

    def get_shop(self):
        slug = self.kwargs.get("shop_slug")
        shop = get_object_or_404(Shop, slug=slug)
        if shop.owner_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Not your shop.")
        return shop

    def get(self, request, shop_slug):
        shop = self.get_shop()
        sub, _ = Subscription.objects.get_or_create(
            shop=shop,
            defaults={"status": Subscription.TRIAL},
        )
        return Response(SubscriptionSerializer(sub).data)


class MpesaStkPushView(APIView):
    """POST /api/shops/<shop_slug>/payments/mpesa/stk-push/ — initiate STK push."""

    permission_classes = [IsAuthenticated, IsShopOwner]

    def get_shop(self):
        slug = self.kwargs.get("shop_slug")
        shop = get_object_or_404(Shop, slug=slug)
        if shop.owner_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Not your shop.")
        return shop

    def post(self, request, shop_slug):
        shop = self.get_shop()
        ser = StkPushSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        phone = ser.validated_data["phone"]
        plan_id = ser.validated_data["plan_id"]

        plan = get_object_or_404(SubscriptionPlan, pk=plan_id)
        amount = plan.price

        try:
            normalized = normalize_phone(phone)
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        account_ref = f"SHOP{shop.id}"
        try:
            result = initiate_stk_push(
                phone=normalized,
                amount=amount,
                account_ref=account_ref,
            )
        except Exception as e:
            logger.exception("STK push failed: %s", e)
            return Response(
                {"detail": "Failed to initiate payment. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        checkout_id = result.get("CheckoutRequestID") or result.get("checkout_request_id")
        if not checkout_id:
            logger.warning("No CheckoutRequestID in response: %s", result)
            return Response(
                {"detail": "Invalid response from payment provider."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        MpesaStkRequest.objects.create(
            shop=shop,
            plan=plan,
            phone=normalized,
            amount=amount,
            checkout_request_id=checkout_id,
            status=MpesaStkRequest.INITIATED,
        )

        return Response({
            "checkout_request_id": checkout_id,
            "message": "Payment request sent. Complete on your phone.",
        })


@method_decorator(csrf_exempt, name="dispatch")
class MpesaCallbackView(APIView):
    """POST /api/payments/mpesa/callback/ — Daraja STK callback (no auth)."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        payload = request.data
        logger.info("M-Pesa callback received: %s", payload)

        body = payload.get("Body", {})
        stk_callback = body.get("stkCallback", {})
        checkout_id = stk_callback.get("CheckoutRequestID")
        result_code = stk_callback.get("ResultCode")
        result_desc = stk_callback.get("ResultDesc", "")

        if not checkout_id:
            logger.warning("Callback missing CheckoutRequestID")
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

        try:
            stk_req = MpesaStkRequest.objects.select_related("shop", "plan").get(
                checkout_request_id=checkout_id
            )
        except MpesaStkRequest.DoesNotExist:
            logger.warning("Unknown CheckoutRequestID: %s", checkout_id)
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

        # Idempotency: already processed
        if stk_req.status == MpesaStkRequest.SUCCESS:
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

        stk_req.raw_callback_payload = payload
        stk_req.save(update_fields=["raw_callback_payload"])

        if result_code != 0:
            stk_req.status = MpesaStkRequest.FAILED
            stk_req.save(update_fields=["status"])
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

        # Extract receipt
        callback_metadata = stk_callback.get("CallbackMetadata", {})
        metadata_list = callback_metadata.get("Item", [])
        receipt = ""
        for item in metadata_list:
            if item.get("Name") == "MpesaReceiptNumber":
                receipt = str(item.get("Value", ""))
                break

        stk_req.receipt_number = receipt
        stk_req.status = MpesaStkRequest.SUCCESS
        stk_req.save(update_fields=["receipt_number", "status"])

        # Activate subscription
        sub, _ = Subscription.objects.get_or_create(
            shop=stk_req.shop,
            defaults={"status": Subscription.TRIAL},
        )
        plan = stk_req.plan
        today = date.today()
        period_days = plan.days_in_period()
        period_end = today + timedelta(days=period_days)

        sub.plan = plan
        sub.status = Subscription.ACTIVE
        sub.period_start = today
        sub.period_end = period_end
        sub.next_billing_date = period_end
        sub.last_payment_date = today
        sub.save()

        Payment.objects.create(
            subscription=sub,
            amount=stk_req.amount,
            method=Payment.MPESA_C2B,
            status=Payment.COMPLETED,
            receipt_number=receipt,
            phone=stk_req.phone,
            request_id=checkout_id,
            period_start=today,
            period_end=period_end,
            metadata={"stk_request_id": stk_req.id},
        )

        return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
