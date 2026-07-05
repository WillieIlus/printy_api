"""
Quote marketplace serializers — customer vs shop separation.

Customer-facing: minimal shop internals, focus on request status and quote response.
Shop-facing: full request details, delivery info, services — everything to respond quickly.
"""
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.urls import reverse
from rest_framework import serializers

from quotes.choices import QuoteStatus, QuoteOfferStatus
from quotes.models import (
    QuoteItem,
    QuoteItemAttachment,
    QuoteItemFinishing,
    QuoteRequest,
    QuoteRequestAttachment,
    QuoteRequestMessage,
    QuoteRequestService,
    Quote,
    QuoteAttachment,
)
from quotes.request_brief import build_quote_request_whatsapp_handoff
from quotes.status_normalization import (
    normalize_quote_request_status,
    normalize_quote_response_status,
    quote_request_status_label,
    quote_response_status_label,
)
from quotes.turnaround import estimate_turnaround, legacy_days_from_hours, humanize_working_hours

from .visibility import (
    CLIENT_ACTOR,
    project_client_counterparty_name,
    project_client_identity,
    project_identity,
    project_participant_name,
    resolve_actor,
    resolve_topology_mode_for_quote_request,
)

# ---------------------------------------------------------------------------
# Nested / shared
# ---------------------------------------------------------------------------


class QuoteItemFinishingReadSerializer(serializers.ModelSerializer):
    """Read-only finishing for quote items."""

    finishing_rate_name = serializers.CharField(source="finishing_rate.name", read_only=True)

    class Meta:
        model = QuoteItemFinishing
        fields = ["id", "finishing_rate", "finishing_rate_name", "coverage_qty", "price_override", "apply_to_sides"]


class QuoteItemAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteItemAttachment
        fields = ["id", "file", "name", "created_at"]


class QuoteItemCustomerSerializer(serializers.ModelSerializer):
    """Quote item for customer view — no pricing_snapshot, no internal IDs."""

    product_name = serializers.SerializerMethodField()
    paper_label = serializers.SerializerMethodField()
    finishings = QuoteItemFinishingReadSerializer(many=True, read_only=True)

    class Meta:
        model = QuoteItem
        fields = [
            "id",
            "item_type",
            "product",
            "product_name",
            "title",
            "spec_text",
            "has_artwork",
            "quantity",
            "pricing_mode",
            "paper_label",
            "sides",
            "color_mode",
            "chosen_width_mm",
            "chosen_height_mm",
            "input_pages",
            "normalized_pages",
            "binding_type",
            "unit_price",
            "line_total",
            "needs_review",
            "finishings",
            "attachments",
            "created_at",
        ]

    def get_product_name(self, obj):
        if obj.item_type == "PRODUCT" and obj.product_id:
            return obj.product.name
        return obj.title or ""

    def get_paper_label(self, obj):
        if obj.paper_id:
            p = obj.paper
            return f"{p.sheet_size} {p.gsm}gsm {p.get_paper_type_display()}"
        return None


class QuoteItemShopSerializer(serializers.ModelSerializer):
    """Quote item for shop view — includes pricing_snapshot, needs_review."""

    product_name = serializers.SerializerMethodField()
    paper_label = serializers.SerializerMethodField()
    finishings = QuoteItemFinishingReadSerializer(many=True, read_only=True)
    attachments = QuoteItemAttachmentSerializer(many=True, read_only=True)
    attachments = QuoteItemAttachmentSerializer(many=True, read_only=True)
    calculation_description = serializers.SerializerMethodField()
    calculation_explanations = serializers.SerializerMethodField()

    class Meta:
        model = QuoteItem
        fields = [
            "id",
            "item_type",
            "product",
            "product_name",
            "title",
            "spec_text",
            "has_artwork",
            "quantity",
            "pricing_mode",
            "paper",
            "paper_label",
            "material",
            "chosen_width_mm",
            "chosen_height_mm",
            "sides",
            "color_mode",
            "machine",
            "special_instructions",
            "input_pages",
            "normalized_pages",
            "binding_type",
            "unit_price",
            "line_total",
            "calculation_description",
            "calculation_explanations",
            "pricing_locked_at",
            "needs_review",
            "finishings",
            "attachments",
        ]

    def get_product_name(self, obj):
        if obj.item_type == "PRODUCT" and obj.product_id:
            return obj.product.name
        return obj.title or ""

    def get_paper_label(self, obj):
        if obj.paper_id:
            p = obj.paper
            return f"{p.sheet_size} {p.gsm}gsm {p.get_paper_type_display()}"
        return None

    def get_calculation_description(self, obj):
        snapshot = obj.pricing_snapshot or {}
        return snapshot.get("calculation_description", "")

    def get_calculation_explanations(self, obj):
        snapshot = obj.pricing_snapshot or {}
        return snapshot.get("explanations", [])


