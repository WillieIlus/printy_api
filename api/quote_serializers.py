"""
Quote marketplace serializers — customer vs shop separation.

Customer-facing: minimal shop internals, focus on request status and quote response.
Shop-facing: full request details, delivery info, services — everything to respond quickly.
"""
from rest_framework import serializers

from quotes.choices import QuoteStatus, ShopQuoteStatus
from quotes.models import (
    QuoteItem,
    QuoteItemFinishing,
    QuoteRequest,
    QuoteRequestAttachment,
    QuoteRequestService,
    ShopQuote,
    ShopQuoteAttachment,
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
            "unit_price",
            "line_total",
            "finishings",
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
            "unit_price",
            "line_total",
            "pricing_snapshot",
            "pricing_locked_at",
            "needs_review",
            "finishings",
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


class ShopQuoteSummarySerializer(serializers.ModelSerializer):
    """Minimal shop quote for embedding in QuoteRequest responses."""

    class Meta:
        model = ShopQuote
        fields = [
            "id",
            "status",
            "total",
            "turnaround_days",
            "note",
            "revision_number",
            "sent_at",
            "created_at",
        ]


class QuoteRequestServiceReadSerializer(serializers.ModelSerializer):
    """Read-only quote request service (e.g. delivery)."""

    service_rate_name = serializers.CharField(source="service_rate.name", read_only=True)
    service_rate_code = serializers.CharField(source="service_rate.code", read_only=True)

    class Meta:
        model = QuoteRequestService
        fields = ["id", "service_rate", "service_rate_name", "service_rate_code", "is_selected", "distance_km", "price_override"]


class QuoteRequestAttachmentSerializer(serializers.ModelSerializer):
    """Read attachment — id, file URL, name."""

    class Meta:
        model = QuoteRequestAttachment
        fields = ["id", "file", "name", "created_at"]


class QuoteRequestAttachmentUploadSerializer(serializers.ModelSerializer):
    """Upload attachment (multipart/form-data)."""

    class Meta:
        model = QuoteRequestAttachment
        fields = ["file", "name"]
        extra_kwargs = {"file": {"required": True}, "name": {"required": False, "allow_blank": True}}


class ShopQuoteAttachmentSerializer(serializers.ModelSerializer):
    """Read attachment — id, file URL, name."""

    class Meta:
        model = ShopQuoteAttachment
        fields = ["id", "file", "name", "created_at"]


class ShopQuoteAttachmentUploadSerializer(serializers.ModelSerializer):
    """Upload attachment (multipart/form-data)."""

    class Meta:
        model = ShopQuoteAttachment
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

    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_slug = serializers.CharField(source="shop.slug", read_only=True)
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)
    quote_draft_file_id = serializers.IntegerField(read_only=True)
    items_count = serializers.SerializerMethodField()
    latest_sent_quote = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "shop",
            "shop_name",
            "shop_slug",
            "shop_currency",
            "quote_draft_file_id",
            "status",
            "items_count",
            "latest_sent_quote",
            "created_at",
            "updated_at",
        ]

    def get_items_count(self, obj):
        return obj.items.count()

    def get_latest_sent_quote(self, obj):
        sq = obj.get_latest_shop_quote()
        if not sq:
            return None
        return ShopQuoteSummarySerializer(sq).data


class QuoteRequestCustomerDetailSerializer(serializers.ModelSerializer):
    """Customer: detail for own quote request — items + latest sent quote, no pricing breakdown."""

    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_slug = serializers.CharField(source="shop.slug", read_only=True)
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)
    quote_draft_file_id = serializers.IntegerField(read_only=True)
    delivery_location_name = serializers.CharField(source="delivery_location.name", read_only=True)
    items = QuoteItemCustomerSerializer(many=True, read_only=True)
    services = QuoteRequestServiceReadSerializer(many=True, read_only=True)
    attachments = QuoteRequestAttachmentSerializer(many=True, read_only=True)
    latest_sent_quote = serializers.SerializerMethodField()
    whatsapp_summary = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "shop",
            "shop_name",
            "shop_slug",
            "shop_currency",
            "quote_draft_file_id",
            "customer_name",
            "customer_email",
            "customer_phone",
            "status",
            "notes",
            "delivery_preference",
            "delivery_address",
            "delivery_location",
            "delivery_location_name",
            "items",
            "services",
            "attachments",
            "latest_sent_quote",
            "whatsapp_summary",
            "created_at",
            "updated_at",
        ]

    def get_whatsapp_summary(self, obj):
        from quotes.summary_service import get_quote_request_summary_text
        return get_quote_request_summary_text(obj)

    def get_latest_sent_quote(self, obj):
        sq = obj.get_latest_shop_quote()
        if not sq:
            return None
        return ShopQuoteSummarySerializer(sq).data


# ---------------------------------------------------------------------------
# QuoteRequest — shop
# ---------------------------------------------------------------------------


class QuoteRequestShopListSerializer(serializers.ModelSerializer):
    """Shop: list incoming requests — customer info, delivery preference."""

    shop_name = serializers.CharField(source="shop.name", read_only=True)
    items_count = serializers.SerializerMethodField()
    has_sent_quote = serializers.SerializerMethodField()

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
            "delivery_preference",
            "delivery_address",
            "items_count",
            "has_sent_quote",
            "created_at",
        ]

    def get_items_count(self, obj):
        return obj.items.count()

    def get_has_sent_quote(self, obj):
        return obj.shop_quotes.filter(status__in=[ShopQuoteStatus.SENT, ShopQuoteStatus.ACCEPTED]).exists()


