"""Serializers for the M-Pesa payment flow.

The initiate serializer accepts every phone format Printy's frontend uses and
normalizes to 2547XXXXXXXX. The read serializer exposes only canonical states
so the frontend card can never invent a paid state.
"""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from .models import MpesaPayment, MpesaPaymentStatus, MpesaReconciliationStatus
from .services import normalize_msisdn


class MpesaStkPushSerializer(serializers.Serializer):
    """Body of POST /api/payments/mpesa/stk-push/."""

    phone_number = serializers.CharField(max_length=20)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    account_reference = serializers.CharField(required=False, allow_blank=True, max_length=40)
    description = serializers.CharField(required=False, allow_blank=True, max_length=100)

    # The payable is optional — a payment can exist before a job does.
    managed_job_id = serializers.IntegerField(required=False, min_value=1)
    quote_id = serializers.IntegerField(required=False, min_value=1)
    quote_request_id = serializers.IntegerField(required=False, min_value=1)

    def validate_phone_number(self, value: str) -> str:
        return normalize_msisdn(value)

    def validate(self, attrs: dict) -> dict:
        targets = [k for k in ("managed_job_id", "quote_id", "quote_request_id") if attrs.get(k)]
        if len(targets) > 1:
            raise serializers.ValidationError(
                "Pass only one of managed_job_id, quote_id or quote_request_id."
            )
        return attrs


class MpesaPaymentReadSerializer(serializers.ModelSerializer):
    """Canonical payment state for the frontend card."""

    status = serializers.ChoiceField(choices=MpesaPaymentStatus.choices, read_only=True)
    reconciliation_status = serializers.ChoiceField(
        choices=MpesaReconciliationStatus.choices, read_only=True
    )

    class Meta:
        model = MpesaPayment
        fields = [
            "id",
            "phone_number",
            "amount",
            "currency",
            "status",
            "reconciliation_status",
            "account_reference",
            "description",
            "mpesa_receipt_number",
            "paid_amount",
            "result_desc",
            "customer_message",
            "merchant_request_id",
            "checkout_request_id",
            "initiated_at",
            "stk_pushed_at",
            "confirmed_at",
        ]
        read_only_fields = fields

    def to_representation(self, instance: MpesaPayment) -> dict:
        data = super().to_representation(instance)
        # The frontend card must distinguish "money moved" from "we asked for money".
        data["is_paid"] = instance.is_paid
        data["is_terminal"] = instance.is_terminal
        data["amount_matched"] = instance.is_amount_matched
        data["payable_id"] = instance.object_id
        data["payable_type"] = instance.content_type.model if instance.content_type_id else None
        return data


class MpesaCallbackSerializer(serializers.Serializer):
    """Loose validation of Daraja's callback body.

    Daraja controls this shape, not us — so validate minimally and let
    services.process_callback decide what to do with it.
    """

    Body = serializers.DictField(required=False)

    def to_internal_value(self, data):
        return {"payload": data if isinstance(data, dict) else {}}