class QuoteSummarySerializer(serializers.ModelSerializer):
    """Minimal shop quote for embedding in QuoteRequest responses."""

    estimated_working_hours = serializers.IntegerField(source="turnaround_hours", read_only=True)
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    whatsapp_available = serializers.SerializerMethodField()
    whatsapp_url = serializers.SerializerMethodField()
    whatsapp_label = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = [
            "id",
            "status",
            "raw_status",
            "status_label",
            "total",
            "turnaround_days",
            "turnaround_hours",
            "estimated_working_hours",
            "estimated_ready_at",
            "human_ready_text",
            "turnaround_label",
            "note",
            "revision_number",
            "sent_at",
            "created_at",
            "whatsapp_available",
            "whatsapp_url",
            "whatsapp_label",
        ]

    def get_status(self, obj):
        return normalize_quote_response_status(obj.status)

    def get_status_label(self, obj):
        return quote_response_status_label(self.get_status(obj))

    def _viewer_role(self, obj):
        request = self.context.get("request")
        if request and getattr(request.user, "id", None) == obj.shop.owner_id:
            return "shop"
        return "buyer"

    def _whatsapp_handoff(self, obj):
        return build_quote_request_whatsapp_handoff(obj.quote_request, viewer_role=self._viewer_role(obj))

    def get_whatsapp_available(self, obj):
        return self._whatsapp_handoff(obj).get("available", False)

    def get_whatsapp_url(self, obj):
        return self._whatsapp_handoff(obj).get("url", "")

    def get_whatsapp_label(self, obj):
        return self._whatsapp_handoff(obj).get("label", "")


class QuoteRequestServiceReadSerializer(serializers.ModelSerializer):
    """Read-only quote request service (e.g. delivery)."""

    service_rate_name = serializers.CharField(source="service_rate.name", read_only=True)
    service_rate_code = serializers.CharField(source="service_rate.code", read_only=True)

    class Meta:
        model = QuoteRequestService
        fields = ["id", "service_rate", "service_rate_name", "service_rate_code", "is_selected", "distance_km", "price_override"]


class QuoteRequestAttachmentSerializer(serializers.ModelSerializer):
    """Read attachment metadata with an authenticated download URL."""

    download_url = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequestAttachment
        fields = ["id", "download_url", "name", "created_at"]

    def get_download_url(self, obj):
        request = self.context.get("request")
        path = reverse("quote-request-attachment-download", kwargs={"pk": obj.pk})
        return request.build_absolute_uri(path) if request else path


class QuoteRequestAttachmentUploadSerializer(serializers.ModelSerializer):
    """Upload attachment (multipart/form-data)."""

    class Meta:
        model = QuoteRequestAttachment
        fields = ["file", "name"]
        extra_kwargs = {"file": {"required": True}, "name": {"required": False, "allow_blank": True}}


class QuoteRequestMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequestMessage
        fields = [
            "id",
            "sender_role",
            "sender_name",
            "message_kind",
            "body",
            "metadata",
            "quote",
            "created_at",
            "updated_at",
        ]

    def get_sender_name(self, obj):
        sender = obj.sender
        if not sender:
            return "System"
        
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        
        raw_name = getattr(sender, "name", "") or getattr(sender, "email", "") or "User"
        
        # Project based on role
        return project_participant_name(raw_name, obj.sender_role, actor=actor)