class QuoteRequestShopDetailSerializer(serializers.ModelSerializer):
    """Shop: full detail for incoming request — items with pricing, services, sent quotes."""

    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)
    delivery_location_name = serializers.CharField(source="delivery_location.name", read_only=True)
    items = QuoteItemShopSerializer(many=True, read_only=True)
    services = QuoteRequestServiceReadSerializer(many=True, read_only=True)
    attachments = QuoteRequestAttachmentSerializer(many=True, read_only=True)
    sent_quotes = ShopQuoteSummarySerializer(source="shop_quotes", many=True, read_only=True)
    whatsapp_summary = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "shop",
            "shop_name",
            "shop_currency",
            "created_by",
            "customer_name",
            "customer_email",
            "customer_phone",
            "customer_inquiry",
            "status",
            "notes",
            "delivery_preference",
            "delivery_address",
            "delivery_location",
            "delivery_location_name",
            "items",
            "services",
            "attachments",
            "sent_quotes",
            "whatsapp_summary",
            "created_at",
            "updated_at",
        ]

    def get_whatsapp_summary(self, obj):
        from quotes.summary_service import get_quote_request_summary_text
        return get_quote_request_summary_text(obj)


# ---------------------------------------------------------------------------
# ShopQuote
# ---------------------------------------------------------------------------


class ShopQuoteCreateSerializer(serializers.ModelSerializer):
    """Shop: create or send quote (quote_request from context)."""

    class Meta:
        model = ShopQuote
        fields = [
            "total",
            "note",
            "turnaround_days",
        ]
        extra_kwargs = {"total": {"required": False, "allow_null": True}}

    def validate_total(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Total must be non-negative.")
        return value

    def create(self, validated_data):
        quote_request = self.context["quote_request"]
        request = self.context["request"]
        shop = quote_request.shop

        # Revision number: count existing + 1
        rev = quote_request.shop_quotes.count() + 1

        return ShopQuote.objects.create(
            quote_request=quote_request,
            shop=shop,
            created_by=request.user,
            revision_number=rev,
            **validated_data,
        )


class ShopQuoteUpdateSerializer(serializers.ModelSerializer):
    """Shop: revise quote (update note, turnaround, total)."""

    class Meta:
        model = ShopQuote
        fields = [
            "note",
            "turnaround_days",
            "total",
        ]

    def validate(self, attrs):
        instance = self.instance
        if instance and instance.status not in (ShopQuoteStatus.SENT, ShopQuoteStatus.REVISED):
            raise serializers.ValidationError(
                "Only sent or revised quotes can be updated."
            )
        return attrs


class ShopQuoteListSerializer(serializers.ModelSerializer):
    """List shop quotes — for quote_request or shop views."""

    quote_request_id = serializers.IntegerField(source="quote_request_id", read_only=True)
    shop_name = serializers.CharField(source="shop.name", read_only=True)
    customer_name = serializers.CharField(source="quote_request.customer_name", read_only=True)

    class Meta:
        model = ShopQuote
        fields = [
            "id",
            "quote_request_id",
            "shop",
            "shop_name",
            "customer_name",
            "status",
            "total",
            "turnaround_days",
            "revision_number",
            "sent_at",
            "created_at",
        ]


class ShopQuoteDetailSerializer(serializers.ModelSerializer):
    """Full shop quote with items — for shop and customer."""

    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)
    quote_request_summary = serializers.SerializerMethodField()
    items = serializers.SerializerMethodField()
    attachments = ShopQuoteAttachmentSerializer(many=True, read_only=True)
    whatsapp_summary = serializers.SerializerMethodField()

    class Meta:
        model = ShopQuote
        fields = [
            "id",
            "quote_request",
            "quote_request_summary",
            "shop",
            "shop_name",
            "shop_currency",
            "status",
            "total",
            "note",
            "turnaround_days",
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

    def get_whatsapp_summary(self, obj):
        from quotes.summary_service import get_shop_quote_summary_text
        return obj.whatsapp_message or get_shop_quote_summary_text(obj)

    def get_quote_request_summary(self, obj):
        qr = obj.quote_request
        return {
            "id": qr.id,
            "customer_name": qr.customer_name,
            "customer_email": qr.customer_email,
            "customer_phone": qr.customer_phone,
            "status": qr.status,
        }

    def get_items(self, obj):
        # Items linked to this shop quote (priced items)
        items = obj.items.select_related("product", "paper", "material", "machine").prefetch_related(
            "finishings__finishing_rate"
        )
        return QuoteItemCustomerSerializer(items, many=True).data


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
    """Public quote summary for share link — works with ShopQuote (no private shop settings)."""

    def to_representation(self, instance):
        """Instance is ShopQuote. Build public dict from shop_quote + quote_request."""
        qr = instance.quote_request
        items = instance.items.select_related("product").prefetch_related("finishings__finishing_rate")
        return {
            "id": instance.id,
            "shop_name": instance.shop.name,
            "customer_name": qr.customer_name,
            "status": instance.status,
            "total": instance.total,
            "turnaround_days": instance.turnaround_days,
            "note": instance.note or "",
            "items": QuoteShareItemPublicSerializer(items, many=True).data,
        }
