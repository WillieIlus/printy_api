from rest_framework import serializers
from decimal import Decimal

from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate
from api.size_utils import normalize_size_payload, validate_size_selection
from quotes.choices import QuoteDraftStatus, QuoteStatus, ShopQuoteStatus
from quotes.models import QuoteDraft, QuoteRequest, ShopQuote
from quotes.turnaround import estimate_turnaround, legacy_days_from_hours
from shops.models import Shop


class FinishingSelectionSerializer(serializers.Serializer):
    finishing_rate = serializers.PrimaryKeyRelatedField(queryset=FinishingRate.objects.filter(is_active=True))
    selected_side = serializers.ChoiceField(choices=["front", "back", "both"], default="both")

    def to_internal_value(self, data):
        if isinstance(data, dict) and "finishing_rate" not in data and "finishing_rate_id" in data:
            data = {**data, "finishing_rate": data["finishing_rate_id"]}
        return super().to_internal_value(data)


class CalculatorPreviewSerializer(serializers.Serializer):
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all())
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1)
    paper = serializers.PrimaryKeyRelatedField(queryset=Paper.objects.filter(is_active=True))
    machine = serializers.PrimaryKeyRelatedField(queryset=Machine.objects.filter(is_active=True))
    color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="SIMPLEX")
    apply_duplex_surcharge = serializers.BooleanField(required=False, allow_null=True, default=None)
    size_mode = serializers.ChoiceField(choices=["standard", "custom"], required=False, default="custom")
    size_label = serializers.CharField(required=False, allow_blank=True, default="")
    input_unit = serializers.ChoiceField(choices=["mm", "cm", "in"], required=False, default="mm")
    width_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    height_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    finishings = FinishingSelectionSerializer(many=True, required=False)

    def to_internal_value(self, data):
        normalized = normalize_size_payload(
            data,
            legacy_width_keys=("chosen_width_mm",),
            legacy_height_keys=("chosen_height_mm",),
        )
        return super().to_internal_value(normalized)

    def validate(self, attrs):
        attrs = validate_size_selection(attrs)
        shop = attrs["shop"]
        product = attrs.get("product")
        errors = {}

        if product and product.shop_id != shop.id:
            errors["product"] = ["Product must belong to the selected shop."]
        if not product and (not attrs.get("width_mm") or not attrs.get("height_mm")):
            errors["non_field_errors"] = ["width_mm and height_mm are required for custom previews."]

        if attrs["paper"].shop_id != shop.id:
            errors["paper"] = ["Paper must belong to the selected shop."]
        if attrs["machine"].shop_id != shop.id:
            errors["machine"] = ["Machine must belong to the selected shop."]

        finishing_errors = []
        for selection in attrs.get("finishings") or []:
            if selection["finishing_rate"].shop_id != shop.id:
                finishing_errors.append(
                    {"finishing_rate": ["Finishing rate must belong to the selected shop."]}
                )
            else:
                finishing_errors.append({})
        if any(item for item in finishing_errors):
            errors["finishings"] = finishing_errors

        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class BookletCalculatorPreviewSerializer(serializers.Serializer):
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all())
    quantity = serializers.IntegerField(min_value=1)
    total_pages = serializers.IntegerField(min_value=4)
    binding_type = serializers.ChoiceField(choices=["saddle_stitch", "perfect_bind", "wire_o"], default="saddle_stitch")
    cover_paper = serializers.PrimaryKeyRelatedField(queryset=Paper.objects.filter(is_active=True))
    insert_paper = serializers.PrimaryKeyRelatedField(queryset=Paper.objects.filter(is_active=True))
    cover_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="DUPLEX")
    insert_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="DUPLEX")
    cover_color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    insert_color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    cover_lamination_mode = serializers.ChoiceField(choices=["none", "front", "both"], default="none")
    cover_lamination_finishing_rate = serializers.PrimaryKeyRelatedField(
        queryset=FinishingRate.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    binding_finishing_rate = serializers.PrimaryKeyRelatedField(
        queryset=FinishingRate.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    turnaround_hours = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    size_mode = serializers.ChoiceField(choices=["standard", "custom"], required=False, default="custom")
    size_label = serializers.CharField(required=False, allow_blank=True, default="")
    input_unit = serializers.ChoiceField(choices=["mm", "cm", "in"], required=False, default="mm")
    width_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    height_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def to_internal_value(self, data):
        normalized = normalize_size_payload(
            data,
            legacy_width_keys=("chosen_width_mm",),
            legacy_height_keys=("chosen_height_mm",),
        )
        return super().to_internal_value(normalized)

    def validate(self, attrs):
        attrs = validate_size_selection(attrs)
        shop = attrs["shop"]
        errors = {}
        if not attrs.get("width_mm") or not attrs.get("height_mm"):
            errors["non_field_errors"] = ["width_mm and height_mm are required for booklet previews."]
        if attrs["cover_paper"].shop_id != shop.id:
            errors["cover_paper"] = ["Cover paper must belong to the selected shop."]
        if attrs["insert_paper"].shop_id != shop.id:
            errors["insert_paper"] = ["Insert paper must belong to the selected shop."]
        if attrs.get("cover_lamination_finishing_rate") and attrs["cover_lamination_finishing_rate"].shop_id != shop.id:
            errors["cover_lamination_finishing_rate"] = ["Lamination rate must belong to the selected shop."]
        if attrs.get("binding_finishing_rate") and attrs["binding_finishing_rate"].shop_id != shop.id:
            errors["binding_finishing_rate"] = ["Binding rate must belong to the selected shop."]
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class QuoteDraftCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all(), required=False, allow_null=True)
    selected_product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    calculator_inputs_snapshot = serializers.JSONField()
    pricing_snapshot = serializers.JSONField(required=False)
    custom_product_snapshot = serializers.JSONField(required=False)
    request_details_snapshot = serializers.JSONField(required=False)


class QuoteDraftUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all(), required=False, allow_null=True)
    selected_product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    calculator_inputs_snapshot = serializers.JSONField(required=False)
    pricing_snapshot = serializers.JSONField(required=False)
    custom_product_snapshot = serializers.JSONField(required=False)
    request_details_snapshot = serializers.JSONField(required=False)