class QuoteInboxMessageSerializer(serializers.ModelSerializer):
    quote_request_id = serializers.IntegerField(read_only=True)
    quote_response_id = serializers.IntegerField(source="quote_id", read_only=True)
    shop_name = serializers.SerializerMethodField()
    client_name = serializers.SerializerMethodField()
    snippet = serializers.SerializerMethodField()
    has_attachment = serializers.SerializerMethodField()
    attachments_summary = serializers.SerializerMethodField()
    action_url = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequestMessage
        fields = [
            "id",
            "subject",
            "body",
            "snippet",
            "message_type",
            "direction",
            "quote_request_id",
            "quote_response_id",
            "shop_name",
            "client_name",
            "read_at",
            "created_at",
            "sent_at",
            "email_status",
            "has_attachment",
            "attachments_summary",
            "action_url",
        ]

    def get_shop_name(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        topology_mode = resolve_topology_mode_for_quote_request(obj.quote_request)
        if actor == CLIENT_ACTOR:
            return project_client_counterparty_name(
                fallback_name=obj.shop.name if obj.shop_id else None,
                topology_mode=topology_mode,
                request_snapshot=getattr(obj.quote_request, "request_snapshot", None),
            )
        return project_identity(obj.shop.name if obj.shop_id else None, actor=actor, topology_mode=topology_mode)

    def get_client_name(self, obj):
        request = self.context.get("request")
        if request and getattr(request.user, "id", None) == obj.quote_request.created_by_id:
            return ""
        actor = resolve_actor(getattr(request, "user", None))
        topology_mode = resolve_topology_mode_for_quote_request(obj.quote_request)
        return project_client_identity(
            obj.quote_request.customer_name or "",
            actor=actor,
            topology_mode=topology_mode,
        )

    def get_snippet(self, obj):
        return (obj.body or "").strip()[:140]

    def get_has_attachment(self, obj):
        return bool(self._attachments(obj))

    def get_attachments_summary(self, obj):
        return [
            {"id": attachment.id, "name": attachment.name or attachment.file.name.rsplit("/", 1)[-1]}
            for attachment in self._attachments(obj)[:5]
        ]

    def get_action_url(self, obj):
        metadata = obj.metadata or {}
        return metadata.get("action_url", "")

    def _attachments(self, obj):
        if obj.quote_id:
            return list(obj.quote.attachments.all())
        return list(obj.quote_request.attachments.all())


class QuoteAttachmentSerializer(serializers.ModelSerializer):
    """Read attachment — id, file URL, name."""

    class Meta:
        model = QuoteAttachment
        fields = ["id", "file", "name", "created_at"]


class QuoteAttachmentUploadSerializer(serializers.ModelSerializer):
    """Upload attachment (multipart/form-data)."""

    class Meta:
        model = QuoteAttachment
        fields = ["file", "name"]
        extra_kwargs = {"file": {"required": True}, "name": {"required": False, "allow_blank": True}}


# ---------------------------------------------------------------------------
# QuoteRequest — customer
# ---------------------------------------------------------------------------


class QuoteRequestCustomerCreateSerializer(serializers.ModelSerializer):
    """Customer: create draft quote request."""

    class Meta:
        model = QuoteRequest
        fields = [
            "shop",
            "customer_name",
            "customer_email",
            "customer_phone",
            "notes",
            "delivery_preference",
            "delivery_address",
            "delivery_location",
        ]

    def validate_shop(self, value):
        if not value or not value.is_active:
            raise serializers.ValidationError("Shop must be active.")
        return value

    def validate_delivery_location(self, value):
        if value and not self.initial_data.get("delivery_preference") == QuoteRequest.DELIVERY:
            return value  # Location optional when pickup
        return value

    def create(self, validated_data):
        validated_data["created_by"] = self.context["request"].user
        validated_data["status"] = QuoteStatus.DRAFT
        return super().create(validated_data)


class QuoteRequestCustomerUpdateSerializer(serializers.ModelSerializer):
    """Customer: patch draft (partial update)."""

    class Meta:
        model = QuoteRequest
        fields = [
            "customer_name",
            "customer_email",
            "customer_phone",
            "notes",
            "delivery_preference",
            "delivery_address",
            "delivery_location",
        ]

    def validate(self, attrs):
        instance = self.instance
        if instance and instance.status != QuoteStatus.DRAFT:
            raise serializers.ValidationError("Only draft quote requests can be updated.")
        return attrs


class QuoteRequestCustomerListSerializer(serializers.ModelSerializer):
    """Customer: list own quote requests — no shop internals."""

    shop_name = serializers.SerializerMethodField()
    shop_slug = serializers.CharField(source="shop.slug", read_only=True)
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)
    calculator_draft_file_id = serializers.IntegerField(read_only=True)
    items_count = serializers.SerializerMethodField()
    latest_sent_quote = serializers.SerializerMethodField()
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "calculator_draft_file_id",
            "status",
            "raw_status",
            "status_label",
            "items_count",
            "latest_sent_quote",
            "created_at",
            "updated_at",
        ]

    def get_shop_name(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if actor == CLIENT_ACTOR:
            return project_client_counterparty_name(
                fallback_name=obj.shop.name if obj.shop_id else None,
                request_snapshot=getattr(obj, "request_snapshot", None),
            )
        return project_identity(obj.shop.name, actor=actor)

    def get_items_count(self, obj):
        return obj.items.count()

    def get_latest_sent_quote(self, obj):
        sq = obj.get_latest_quote()
        if not sq:
            return None
        return QuoteSummarySerializer(sq, context=self.context).data

    def get_status(self, obj):
        return normalize_quote_request_status(obj.status)

    def get_status_label(self, obj):
        return quote_request_status_label(self.get_status(obj))


class QuoteRequestCustomerDetailSerializer(serializers.ModelSerializer):
    """Customer: detail for own quote request — items + latest sent quote, no pricing breakdown."""

    shop_name = serializers.SerializerMethodField()
    shop_slug = serializers.CharField(source="shop.slug", read_only=True)
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)
    calculator_draft_file_id = serializers.IntegerField(read_only=True)
    delivery_location_name = serializers.CharField(source="delivery_location.name", read_only=True)
    items = QuoteItemCustomerSerializer(many=True, read_only=True)
    services = QuoteRequestServiceReadSerializer(many=True, read_only=True)
    attachments = QuoteRequestAttachmentSerializer(many=True, read_only=True)
    messages = QuoteRequestMessageSerializer(many=True, read_only=True)
    latest_sent_quote = serializers.SerializerMethodField()
    whatsapp_summary = serializers.SerializerMethodField()
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "calculator_draft_file_id",
            "customer_name",
            "customer_email",
            "customer_phone",
            "status",
            "raw_status",
            "status_label",
            "notes",
            "delivery_preference",
            "delivery_address",
            "delivery_location",
            "delivery_location_name",
            "items",
            "services",
            "attachments",
            "messages",
            "latest_sent_quote",
            "whatsapp_summary",
            "created_at",
            "updated_at",
        ]

    def get_shop_name(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if actor == CLIENT_ACTOR:
            return project_client_counterparty_name(
                fallback_name=obj.shop.name if obj.shop_id else None,
                request_snapshot=getattr(obj, "request_snapshot", None),
            )
        return project_identity(obj.shop.name, actor=actor)

    def get_whatsapp_summary(self, obj):
        from quotes.summary_service import get_quote_request_summary_text
        return get_quote_request_summary_text(obj)

    def get_latest_sent_quote(self, obj):
        sq = obj.get_latest_quote()
        if not sq:
            return None
        return QuoteSummarySerializer(sq, context=self.context).data

    def get_status(self, obj):
        return normalize_quote_request_status(obj.status)

    def get_status_label(self, obj):
        return quote_request_status_label(self.get_status(obj))


# ---------------------------------------------------------------------------
# QuoteRequest — shop
# ---------------------------------------------------------------------------


class QuoteRequestShopListSerializer(serializers.ModelSerializer):
    """Shop: list incoming requests — customer info, delivery preference."""

    shop_name = serializers.CharField(source="shop.name", read_only=True)
    items_count = serializers.SerializerMethodField()
    has_sent_quote = serializers.SerializerMethodField()
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "shop",
            "shop_name",
            "customer_name",
            "customer_email",
            "customer_phone",
            "status",
            "raw_status",
            "status_label",
            "delivery_preference",
            "delivery_address",
            "items_count",
            "has_sent_quote",
            "created_at",
        ]

    def get_items_count(self, obj):
        return obj.items.count()

    def get_has_sent_quote(self, obj):
        return obj.quotes.filter(status__in=[QuoteOfferStatus.SENT, QuoteOfferStatus.REVISED, QuoteOfferStatus.ACCEPTED]).exists()

    def get_status(self, obj):
        return normalize_quote_request_status(obj.status)

    def get_status_label(self, obj):
        return quote_request_status_label(self.get_status(obj))


