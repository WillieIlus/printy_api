from rest_framework import serializers

from catalog.models import Product
from quotes.models import QuoteDraft, QuoteRequest, ShopQuote
from shops.models import Shop


class FinishingSelectionSerializer(serializers.Serializer):
    finishing_rate_id = serializers.IntegerField()
    selected_side = serializers.ChoiceField(choices=["front", "back", "both"], default="both")


class CalculatorPreviewSerializer(serializers.Serializer):
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all())
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1)
    paper = serializers.IntegerField()
    machine = serializers.IntegerField()
    color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="SIMPLEX")
    finishings = FinishingSelectionSerializer(many=True, required=False)


class QuoteDraftCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all(), required=False, allow_null=True)
    selected_product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    calculator_inputs_snapshot = serializers.JSONField()
    pricing_snapshot = serializers.JSONField(required=False)
    custom_product_snapshot = serializers.JSONField(required=False)
    request_details_snapshot = serializers.JSONField(required=False)


class QuoteDraftReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteDraft
        fields = [
            "id",
            "draft_reference",
            "title",
            "status",
            "shop",
            "selected_product",
            "calculator_inputs_snapshot",
            "pricing_snapshot",
            "custom_product_snapshot",
            "request_details_snapshot",
            "created_at",
            "updated_at",
        ]


class QuoteDraftSendSerializer(serializers.Serializer):
    shops = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all(), many=True)
    request_details_snapshot = serializers.JSONField(required=False)


class QuoteRequestReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "request_reference",
            "shop",
            "created_by",
            "status",
            "customer_name",
            "customer_email",
            "customer_phone",
            "request_snapshot",
            "created_at",
            "updated_at",
        ]


class QuoteResponseCreateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["pending", "modified", "accepted", "rejected"])
    response_snapshot = serializers.JSONField()
    revised_pricing_snapshot = serializers.JSONField(required=False)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    note = serializers.CharField(required=False, allow_blank=True)
    turnaround_days = serializers.IntegerField(required=False, min_value=0)


class QuoteResponseReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShopQuote
        fields = [
            "id",
            "quote_reference",
            "quote_request",
            "shop",
            "status",
            "total",
            "note",
            "turnaround_days",
            "response_snapshot",
            "revised_pricing_snapshot",
            "created_at",
            "sent_at",
        ]