class QuoteDraftReadSerializer(serializers.ModelSerializer):
    generated_request_ids = serializers.SerializerMethodField()
    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_slug = serializers.CharField(source="shop.slug", read_only=True)
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)

    class Meta:
        model = QuoteDraft
        fields = [
            "id",
            "draft_reference",
            "title",
            "status",
            "shop",
            "shop_name",
            "shop_slug",
            "shop_currency",
            "selected_product",
            "calculator_inputs_snapshot",
            "pricing_snapshot",
            "custom_product_snapshot",
            "request_details_snapshot",
            "generated_request_ids",
            "created_at",
            "updated_at",
        ]

    def get_generated_request_ids(self, obj):
        return list(obj.generated_requests.values_list("id", flat=True))


class QuoteDraftSendSerializer(serializers.Serializer):
    shops = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all(), many=True)
    request_details_snapshot = serializers.JSONField(required=False)


class QuoteRequestReadSerializer(serializers.ModelSerializer):
    source_draft_reference = serializers.CharField(source="source_draft.draft_reference", read_only=True)
    latest_response = serializers.SerializerMethodField()
    responses_count = serializers.SerializerMethodField()

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
            "source_draft",
            "source_draft_reference",
            "request_snapshot",
            "latest_response",
            "responses_count",
            "created_at",
            "updated_at",
        ]

    def get_latest_response(self, obj):
        latest = obj.get_latest_response()
        if not latest:
            return None
        return {
            "id": latest.id,
            "quote_reference": latest.quote_reference,
            "status": latest.status,
            "total": latest.total,
            "turnaround_days": latest.turnaround_days,
            "turnaround_hours": latest.turnaround_hours,
            "estimated_ready_at": latest.estimated_ready_at,
            "human_ready_text": latest.human_ready_text,
            "turnaround_label": latest.turnaround_label,
            "created_at": latest.created_at,
            "sent_at": latest.sent_at,
        }

    def get_responses_count(self, obj):
        return obj.shop_quotes.count()


class DashboardQuoteRequestSummarySerializer(serializers.ModelSerializer):
    source_draft_reference = serializers.CharField(source="source_draft.draft_reference", read_only=True)
    latest_response = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "request_reference",
            "status",
            "customer_name",
            "customer_email",
            "customer_phone",
            "source_draft_reference",
            "latest_response",
            "created_at",
            "updated_at",
        ]

    def get_latest_response(self, obj):
        latest_response_id = getattr(obj, "latest_response_id", None)
        if not latest_response_id:
            return None
        return {
            "id": latest_response_id,
            "quote_reference": getattr(obj, "latest_response_reference", ""),
            "status": getattr(obj, "latest_response_status", ""),
            "total": getattr(obj, "latest_response_total", None),
            "created_at": getattr(obj, "latest_response_created_at", None),
            "sent_at": getattr(obj, "latest_response_sent_at", None),
        }


class QuoteResponseCreateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ShopQuoteStatus.choices)
    response_snapshot = serializers.JSONField()
    revised_pricing_snapshot = serializers.JSONField(required=False)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    note = serializers.CharField(required=False, allow_blank=True)
    turnaround_days = serializers.IntegerField(required=False, min_value=0)
    turnaround_hours = serializers.IntegerField(required=False, min_value=1)

    def validate_status(self, value):
        if value not in {
            ShopQuoteStatus.PENDING,
            ShopQuoteStatus.MODIFIED,
            ShopQuoteStatus.ACCEPTED,
            ShopQuoteStatus.REJECTED,
        }:
            raise serializers.ValidationError("Workflow quote responses only support pending, modified, accepted, or rejected.")
        return value


class QuoteResponseUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ShopQuoteStatus.choices)
    response_snapshot = serializers.JSONField(required=False)
    revised_pricing_snapshot = serializers.JSONField(required=False)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    note = serializers.CharField(required=False, allow_blank=True)
    turnaround_days = serializers.IntegerField(required=False, min_value=0)
    turnaround_hours = serializers.IntegerField(required=False, min_value=1)

    def validate_status(self, value):
        if value not in {
            ShopQuoteStatus.PENDING,
            ShopQuoteStatus.MODIFIED,
            ShopQuoteStatus.ACCEPTED,
            ShopQuoteStatus.REJECTED,
        }:
            raise serializers.ValidationError("Workflow quote responses only support pending, modified, accepted, or rejected.")
        return value


class QuoteResponseReadSerializer(serializers.ModelSerializer):
    request_reference = serializers.CharField(source="quote_request.request_reference", read_only=True)

    class Meta:
        model = ShopQuote
        fields = [
            "id",
            "quote_reference",
            "quote_request",
            "request_reference",
            "shop",
            "status",
            "total",
            "note",
            "turnaround_days",
            "turnaround_hours",
            "estimated_ready_at",
            "human_ready_text",
            "turnaround_label",
            "response_snapshot",
            "revised_pricing_snapshot",
            "revision_number",
            "pricing_locked_at",
            "created_at",
            "sent_at",
        ]