class QuoteRequestShopDetailSerializer(serializers.ModelSerializer):
    """Shop: full detail for incoming request — items with pricing, services, sent quotes."""

    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)
    calculator_draft_file_id = serializers.IntegerField(read_only=True)
    delivery_location_name = serializers.CharField(source="delivery_location.name", read_only=True)
    items = QuoteItemShopSerializer(many=True, read_only=True)
    services = QuoteRequestServiceReadSerializer(many=True, read_only=True)
    attachments = QuoteRequestAttachmentSerializer(many=True, read_only=True)
    messages = QuoteRequestMessageSerializer(many=True, read_only=True)
    sent_quotes = QuoteSummarySerializer(source="quotes", many=True, read_only=True)
    whatsapp_summary = serializers.SerializerMethodField()
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "shop",
            "shop_name",
            "shop_currency",
            "calculator_draft_file_id",
            "created_by",
            "customer_name",
            "customer_email",
            "customer_phone",
            "customer_inquiry",
            "status",
            "raw_status",
            "status_label",
            "notes",
            "delivery_preference",
            "delivery_address",
            "delivery_location",
            "delivery_location_name",
            "request_snapshot",
            "items",
            "services",
            "attachments",
            "messages",
            "sent_quotes",
            "whatsapp_summary",
            "created_at",
            "updated_at",
        ]

    def get_whatsapp_summary(self, obj):
        from quotes.summary_service import get_quote_request_summary_text
        return get_quote_request_summary_text(obj)

    def get_status(self, obj):
        return normalize_quote_request_status(obj.status)

    def get_status_label(self, obj):
        return quote_request_status_label(self.get_status(obj))


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------


