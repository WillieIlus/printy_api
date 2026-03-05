"""Subscription and payment serializers."""
from rest_framework import serializers

from .models import MpesaStkRequest, Payment, Subscription, SubscriptionPlan


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    """Read-only plan serializer."""

    days_in_period = serializers.SerializerMethodField()

    class Meta:
        model = SubscriptionPlan
        fields = ["id", "name", "price", "billing_period", "days_in_period"]

    def get_days_in_period(self, obj):
        return obj.days_in_period()


class SubscriptionSerializer(serializers.ModelSerializer):
    """Subscription detail."""

    plan = SubscriptionPlanSerializer(read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "shop",
            "plan",
            "status",
            "period_start",
            "period_end",
            "next_billing_date",
            "last_payment_date",
        ]


class StkPushSerializer(serializers.Serializer):
    """STK push request body."""

    phone = serializers.CharField(required=True)
    plan_id = serializers.IntegerField(required=True)