class QuoteCreateSerializer(serializers.ModelSerializer):
    """Shop: create or send quote (quote_request from context)."""

    turnaround_hours = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    status = serializers.ChoiceField(
        choices=[QuoteOfferStatus.PENDING, QuoteOfferStatus.SENT],
        required=False,
        default=QuoteOfferStatus.SENT,
    )
    price_min = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    price_max = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    confirmed_specs = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
    )
    needs_buyer_confirmation = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
    )
    alternative_suggestion = serializers.CharField(required=False, allow_blank=True)
    availability_status = serializers.CharField(required=False, allow_blank=True)
    response_snapshot = serializers.JSONField(required=False)
    revised_pricing_snapshot = serializers.JSONField(required=False)

    class Meta:
        model = Quote
        fields = [
            "status",
            "total",
            "price_min",
            "price_max",
            "note",
            "turnaround_days",
            "turnaround_hours",
            "confirmed_specs",
            "needs_buyer_confirmation",
            "alternative_suggestion",
            "availability_status",
            "response_snapshot",
            "revised_pricing_snapshot",
        ]
        extra_kwargs = {"total": {"required": False, "allow_null": True}}

    def validate_total(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Total must be non-negative.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        turnaround_hours = attrs.get("turnaround_hours")
        turnaround_days = attrs.get("turnaround_days")
        if turnaround_hours is None and turnaround_days:
            attrs["turnaround_hours"] = turnaround_days * 8
        if attrs.get("price_min") is not None and attrs.get("price_min") < 0:
            raise serializers.ValidationError({"price_min": "Price minimum must be non-negative."})
        if attrs.get("price_max") is not None and attrs.get("price_max") < 0:
            raise serializers.ValidationError({"price_max": "Price maximum must be non-negative."})
        if (
            attrs.get("price_min") is not None
            and attrs.get("price_max") is not None
            and attrs["price_min"] > attrs["price_max"]
        ):
            raise serializers.ValidationError({"price_max": "Price maximum must be greater than or equal to price minimum."})
        return attrs

    def _build_response_snapshot(self, quote_request, attrs, *, estimate=None, existing_snapshot=None):
        request_snapshot = quote_request.request_snapshot or {}
        pricing_preview = request_snapshot.get("pricing_preview_snapshot") or {}
        production_preview = request_snapshot.get("production_preview_snapshot") or {}
        selected_shop_preview = request_snapshot.get("selected_shop_preview") or {}
        response_snapshot = dict(existing_snapshot or {})
        response_snapshot.update(attrs.get("response_snapshot") or {})
        response_snapshot.update(
            {
                "shop_name": quote_request.shop.name,
                "shop_slug": quote_request.shop.slug,
                "currency": quote_request.shop.currency or "KES",
                "price": attrs.get("total"),
                "price_min": attrs.get("price_min"),
                "price_max": attrs.get("price_max"),
                "turnaround_label": estimate.label if estimate else response_snapshot.get("turnaround_label", ""),
                "confirmed_specs": attrs.get("confirmed_specs") or [],
                "included_items": attrs.get("confirmed_specs") or [],
                "needs_confirmation": attrs.get("needs_buyer_confirmation") or [],
                "shop_note": attrs.get("note", "") or "",
                "alternative_suggestion": attrs.get("alternative_suggestion", "") or "",
                "availability_status": attrs.get("availability_status", "") or "",
                "estimated_price": pricing_preview.get("total")
                or (pricing_preview.get("totals") or {}).get("grand_total")
                or selected_shop_preview.get("total"),
                "pricing_preview_lines": pricing_preview.get("line_items")
                or pricing_preview.get("lines")
                or [],
                "paper_line": pricing_preview.get("paper_line"),
                "printing_line": pricing_preview.get("printing_line"),
                "finishing_lines": pricing_preview.get("finishing_lines") or [],
                "production_preview": production_preview,
                "missing_specs": request_snapshot.get("needs_confirmation")
                or selected_shop_preview.get("needs_confirmation")
                or [],
            }
        )
        return response_snapshot

    def create(self, validated_data):
        quote_request = self.context["quote_request"]
        request = self.context["request"]
        shop = quote_request.shop
        quote_status = validated_data.pop("status", QuoteOfferStatus.SENT)
        validated_data.pop("price_min", None)
        validated_data.pop("price_max", None)
        validated_data.pop("confirmed_specs", None)
        validated_data.pop("needs_buyer_confirmation", None)
        validated_data.pop("alternative_suggestion", None)
        validated_data.pop("availability_status", None)
        revised_pricing_snapshot = validated_data.pop("revised_pricing_snapshot", None)

        # Revision number: count existing + 1
        rev = quote_request.quotes.count() + 1
        turnaround_hours = validated_data.get("turnaround_hours")
        estimate = None
        if turnaround_hours:
            estimate = estimate_turnaround(shop=shop, working_hours=turnaround_hours)
            if estimate:
                validated_data["turnaround_days"] = legacy_days_from_hours(estimate.working_hours)
                validated_data["estimated_ready_at"] = estimate.ready_at
                validated_data["human_ready_text"] = estimate.human_ready_text
                validated_data["turnaround_label"] = estimate.label
        validated_data["response_snapshot"] = self._build_response_snapshot(
            quote_request,
            self.validated_data,
            estimate=estimate,
        )
        validated_data["response_snapshot"] = json.loads(
            json.dumps(validated_data["response_snapshot"], cls=DjangoJSONEncoder)
        )
        if revised_pricing_snapshot is not None:
            validated_data["revised_pricing_snapshot"] = revised_pricing_snapshot

        return Quote.objects.create(
            quote_request=quote_request,
            shop=shop,
            created_by=request.user,
            status=quote_status,
            revision_number=rev,
            **validated_data,
        )


class QuoteUpdateSerializer(serializers.ModelSerializer):
    """Shop: revise quote (update note, turnaround, total)."""

    turnaround_hours = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    status = serializers.ChoiceField(
        choices=[QuoteOfferStatus.PENDING, QuoteOfferStatus.SENT, QuoteOfferStatus.REVISED],
        required=False,
    )
    price_min = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    price_max = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    confirmed_specs = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
    )
    needs_buyer_confirmation = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
    )
    alternative_suggestion = serializers.CharField(required=False, allow_blank=True)
    availability_status = serializers.CharField(required=False, allow_blank=True)
    response_snapshot = serializers.JSONField(required=False)
    revised_pricing_snapshot = serializers.JSONField(required=False)

    class Meta:
        model = Quote
        fields = [
            "status",
            "note",
            "turnaround_days",
            "turnaround_hours",
            "total",
            "price_min",
            "price_max",
            "confirmed_specs",
            "needs_buyer_confirmation",
            "alternative_suggestion",
            "availability_status",
            "response_snapshot",
            "revised_pricing_snapshot",
        ]

    def validate(self, attrs):
        instance = self.instance
        if instance and instance.status not in (QuoteOfferStatus.PENDING, QuoteOfferStatus.SENT, QuoteOfferStatus.REVISED):
            raise serializers.ValidationError(
                "Only pending, sent, or revised quotes can be updated."
            )
        turnaround_hours = attrs.get("turnaround_hours")
        turnaround_days = attrs.get("turnaround_days")
        if turnaround_hours is None and turnaround_days:
            attrs["turnaround_hours"] = turnaround_days * 8
        if turnaround_hours:
            estimate = estimate_turnaround(shop=instance.shop if instance else None, working_hours=turnaround_hours)
            if estimate:
                attrs["turnaround_days"] = legacy_days_from_hours(estimate.working_hours)
                attrs["estimated_ready_at"] = estimate.ready_at
                attrs["human_ready_text"] = estimate.human_ready_text
                attrs["turnaround_label"] = estimate.label
        if attrs.get("price_min") is not None and attrs.get("price_min") < 0:
            raise serializers.ValidationError({"price_min": "Price minimum must be non-negative."})
        if attrs.get("price_max") is not None and attrs.get("price_max") < 0:
            raise serializers.ValidationError({"price_max": "Price maximum must be non-negative."})
        if (
            attrs.get("price_min") is not None
            and attrs.get("price_max") is not None
            and attrs["price_min"] > attrs["price_max"]
        ):
            raise serializers.ValidationError({"price_max": "Price maximum must be greater than or equal to price minimum."})
        return attrs

    def update(self, instance, validated_data):
        price_min = validated_data.pop("price_min", None) if "price_min" in validated_data else None
        price_max = validated_data.pop("price_max", None) if "price_max" in validated_data else None
        confirmed_specs = validated_data.pop("confirmed_specs", None)
        needs_buyer_confirmation = validated_data.pop("needs_buyer_confirmation", None)
        alternative_suggestion = validated_data.pop("alternative_suggestion", None)
        availability_status = validated_data.pop("availability_status", None)
        custom_snapshot = validated_data.pop("response_snapshot", None) or {}
        revised_pricing_snapshot = validated_data.pop("revised_pricing_snapshot", None)
        explicit_status = validated_data.pop("status", None)

        instance = super().update(instance, validated_data)

        snapshot = dict(instance.response_snapshot or {})
        snapshot.update(custom_snapshot)
        if "total" in self.validated_data:
            snapshot["price"] = self.validated_data.get("total")
        if price_min is not None:
            snapshot["price_min"] = price_min
        if price_max is not None:
            snapshot["price_max"] = price_max
        if confirmed_specs is not None:
            snapshot["confirmed_specs"] = confirmed_specs
            snapshot["included_items"] = confirmed_specs
        if needs_buyer_confirmation is not None:
            snapshot["needs_confirmation"] = needs_buyer_confirmation
        if alternative_suggestion is not None:
            snapshot["alternative_suggestion"] = alternative_suggestion
        if availability_status is not None:
            snapshot["availability_status"] = availability_status
        if "note" in self.validated_data:
            snapshot["shop_note"] = self.validated_data.get("note", "") or ""
        if getattr(instance, "turnaround_label", ""):
            snapshot["turnaround_label"] = instance.turnaround_label
        instance.response_snapshot = snapshot
        if revised_pricing_snapshot is not None:
            instance.revised_pricing_snapshot = revised_pricing_snapshot
        if explicit_status:
            instance.status = explicit_status
        instance.save()
        return instance


class QuoteRequestReplySerializer(serializers.Serializer):
    body = serializers.CharField(required=True, allow_blank=False)


class QuoteRequestRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False)


class QuoteListSerializer(serializers.ModelSerializer):
    """List shop quotes — for quote_request or shop views."""

    quote_request_id = serializers.IntegerField(source="quote_request_id", read_only=True)
    shop_name = serializers.SerializerMethodField()
    customer_name = serializers.CharField(source="quote_request.customer_name", read_only=True)
    estimated_working_hours = serializers.IntegerField(source="turnaround_hours", read_only=True)
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = [
            "id",
            "quote_request_id",
            "shop",
            "shop_name",
            "customer_name",
            "status",
            "raw_status",
            "status_label",
            "total",
            "turnaround_days",
            "turnaround_hours",
            "estimated_working_hours",
            "estimated_ready_at",
            "human_ready_text",
            "turnaround_label",
            "revision_number",
            "sent_at",
            "created_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if resolve_actor(getattr(request, "user", None)) == CLIENT_ACTOR:
            for key in ("shop", "shop_name"):
                data.pop(key, None)
        return data

    def get_shop_name(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if actor == CLIENT_ACTOR:
            return project_client_counterparty_name(
                fallback_name=obj.shop.name if obj.shop_id else None,
                request_snapshot=getattr(obj.quote_request, "request_snapshot", None),
                response_snapshot=obj.response_snapshot,
            )
        return project_identity(obj.shop.name, actor=actor)

    def get_status(self, obj):
        return normalize_quote_response_status(obj.status)

    def get_status_label(self, obj):
        return quote_response_status_label(self.get_status(obj))


class QuoteDetailSerializer(serializers.ModelSerializer):
    """Full shop quote with items — for shop and customer."""

    shop_name = serializers.SerializerMethodField()
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)
    quote_request_summary = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()
    attachments = QuoteAttachmentSerializer(many=True, read_only=True)
    whatsapp_summary = serializers.SerializerMethodField()
    estimated_working_hours = serializers.IntegerField(source="turnaround_hours", read_only=True)
    turnaround_text = serializers.SerializerMethodField()
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = [
            "id",
            "quote_request",
            "quote_request_summary",
            "shop",
            "shop_name",
            "shop_currency",
            "status",
            "raw_status",
            "status_label",
            "total",
            "note",
            "turnaround_days",
            "turnaround_hours",
            "estimated_working_hours",
            "estimated_ready_at",
            "human_ready_text",
            "turnaround_label",
            "turnaround_text",
            "revision_number",
            "pricing_locked_at",
            "sent_at",
            "whatsapp_message",
            "whatsapp_summary",
            "items",
            "attachments",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if resolve_actor(getattr(request, "user", None)) == CLIENT_ACTOR:
            for key in ("shop", "shop_name", "shop_currency"):
                data.pop(key, None)
        return data

    def get_shop_name(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if actor == CLIENT_ACTOR:
            return project_client_counterparty_name(
                fallback_name=obj.shop.name if obj.shop_id else None,
                request_snapshot=getattr(obj.quote_request, "request_snapshot", None),
                response_snapshot=obj.response_snapshot,
            )
        return project_identity(obj.shop.name, actor=actor)

    def get_whatsapp_summary(self, obj):
        from quotes.summary_service import get_quote_summary_text
        return obj.whatsapp_message or get_quote_summary_text(obj)

    def get_quote_request_summary(self, obj):
        qr = obj.quote_request
        normalized_status = normalize_quote_request_status(qr.status)
        return {
            "id": qr.id,
            "customer_name": qr.customer_name,
            "customer_email": qr.customer_email,
            "customer_phone": qr.customer_phone,
            "status": normalized_status,
            "raw_status": qr.status,
            "status_label": quote_request_status_label(normalized_status),
        }

    def get_items(self, obj):
        # Items linked to this shop quote (priced items)
        items = obj.items.select_related("product", "paper", "material", "machine").prefetch_related(
            "finishings__finishing_rate"
        )
        return QuoteItemCustomerSerializer(items, many=True).data

    def get_turnaround_text(self, obj):
        return humanize_working_hours(obj.turnaround_hours)

    def get_status(self, obj):
        return normalize_quote_response_status(obj.status)

    def get_status_label(self, obj):
        return quote_response_status_label(self.get_status(obj))


# ---------------------------------------------------------------------------
# Quote share (public)
# ---------------------------------------------------------------------------


class QuoteShareItemPublicSerializer(serializers.ModelSerializer):
    """Public quote item summary — no internal shop settings."""

    product_name = serializers.SerializerMethodField()
    size_label = serializers.SerializerMethodField()
    finishing_label = serializers.SerializerMethodField()

    class Meta:
        model = QuoteItem
        fields = ["product_name", "title", "quantity", "size_label", "sides", "finishing_label", "line_total"]

    def get_product_name(self, obj):
        if obj.item_type == "PRODUCT" and obj.product_id:
            return obj.product.name
        return obj.title or ""

    def get_size_label(self, obj):
        if obj.pricing_mode == "LARGE_FORMAT" and obj.chosen_width_mm and obj.chosen_height_mm:
            return f"{obj.chosen_width_mm}×{obj.chosen_height_mm}mm"
        if obj.product_id and obj.product:
            w = obj.product.default_finished_width_mm
            h = obj.product.default_finished_height_mm
            if w and h:
                return f"{w}×{h}mm"
        return ""

    def get_finishing_label(self, obj):
        names = [
            qif.finishing_rate.name
            for qif in obj.finishings.select_related("finishing_rate").all()
            if qif.finishing_rate
        ]
        return ", ".join(names) if names else ""


class QuoteSharePublicSerializer(serializers.Serializer):
    """Public quote summary for share link — works with Quote (no private shop settings)."""

    def to_representation(self, instance):
        """Instance is Quote. Build public dict from quote + quote_request."""
        qr = instance.quote_request
        items = instance.items.select_related("product").prefetch_related("finishings__finishing_rate")
        return {
            "id": instance.id,
            "customer_name": qr.customer_name,
            "status": instance.status,
            "total": instance.total,
            "turnaround_days": instance.turnaround_days,
            "turnaround_hours": instance.turnaround_hours,
            "estimated_ready_at": instance.estimated_ready_at,
            "human_ready_text": instance.human_ready_text,
            "turnaround_label": instance.turnaround_label,
            "note": instance.note or "",
            "items": QuoteShareItemPublicSerializer(items, many=True).data,
        }
