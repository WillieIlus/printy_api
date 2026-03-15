"""
API serializers with strong validation of shop consistency.
All nested resources (products, papers, machines, materials, finishing_rates)
must belong to the same shop.
"""
import logging
from decimal import Decimal

from rest_framework import serializers

logger = logging.getLogger(__name__)

from accounts.models import User
from catalog.choices import PricingMode
from catalog.models import Product, ProductFinishingOption, ProductImage
from inventory.models import Machine, Paper, ProductionPaperSize
from pricing.choices import ColorMode, Sides
from pricing.models import FinishingCategory, FinishingRate, Material, PrintingRate, VolumeDiscount
from quotes.choices import QuoteStatus
from quotes.models import CustomerInquiry, QuoteItem, QuoteItemFinishing, QuoteRequest
from shops.models import FavoriteShop, OpeningHours, Shop, ShopRating

from .validators import validate_shop_consistency


def get_shop_status(shop):
    """
    Compute shop status: 'opening' | 'closing_soon' | 'closed'.
    Uses shop's timezone-naive times; assumes local timezone.
    """
    from datetime import datetime

    from django.utils import timezone

    now = timezone.localtime(timezone.now())
    weekday = now.isoweekday()  # 1=Mon .. 7=Sun
    try:
        hours = OpeningHours.objects.get(shop=shop, weekday=weekday)
    except OpeningHours.DoesNotExist:
        return "closed"

    if hours.is_closed:
        return "closed"

    from_hour = hours.from_hour or "08:00"
    to_hour = hours.to_hour or "18:00"
    try:
        open_h = datetime.strptime(from_hour, "%H:%M").time()
        close_h = datetime.strptime(to_hour, "%H:%M").time()
    except (ValueError, TypeError):
        return "closed"

    now_time = now.time()
    if now_time < open_h or now_time > close_h:
        return "closed"

    delta = datetime.combine(now.date(), close_h) - datetime.combine(now.date(), now_time)
    minutes_before_close = int(delta.total_seconds() // 60)
    if minutes_before_close <= getattr(shop, "closing_soon_minutes", 30):
        return "closing_soon"
    return "opening"


# ---------------------------------------------------------------------------
# Public / Read-only serializers
# ---------------------------------------------------------------------------


class OpeningHoursSerializer(serializers.ModelSerializer):
    """Opening hours per weekday. Help text from model = single source of truth."""

    weekday_display = serializers.SerializerMethodField()

    class Meta:
        model = OpeningHours
        fields = ["id", "weekday", "weekday_display", "from_hour", "to_hour", "is_closed"]
        read_only_fields = ["weekday_display"]

    def get_weekday_display(self, obj):
        return obj.get_weekday_display()


class OpeningHoursBulkInputSerializer(serializers.Serializer):
    """Single item for bulk hours update. Help text from model."""

    id = serializers.IntegerField(required=False, allow_null=True)
    weekday = serializers.IntegerField(min_value=1, max_value=7)
    from_hour = serializers.CharField(required=False, allow_blank=True, default="08:00")
    to_hour = serializers.CharField(required=False, allow_blank=True, default="18:00")
    is_closed = serializers.BooleanField(default=False)


class OpeningHoursBulkSerializer(serializers.Serializer):
    """Bulk update opening hours. { hours: [{ weekday, from_hour, to_hour, is_closed }, ...] }."""

    hours = OpeningHoursBulkInputSerializer(many=True)


class PublicShopListSerializer(serializers.ModelSerializer):
    """List active shops (public)."""

    opening_hours = OpeningHoursSerializer(many=True, read_only=True)
    status = serializers.SerializerMethodField()
    description = serializers.CharField(read_only=True)

    class Meta:
        model = Shop
        fields = [
            "id",
            "name",
            "slug",
            "currency",
            "description",
            "latitude",
            "longitude",
            "opening_hours",
            "status",
            "opening_time",
            "closing_time",
            "closing_soon_minutes",
        ]
        read_only_fields = ["slug"]

    def get_status(self, obj):
        return get_shop_status(obj)


class MatchShopsInputSerializer(serializers.Serializer):
    """Input for POST /api/public/match-shops/."""

    pricing_mode = serializers.ChoiceField(choices=["SHEET", "LARGE_FORMAT"], default="SHEET")
    finished_width_mm = serializers.IntegerField(default=0, min_value=0)
    finished_height_mm = serializers.IntegerField(default=0, min_value=0)
    quantity = serializers.IntegerField(default=100, min_value=1)
    sides = serializers.ChoiceField(choices=[("SIMPLEX", "Simplex"), ("DUPLEX", "Duplex")], default="SIMPLEX")
    color_mode = serializers.ChoiceField(choices=[("BW", "B&W"), ("COLOR", "Color")], default="COLOR")
    sheet_size = serializers.CharField(required=False, allow_blank=True, default="SRA3")
    paper_gsm = serializers.IntegerField(required=False, allow_null=True)
    paper_type = serializers.CharField(required=False, allow_blank=True, default="")
    finishing_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        default=list,
    )
    lat = serializers.FloatField(required=False, allow_null=True)
    lng = serializers.FloatField(required=False, allow_null=True)
    radius_km = serializers.FloatField(default=50, min_value=0.1, max_value=500)


class MatchShopsResultSerializer(serializers.Serializer):
    """Single shop match result."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    slug = serializers.CharField()
    can_calculate = serializers.BooleanField()
    reason = serializers.CharField()
    missing_fields = serializers.ListField(child=serializers.CharField())


class MatchShopsResponseSerializer(serializers.Serializer):
    """Response for POST /api/public/match-shops/."""

    shops = MatchShopsResultSerializer(many=True)
    total = serializers.IntegerField()


class FavoriteShopSerializer(serializers.ModelSerializer):
    """Favorite shop (buyer) - returns shop info."""

    shop = PublicShopListSerializer(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = FavoriteShop
        fields = ["id", "shop", "created_at"]


class FavoriteShopCreateSerializer(serializers.ModelSerializer):
    """Add favorite - accepts shop id."""

    class Meta:
        model = FavoriteShop
        fields = ["shop"]

    def validate_shop(self, value):
        if not value or not value.is_active:
            raise serializers.ValidationError("Shop must be active.")
        return value


class ShopRatingSerializer(serializers.ModelSerializer):
    """Create/update shop rating (buyer)."""

    class Meta:
        model = ShopRating
        fields = ["stars", "comment"]

    def validate_stars(self, value):
        if value is None or value < 1 or value > 5:
            raise serializers.ValidationError("Stars must be between 1 and 5.")
        return value


class ShopRatingSummarySerializer(serializers.Serializer):
    """Rating summary for public shop pages."""

    average = serializers.FloatField()
    count = serializers.IntegerField()


class FinishingOptionSerializer(serializers.ModelSerializer):
    """Finishing option for a product (read-only for catalog)."""

    finishing_rate_name = serializers.CharField(source="finishing_rate.name", read_only=True)
    charge_unit = serializers.CharField(source="finishing_rate.charge_unit", read_only=True)
    price = serializers.DecimalField(
        source="finishing_rate.price", max_digits=12, decimal_places=2, read_only=True
    )

    class Meta:
        model = ProductFinishingOption
        fields = ["id", "finishing_rate", "finishing_rate_name", "charge_unit", "price", "is_default", "price_adjustment"]


class ProductImageSerializer(serializers.ModelSerializer):
    """Product image for catalog. Returns image path for frontend getMediaUrl."""

    image = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["id", "image", "is_primary", "display_order"]

    def get_image(self, obj):
        """Return path relative to MEDIA_ROOT for frontend."""
        if obj.image:
            return obj.image.name
        return None


class CatalogProductSerializer(serializers.ModelSerializer):
    """Product with allowed finishing options, price hint, and gallery breakdown for public catalog."""

    finishing_options = FinishingOptionSerializer(many=True, read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    primary_image = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    default_sides = serializers.CharField()
    pricing_mode = serializers.CharField()
    price_hint = serializers.SerializerMethodField()
    price_range_est = serializers.SerializerMethodField()
    imposition_summary = serializers.SerializerMethodField()
    default_size_label = serializers.SerializerMethodField()
    printing_total = serializers.SerializerMethodField()
    finishing_summary = serializers.SerializerMethodField()
    final_size = serializers.SerializerMethodField()
    is_owner = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "slug",
            "name",
            "description",
            "category",
            "pricing_mode",
            "default_finished_width_mm",
            "default_finished_height_mm",
            "default_bleed_mm",
            "default_sides",
            "min_quantity",
            "finishing_options",
            "images",
            "primary_image",
            "price_hint",
            "price_range_est",
            "imposition_summary",
            "default_size_label",
            "printing_total",
            "finishing_summary",
            "final_size",
            "is_owner",
        ]

    def get_is_owner(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return False
        shop = self.context.get("shop") or getattr(obj, "shop", None)
        if not shop:
            return False
        owner_id = getattr(shop, "owner_id", None)
        return owner_id == request.user.id

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # When shop is in context (catalog view), add shop to product for edit URL
        shop = self.context.get("shop")
        if shop and "shop" not in data:
            data["shop"] = PublicShopListSerializer(shop).data
        return data

    def get_primary_image(self, obj):
        """Path of primary or first image for card display (frontend prepends mediaBase)."""
        img = obj.get_primary_image()
        if img and img.image:
            return img.image.name
        return None

    def get_category(self, obj):
        """Category name for display (frontend expects string, not FK id)."""
        return obj.category.name if obj.category else None

    def get_price_hint(self, obj):
        from catalog.services import product_price_hint

        return product_price_hint(obj)

    def get_price_range_est(self, obj):
        from catalog.services import compute_product_price_range_est

        return compute_product_price_range_est(obj)

    def get_imposition_summary(self, obj):
        """e.g. 'Fits on SRA3: 10-up' for SHEET products."""
        if obj.pricing_mode != "SHEET":
            return None
        try:
            from inventory.choices import SHEET_SIZE_DIMENSIONS
            sheet_size = (obj.default_sheet_size or "").strip() or "SRA3"
            dims = SHEET_SIZE_DIMENSIONS.get(sheet_size)
            if dims:
                cps = obj.get_copies_per_sheet(sheet_size, dims[0], dims[1])
                return f"{sheet_size}: {cps}-up"
        except Exception:
            pass
        return None

    def get_default_size_label(self, obj):
        """e.g. 'SRA3' or 'Large Format'."""
        if obj.pricing_mode == "LARGE_FORMAT":
            return "Large Format"
        return (obj.default_sheet_size or "").strip() or "SRA3"

    def get_printing_total(self, obj):
        """Computed printing total at min_quantity for display."""
        try:
            hint = self.get_price_hint(obj)
            if hint and hint.get("can_calculate") and hint.get("min_price") is not None:
                return hint["min_price"]
        except Exception:
            pass
        return None

    def get_finishing_summary(self, obj):
        """Short list of finishing labels, e.g. ['Lamination', 'Cutting']."""
        try:
            options = obj.finishing_options.select_related("finishing_rate").all()
            return [opt.finishing_rate.name for opt in options if opt.finishing_rate.is_active]
        except Exception:
            return []

    def get_final_size(self, obj):
        """Final product size string, e.g. '90×50mm' or '6000×3000mm'."""
        w = obj.default_finished_width_mm
        h = obj.default_finished_height_mm
        if w and h:
            return f"{w}×{h}mm"
        return None


class CatalogProductWithShopSerializer(CatalogProductSerializer):
    """Product with shop info for all-products gallery (bypasses shop selection)."""

    shop = PublicShopListSerializer(read_only=True)

    class Meta(CatalogProductSerializer.Meta):
        fields = CatalogProductSerializer.Meta.fields + ["shop"]


# ---------------------------------------------------------------------------
# Quote request serializers (buyer)
# ---------------------------------------------------------------------------


class QuoteItemFinishingWriteSerializer(serializers.ModelSerializer):
    """Write serializer for quote item finishing (validates shop consistency)."""

    class Meta:
        model = QuoteItemFinishing
        fields = ["finishing_rate", "coverage_qty", "price_override", "apply_to_sides"]

    def validate_finishing_rate(self, value):
        quote_item = self.context.get("quote_item")
        if quote_item and value:
            validate_shop_consistency(
                quote_item.quote_request.shop,
                finishing_rate=value,
                field_name="finishing_rate",
            )
        return value


class QuoteItemWriteSerializer(serializers.ModelSerializer):
    """Write serializer for quote items (PRODUCT + CUSTOM, validates shop consistency)."""

    finishings = QuoteItemFinishingWriteSerializer(many=True, required=False)
    item_spec_snapshot = serializers.JSONField(required=False, allow_null=True)

    class Meta:
        model = QuoteItem
        fields = [
            "item_type",
            "product",
            "title",
            "spec_text",
            "has_artwork",
            "quantity",
            "pricing_mode",
            "paper",
            "material",
            "chosen_width_mm",
            "chosen_height_mm",
            "sides",
            "color_mode",
            "machine",
            "special_instructions",
            "finishings",
            "item_spec_snapshot",
        ]

    def validate(self, attrs):
        quote_request = self.context.get("quote_request")
        if not quote_request:
            return attrs

        shop = quote_request.shop
        item_type = attrs.get("item_type") or getattr(self.instance, "item_type", "PRODUCT")

        # Auto-bump quantity to product's min_quantity
        if item_type == "PRODUCT":
            product = attrs.get("product") or (self.instance.product if self.instance else None)
            if product:
                min_qty = getattr(product, "min_quantity", 1) or 1
                qty = attrs.get("quantity")
                if qty is not None and qty < min_qty:
                    attrs["quantity"] = min_qty

        # PRODUCT: product required
        if item_type == "PRODUCT":
            product = attrs.get("product") or (self.instance.product if self.instance else None)
            if not product:
                raise serializers.ValidationError({"product": "Product is required for PRODUCT items."})
        # CUSTOM: title or spec_text required
        elif item_type == "CUSTOM":
            title = attrs.get("title", getattr(self.instance, "title", "") if self.instance else "")
            spec_text = attrs.get("spec_text", getattr(self.instance, "spec_text", "") if self.instance else "")
            if not title and not spec_text:
                raise serializers.ValidationError(
                    {"title": "Title or spec_text is required for CUSTOM items."}
                )

        # Pricing mode validation
        pricing_mode = attrs.get("pricing_mode") or getattr(self.instance, "pricing_mode", None)
        if pricing_mode == "SHEET" and attrs.get("paper") is None and (
            not self.instance or not self.instance.paper_id
        ):
            pass  # Optional at create; can be set later
        if pricing_mode == "LARGE_FORMAT":
            m = attrs.get("material") or (self.instance.material if self.instance else None)
            cw = attrs.get("chosen_width_mm") or (self.instance.chosen_width_mm if self.instance else None)
            ch = attrs.get("chosen_height_mm") or (self.instance.chosen_height_mm if self.instance else None)
            if not m or not cw or not ch:
                pass  # Best-effort; preview will mark needs_review

        validate_shop_consistency(
            shop,
            product=attrs.get("product"),
            paper=attrs.get("paper"),
            material=attrs.get("material"),
            machine=attrs.get("machine"),
        )
        return attrs

    def create(self, validated_data):
        from django.db import transaction
        from quotes.pricing_service import compute_and_store_pricing

        finishings_data = validated_data.pop("finishings", [])
        item_spec_snapshot = validated_data.pop("item_spec_snapshot", None)
        quote_request = self.context["quote_request"]
        item_type = validated_data.get("item_type", "PRODUCT")
        product = validated_data.get("product")
        material = validated_data.get("material")

        # Default pricing_mode: from product (PRODUCT) or SHEET/LARGE_FORMAT (CUSTOM)
        if not validated_data.get("pricing_mode"):
            if item_type == "PRODUCT" and product:
                validated_data["pricing_mode"] = product.pricing_mode or "SHEET"
            elif item_type == "CUSTOM":
                validated_data["pricing_mode"] = "LARGE_FORMAT" if material else "SHEET"

        with transaction.atomic():
            item = QuoteItem.objects.create(
                quote_request=quote_request,
                item_spec_snapshot=item_spec_snapshot,
                **validated_data,
            )
            for fd in finishings_data:
                QuoteItemFinishing.objects.create(quote_item=item, **fd)
            try:
                compute_and_store_pricing(item)
            except Exception:
                item.needs_review = True
                item.save(update_fields=["needs_review"])
        return item

    def update(self, instance, validated_data):
        from quotes.pricing_service import compute_and_store_pricing

        finishings_data = validated_data.pop("finishings", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if finishings_data is not None:
            instance.finishings.all().delete()
            for fd in finishings_data:
                QuoteItemFinishing.objects.create(quote_item=instance, **fd)
        try:
            compute_and_store_pricing(instance)
        except Exception:
            instance.needs_review = True
            instance.save(update_fields=["needs_review"])
        return instance


class QuoteItemReadSerializer(serializers.ModelSerializer):
    """Read serializer for quote items (PRODUCT + CUSTOM)."""

    product_name = serializers.SerializerMethodField()
    product_slug = serializers.SerializerMethodField()
    finishings = QuoteItemFinishingWriteSerializer(many=True, read_only=True)

    class Meta:
        model = QuoteItem
        fields = [
            "id",
            "item_type",
            "product",
            "product_name",
            "product_slug",
            "title",
            "spec_text",
            "has_artwork",
            "quantity",
            "pricing_mode",
            "paper",
            "material",
            "chosen_width_mm",
            "chosen_height_mm",
            "sides",
            "color_mode",
            "machine",
            "special_instructions",
            "unit_price",
            "line_total",
            "finishings",
        ]

    def get_product_name(self, obj):
        if obj.item_type == "PRODUCT" and obj.product_id:
            return obj.product.name
        return obj.title or ""

    def get_product_slug(self, obj):
        if obj.item_type == "PRODUCT" and obj.product_id and obj.product:
            return getattr(obj.product, "slug", None) or ""
        return ""


class QuoteRequestCreateSerializer(serializers.ModelSerializer):
    """Create draft quote request (buyer)."""

    class Meta:
        model = QuoteRequest
        fields = ["shop", "customer_name", "customer_email", "customer_phone", "notes"]

    def validate_shop(self, value):
        if not value or not value.is_active:
            raise serializers.ValidationError("Shop must be active.")
        return value

    def create(self, validated_data):
        validated_data["created_by"] = self.context["request"].user
        validated_data["status"] = QuoteStatus.DRAFT
        return super().create(validated_data)


class QuoteRequestReadSerializer(serializers.ModelSerializer):
    """Read quote request with items."""

    items = QuoteItemReadSerializer(many=True, read_only=True)
    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "shop",
            "shop_name",
            "shop_currency",
            "customer_name",
            "customer_email",
            "customer_phone",
            "status",
            "notes",
            "totals",
            "created_at",
            "updated_at",
            "items",
        ]


class QuoteRequestPatchSerializer(serializers.ModelSerializer):
    """Partial update for draft (auto-save)."""

    class Meta:
        model = QuoteRequest
        fields = ["customer_name", "customer_email", "customer_phone", "notes"]


# ---------------------------------------------------------------------------
# Staff quoting API (/api/quotes/) — staff-only, full control
# ---------------------------------------------------------------------------


class QuoteItemWithBreakdownSerializer(serializers.ModelSerializer):
    """Read serializer for quote items including pricing_snapshot (breakdown)."""

    product_name = serializers.SerializerMethodField()
    finishings = QuoteItemFinishingWriteSerializer(many=True, read_only=True)

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
            "finishings",
        ]

    def get_product_name(self, obj):
        if obj.item_type == "PRODUCT" and obj.product_id:
            return obj.product.name
        return obj.title or ""


class QuoteCreateSerializer(serializers.ModelSerializer):
    """Staff: create quote draft."""

    class Meta:
        model = QuoteRequest
        fields = ["shop", "customer_name", "customer_email", "customer_phone", "notes", "customer_inquiry"]

    def validate_shop(self, value):
        if not value or not value.is_active:
            raise serializers.ValidationError("Shop must be active.")
        return value

    def create(self, validated_data):
        validated_data["created_by"] = self.context["request"].user
        validated_data["status"] = QuoteStatus.DRAFT
        return super().create(validated_data)


class QuoteShareItemPublicSerializer(serializers.Serializer):
    """Public quote item summary — no internal shop settings."""

    product_name = serializers.SerializerMethodField()
    title = serializers.CharField(allow_blank=True)
    quantity = serializers.IntegerField()
    size_label = serializers.SerializerMethodField()
    sides = serializers.CharField(allow_blank=True, required=False)
    finishing_label = serializers.SerializerMethodField()
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)

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
    """Public quote summary for share link — no private shop settings."""

    id = serializers.IntegerField()
    shop_name = serializers.CharField(source="shop.name")
    customer_name = serializers.CharField()
    status = serializers.CharField()
    total = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    items = QuoteShareItemPublicSerializer(many=True)

    class Meta:
        fields = ["id", "shop_name", "customer_name", "status", "total", "items"]


class QuoteDetailSerializer(serializers.ModelSerializer):
    """Staff: full quote detail with items and pricing breakdown."""

    items = QuoteItemWithBreakdownSerializer(many=True, read_only=True)
    shop_name = serializers.CharField(source="shop.name", read_only=True)
    shop_slug = serializers.CharField(source="shop.slug", read_only=True)

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "shop",
            "shop_name",
            "shop_slug",
            "created_by",
            "customer_name",
            "customer_email",
            "customer_phone",
            "customer_inquiry",
            "status",
            "notes",
            "total",
            "pricing_locked_at",
            "whatsapp_message",
            "sent_at",
            "created_at",
            "updated_at",
            "items",
        ]


class QuoteItemAddSerializer(QuoteItemWriteSerializer):
    """
    Staff: add/update quote item with calculator input.
    On create/update, computes and stores pricing snapshot in a transaction.
    """

    class Meta(QuoteItemWriteSerializer.Meta):
        pass

    def create(self, validated_data):
        from django.db import transaction
        from quotes.pricing_service import compute_and_store_pricing

        finishings_data = validated_data.pop("finishings", [])
        quote_request = self.context["quote_request"]

        with transaction.atomic():
            item = QuoteItem.objects.create(quote_request=quote_request, **validated_data)
            for fd in finishings_data:
                QuoteItemFinishing.objects.create(quote_item=item, **fd)
            compute_and_store_pricing(item)
        return item

    def update(self, instance, validated_data):
        from django.db import transaction
        from quotes.pricing_service import compute_and_store_pricing

        finishings_data = validated_data.pop("finishings", None)
        with transaction.atomic():
            for attr, value in validated_data.items():
                setattr(instance, attr, value)
            instance.save()
            if finishings_data is not None:
                instance.finishings.all().delete()
                for fd in finishings_data:
                    QuoteItemFinishing.objects.create(quote_item=instance, **fd)
            compute_and_store_pricing(instance)
        return instance


# ---------------------------------------------------------------------------
# Seller serializers (shop-scoped with consistency validation)
# ---------------------------------------------------------------------------


class ShopSerializer(serializers.ModelSerializer):
    """CRUD for seller's own shop. Owner set by view on create."""

    class Meta:
        model = Shop
        fields = [
            "id",
            "name",
            "slug",
            "currency",
            "is_active",
            "owner",
            "description",
            "business_email",
            "phone_number",
            "address_line",
            "city",
            "state",
            "country",
            "zip_code",
            "latitude",
            "longitude",
            "google_place_id",
            "opening_time",
            "closing_time",
            "closing_soon_minutes",
        ]
        read_only_fields = ["slug", "owner"]
        extra_kwargs = {
            # Coerce null to "" — model uses blank=True, default="" but no null=True
            "description": {"allow_null": True, "default": ""},
            "business_email": {"allow_null": True, "default": ""},
            "phone_number": {"allow_null": True, "default": ""},
            "address_line": {"allow_null": True, "default": ""},
            "city": {"allow_null": True, "default": ""},
            "state": {"allow_null": True, "default": ""},
            "country": {"allow_null": True, "default": ""},
            "zip_code": {"allow_null": True, "default": ""},
            "google_place_id": {"allow_null": True, "default": ""},
        }

    def validate_description(self, value):
        return value or ""

    def validate_business_email(self, value):
        return value or ""

    def validate_phone_number(self, value):
        return value or ""

    def validate_address_line(self, value):
        return value or ""

    def validate_city(self, value):
        return value or ""

    def validate_state(self, value):
        return value or ""

    def validate_country(self, value):
        return value or ""

    def validate_zip_code(self, value):
        return value or ""


class MachineSerializer(serializers.ModelSerializer):
    """CRUD for shop machines."""

    class Meta:
        model = Machine
        fields = [
            "id",
            "name",
            "machine_type",
            "max_width_mm",
            "max_height_mm",
            "min_gsm",
            "max_gsm",
            "is_active",
        ]

    def validate(self, attrs):
        shop = self.context.get("shop")
        if shop:
            # On create, shop comes from URL; on update, instance already has shop
            pass
        return attrs


class ProductionPaperSizeSerializer(serializers.ModelSerializer):
    """Read-only production size for imposition."""

    class Meta:
        model = ProductionPaperSize
        fields = ["id", "code", "name", "width_mm", "height_mm"]


class PaperSerializer(serializers.ModelSerializer):
    """CRUD for shop papers."""

    production_size_detail = ProductionPaperSizeSerializer(source="production_size", read_only=True)

    class Meta:
        model = Paper
        fields = [
            "id",
            "production_size",
            "production_size_detail",
            "sheet_size",
            "gsm",
            "paper_type",
            "width_mm",
            "height_mm",
            "buying_price",
            "selling_price",
            "quantity_in_stock",
            "reorder_level",
            "is_active",
            "is_default",
        ]


class PrintingRateSerializer(serializers.ModelSerializer):
    """CRUD for machine printing rates (single_price=simplex, double_price=duplex per sheet)."""

    class Meta:
        model = PrintingRate
        fields = [
            "id",
            "sheet_size",
            "color_mode",
            "single_price",
            "double_price",
            "is_active",
            "is_default",
        ]

    def validate(self, attrs):
        machine = self.context.get("machine")
        if machine and self.instance is None:
            # Ensure machine belongs to shop when creating
            pass
        return attrs


class FinishingCategorySerializer(serializers.ModelSerializer):
    """Read/write for finishing categories."""

    class Meta:
        model = FinishingCategory
        fields = ["id", "name", "slug", "description"]
        read_only_fields = ["slug"]


class FinishingRateSerializer(serializers.ModelSerializer):
    """CRUD for shop finishing rates."""

    category_detail = FinishingCategorySerializer(source="category", read_only=True)

    class Meta:
        model = FinishingRate
        fields = [
            "id",
            "name",
            "category",
            "category_detail",
            "charge_unit",
            "price",
            "double_side_price",
            "setup_fee",
            "min_qty",
            "is_active",
        ]


class VolumeDiscountSerializer(serializers.ModelSerializer):
    """CRUD for shop volume discounts."""

    discount_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, coerce_to_string=True
    )

    class Meta:
        model = VolumeDiscount
        fields = ["id", "name", "min_quantity", "discount_percent", "is_active"]


class MaterialSerializer(serializers.ModelSerializer):
    """CRUD for shop materials."""

    production_size_detail = ProductionPaperSizeSerializer(source="production_size", read_only=True)

    class Meta:
        model = Material
        fields = [
            "id",
            "material_type",
            "production_size",
            "production_size_detail",
            "unit",
            "buying_price",
            "selling_price",
            "is_active",
        ]


class ProductImageUploadSerializer(serializers.ModelSerializer):
    """Upload a product image (multipart/form-data)."""

    class Meta:
        model = ProductImage
        fields = ["id", "image", "is_primary", "display_order"]
        extra_kwargs = {
            "image": {"required": True},
            "is_primary": {"required": False},
            "display_order": {"required": False},
        }


class ProductFinishingOptionWriteSerializer(serializers.ModelSerializer):
    """Write serializer for product finishing options."""

    class Meta:
        model = ProductFinishingOption
        fields = ["finishing_rate", "is_default", "price_adjustment"]

    def validate(self, attrs):
        product = self.context.get("product")
        if product and attrs.get("finishing_rate"):
            validate_shop_consistency(
                product.shop,
                finishing_rate=attrs["finishing_rate"],
                field_name="finishing_rate",
            )
        return attrs


class ProductWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for product create/update.
    Enforces publish rules: status can only be PUBLISHED when shop has pricing.
    """

    finishing_options = ProductFinishingOptionWriteSerializer(many=True, required=False)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "category",
            "pricing_mode",
            "default_finished_width_mm",
            "default_finished_height_mm",
            "default_sheet_size",
            "default_bleed_mm",
            "default_sides",
            "default_machine",
            "min_quantity",
            "min_width_mm",
            "min_height_mm",
            "max_width_mm",
            "max_height_mm",
            "min_gsm",
            "max_gsm",
            "allowed_sheet_sizes",
            "allow_simplex",
            "allow_duplex",
            "is_active",
            "status",
            "finishing_options",
        ]
        extra_kwargs = {
            "id": {"read_only": True},
            "name": {"required": True, "allow_blank": False},
            "description": {"required": False, "allow_blank": True},
            "category": {"required": False, "allow_null": True},
            "default_bleed_mm": {"required": False},
            "default_sides": {"required": False},
            "min_quantity": {"required": False, "min_value": 1},
            "min_width_mm": {"required": False},
            "min_height_mm": {"required": False},
            "max_width_mm": {"required": False},
            "max_height_mm": {"required": False},
            "min_gsm": {"required": False},
            "max_gsm": {"required": False},
            "allowed_sheet_sizes": {"required": False},
            "allow_simplex": {"required": False},
            "allow_duplex": {"required": False},
            "status": {"required": False},
        }

    def validate_status(self, value):
        from setup.services import get_product_publish_check
        if value == "PUBLISHED":
            shop = self.context.get("shop")
            instance = self.instance
            product_for_check = instance or Product(shop=shop)
            if shop:
                product_for_check.shop = shop
            check = get_product_publish_check(product_for_check)
            if not check["can_publish"]:
                raise serializers.ValidationError(
                    "Cannot publish: " + " ".join(check["block_reasons"])
                )
        return value

    def create(self, validated_data):
        finishings_data = validated_data.pop("finishing_options", [])
        shop = validated_data.pop("shop", None) or self.context.get("shop")
        validated_data.setdefault("description", "")
        validated_data.setdefault("category", None)
        validated_data.setdefault("min_quantity", 1)
        validated_data.setdefault("default_bleed_mm", 3)
        validated_data.setdefault("default_sides", "SIMPLEX")
        validated_data.setdefault("default_finished_width_mm", 90)
        validated_data.setdefault("default_finished_height_mm", 54)
        if validated_data.get("min_quantity", 1) < 1:
            validated_data["min_quantity"] = 1
        # Force DRAFT when shop pricing not ready
        from setup.services import pricing_exists
        if not pricing_exists(shop):
            validated_data["status"] = "DRAFT"
        else:
            validated_data.setdefault("status", "DRAFT")
        product = Product.objects.create(shop=shop, **validated_data)
        for fd in finishings_data:
            ProductFinishingOption.objects.create(product=product, **fd)
        return product

    def update(self, instance, validated_data):
        finishings_data = validated_data.pop("finishing_options", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if finishings_data is not None:
            instance.finishing_options.all().delete()
            for fd in finishings_data:
                ProductFinishingOption.objects.create(product=instance, **fd)
        return instance


class ProductFinishingOptionListSerializer(serializers.ModelSerializer):
    """Read-only serializer for list. Uses finishing_rate_id to avoid following FK (prevents 500 on orphaned refs)."""

    finishing_rate = serializers.IntegerField(source="finishing_rate_id", read_only=True)

    class Meta:
        model = ProductFinishingOption
        fields = ["finishing_rate", "is_default", "price_adjustment"]


class ProductListSerializer(serializers.ModelSerializer):
    """Printer-facing list serializer with status + publish readiness."""

    finishing_options = ProductFinishingOptionListSerializer(many=True, required=False, read_only=True)
    can_publish = serializers.SerializerMethodField()
    publish_block_reason = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "category",
            "pricing_mode",
            "default_finished_width_mm",
            "default_finished_height_mm",
            "default_sheet_size",
            "default_bleed_mm",
            "default_sides",
            "min_quantity",
            "min_width_mm",
            "min_height_mm",
            "max_width_mm",
            "max_height_mm",
            "min_gsm",
            "max_gsm",
            "allowed_sheet_sizes",
            "allow_simplex",
            "allow_duplex",
            "is_active",
            "status",
            "can_publish",
            "publish_block_reason",
            "finishing_options",
        ]

    def get_can_publish(self, obj):
        from setup.services import get_product_publish_check
        return get_product_publish_check(obj)["can_publish"]

    def get_publish_block_reason(self, obj):
        from setup.services import get_product_publish_check
        check = get_product_publish_check(obj)
        return " ".join(check["block_reasons"]) if check["block_reasons"] else ""


class ProductSerializer(serializers.ModelSerializer):
    """Full product serializer with price hints (for retrieve)."""

    finishing_options = ProductFinishingOptionWriteSerializer(many=True, required=False)
    price_hint = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "description",
            "category",
            "pricing_mode",
            "default_finished_width_mm",
            "default_finished_height_mm",
            "default_sheet_size",
            "default_bleed_mm",
            "default_sides",
            "min_quantity",
            "min_width_mm",
            "min_height_mm",
            "max_width_mm",
            "max_height_mm",
            "min_gsm",
            "max_gsm",
            "allowed_sheet_sizes",
            "allow_simplex",
            "allow_duplex",
            "is_active",
            "finishing_options",
            "price_hint",
            "price_range_est",
        ]

    def get_price_hint(self, obj):
        try:
            from catalog.services import product_price_hint

            return product_price_hint(obj)
        except Exception as e:
            logger.warning("product_price_hint failed for product %s: %s", obj.id if obj.pk else "new", e, exc_info=True)
            return {
                "can_calculate": False,
                "min_price": None,
                "max_price": None,
                "price_display": "Price on request",
                "pricing_mode_label": getattr(obj, "pricing_mode", ""),
                "pricing_mode_explanation": "Price depends on your choices (paper, quantity, finishing).",
                "reason": "Unable to compute price (shop setup may be incomplete).",
            }

    def get_price_range_est(self, obj):
        try:
            from catalog.services import compute_product_price_range_est

            return compute_product_price_range_est(obj)
        except Exception as e:
            logger.warning("compute_product_price_range_est failed for product %s: %s", obj.id if obj.pk else "new", e, exc_info=True)
            return {
                "can_calculate": False,
                "price_display": "Price on request",
                "pricing_mode_label": getattr(obj, "pricing_mode", ""),
                "pricing_mode_explanation": "Price depends on your choices (paper, quantity, finishing).",
                "lowest": {"total": None, "unit_price": None, "paper_id": None, "paper_label": None, "printing_rate_id": None, "assumptions": {}, "summary": None},
                "highest": {"total": None, "unit_price": None, "paper_id": None, "paper_label": None, "printing_rate_id": None, "assumptions": {}, "summary": None},
                "reason": "Unable to compute price range (shop setup may be incomplete).",
            }

    def validate(self, attrs):
        shop = self.context.get("shop")
        if shop:
            pass
        return attrs


# ---------------------------------------------------------------------------
# Profile (User as Profile - no separate Profile model)
# ---------------------------------------------------------------------------


class ProfileSerializer(serializers.Serializer):
    """Profile-like representation of User. Frontend expects id, user, bio, social_links, etc."""

    id = serializers.IntegerField(read_only=True)
    user = serializers.IntegerField(read_only=True)
    bio = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    avatar = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    phone = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    address = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    city = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    state = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    country = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    postal_code = serializers.CharField(allow_null=True, required=False, allow_blank=True)
    social_links = serializers.ListField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def to_representation(self, instance):
        """Map User to Profile-like output."""
        if isinstance(instance, User):
            return {
                "id": instance.id,
                "user": instance.id,
                "bio": None,
                "avatar": None,
                "phone": None,
                "address": None,
                "city": None,
                "state": None,
                "country": None,
                "postal_code": None,
                "social_links": [],
                "created_at": instance.created_at,
                "updated_at": instance.updated_at,
            }
        return super().to_representation(instance)

    def update(self, instance, validated_data):
        """Update User fields (name, preferred_language). Other profile fields ignored for now."""
        if isinstance(instance, User):
            if "name" in validated_data:
                instance.name = validated_data["name"]
            if "preferred_language" in validated_data:
                instance.preferred_language = validated_data["preferred_language"]
            instance.save()
        return instance


# ---------------------------------------------------------------------------
# Tweak-and-Add serializers (Gallery → Tweak → Quote)
# ---------------------------------------------------------------------------


class TweakFinishingInputSerializer(serializers.Serializer):
    """One finishing selection in a tweak request."""
    finishing_rate = serializers.PrimaryKeyRelatedField(queryset=FinishingRate.objects.filter(is_active=True))
    price_override = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)


class TweakAndAddSerializer(serializers.Serializer):
    """
    Create a tweaked quote item from a product template and add to quote.

    Example request:
    {
        "product": 5,
        "quantity": 200,
        "paper": 9,
        "sides": "DUPLEX",
        "color_mode": "COLOR",
        "machine": 1,
        "finishings": [{"finishing_rate": 1}, {"finishing_rate": 3}],
        "special_instructions": "Rush order"
    }

    Example response: See TweakedItemReadSerializer.
    """
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField(required=False, default=None)
    paper = serializers.PrimaryKeyRelatedField(queryset=Paper.objects.filter(is_active=True), required=False, allow_null=True)
    material = serializers.PrimaryKeyRelatedField(queryset=Material.objects.filter(is_active=True), required=False, allow_null=True)
    sides = serializers.ChoiceField(choices=[("SIMPLEX", "Simplex"), ("DUPLEX", "Duplex")], required=False, default="")
    color_mode = serializers.ChoiceField(choices=[("BW", "B&W"), ("COLOR", "Color")], required=False, default="COLOR")
    machine = serializers.PrimaryKeyRelatedField(queryset=Machine.objects.filter(is_active=True), required=False, allow_null=True)
    chosen_width_mm = serializers.IntegerField(required=False, allow_null=True)
    chosen_height_mm = serializers.IntegerField(required=False, allow_null=True)
    finishings = TweakFinishingInputSerializer(many=True, required=False, default=[])
    special_instructions = serializers.CharField(required=False, allow_blank=True, default="")
    has_artwork = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        product = attrs["product"]
        shop = self.context.get("shop")

        if shop and product.shop_id != shop.id:
            raise serializers.ValidationError({"product": "Product must belong to this shop."})

        # Default quantity to product's min_quantity
        qty = attrs.get("quantity")
        min_qty = product.min_quantity or 100
        if qty is None or qty < min_qty:
            attrs["quantity"] = min_qty

        # Default sides from template
        if not attrs.get("sides"):
            attrs["sides"] = product.default_sides or "SIMPLEX"

        # Validate simplex/duplex against template
        if attrs["sides"] == "SIMPLEX" and not product.allow_simplex:
            raise serializers.ValidationError({"sides": "This product does not allow single-sided printing."})
        if attrs["sides"] == "DUPLEX" and not product.allow_duplex:
            raise serializers.ValidationError({"sides": "This product does not allow double-sided printing."})

        pricing_mode = product.pricing_mode

        if pricing_mode == PricingMode.SHEET:
            # Default machine from product if not provided
            machine = attrs.get("machine")
            default_machine = getattr(product, "default_machine", None)
            if machine is None and default_machine and shop and default_machine.shop_id == shop.id:
                attrs["machine"] = default_machine
                machine = attrs["machine"]
            # Validate paper belongs to same shop and product rules (gsm, allowed_sheet_sizes, dimensions)
            paper = attrs.get("paper")
            if paper and shop and paper.shop_id != shop.id:
                raise serializers.ValidationError({"paper": "Paper must belong to this shop."})
            if paper:
                from catalog.validation import validate_product_configuration
                w_mm = attrs.get("chosen_width_mm") or product.default_finished_width_mm
                h_mm = attrs.get("chosen_height_mm") or product.default_finished_height_mm
                v = validate_product_configuration(product, paper=paper, width_mm=w_mm, height_mm=h_mm)
                if not v["is_valid"]:
                    raise serializers.ValidationError({"paper": "; ".join(v["errors"])})
            # Validate machine
            machine = attrs.get("machine")
            if machine and shop and machine.shop_id != shop.id:
                raise serializers.ValidationError({"machine": "Machine must belong to this shop."})

        elif pricing_mode == PricingMode.LARGE_FORMAT:
            # Default dimensions from product if not provided
            if not attrs.get("chosen_width_mm"):
                attrs["chosen_width_mm"] = product.default_finished_width_mm
            if not attrs.get("chosen_height_mm"):
                attrs["chosen_height_mm"] = product.default_finished_height_mm
            w = attrs.get("chosen_width_mm") or 0
            h = attrs.get("chosen_height_mm") or 0
            # Validate dimensions against product rules
            from catalog.validation import validate_product_configuration
            v = validate_product_configuration(product, width_mm=w, height_mm=h)
            if not v["is_valid"]:
                raise serializers.ValidationError({"chosen_width_mm": "; ".join(v["errors"])})
            # Validate material
            mat = attrs.get("material")
            if mat and shop and mat.shop_id != shop.id:
                raise serializers.ValidationError({"material": "Material must belong to this shop."})
            # Validate minimum area
            qty = attrs["quantity"]
            area = (w / 1000) * (h / 1000) * qty
            min_area = float(product.min_area_m2 or Decimal("0.50"))
            if area < min_area:
                raise serializers.ValidationError(
                    {"chosen_width_mm": f"Total area ({area:.2f} m²) is below minimum ({min_area:.2f} m²)."}
                )

        # Validate finishings belong to the same shop
        for fin in attrs.get("finishings", []):
            fr = fin["finishing_rate"]
            if shop and fr.shop_id != shop.id:
                raise serializers.ValidationError({"finishings": f"Finishing '{fr.name}' does not belong to this shop."})

        return attrs

    def create(self, validated_data):
        """
        Create QuoteItem + QuoteItemFinishing records, then compute and store pricing.
        Must be called inside transaction.atomic().
        """
        from django.db import transaction
        from quotes.pricing_service import compute_and_store_pricing

        product = validated_data["product"]
        finishings_data = validated_data.pop("finishings", [])
        quote_request = self.context["quote_request"]

        paper = validated_data.get("paper")
        material = validated_data.get("material")
        machine = validated_data.get("machine")
        item_spec_snapshot = {
            "product_id": product.id,
            "quantity": validated_data["quantity"],
            "paper_id": paper.id if paper else None,
            "material_id": material.id if material else None,
            "sides": validated_data.get("sides", ""),
            "color_mode": validated_data.get("color_mode", "COLOR"),
            "machine_id": machine.id if machine else None,
            "finishings": [
                {"finishing_rate_id": fr.id}
                for f in finishings_data
                for fr in [f.get("finishing_rate")]
                if fr
            ],
        }

        with transaction.atomic():
            item = QuoteItem.objects.create(
                quote_request=quote_request,
                item_type="PRODUCT",
                product=product,
                quantity=validated_data["quantity"],
                pricing_mode=product.pricing_mode,
                paper=validated_data.get("paper"),
                material=validated_data.get("material"),
                chosen_width_mm=validated_data.get("chosen_width_mm"),
                chosen_height_mm=validated_data.get("chosen_height_mm"),
                sides=validated_data.get("sides", ""),
                color_mode=validated_data.get("color_mode", "COLOR"),
                machine=validated_data.get("machine"),
                special_instructions=validated_data.get("special_instructions", ""),
                has_artwork=validated_data.get("has_artwork", False),
                item_spec_snapshot=item_spec_snapshot,
            )
            for fin in finishings_data:
                QuoteItemFinishing.objects.create(
                    quote_item=item,
                    finishing_rate=fin["finishing_rate"],
                    price_override=fin.get("price_override"),
                )
            compute_and_store_pricing(item)

        return item


class TweakedItemReadSerializer(serializers.ModelSerializer):
    """
    Read serializer for a tweaked quote item — returns chosen options,
    computed totals, and full pricing breakdown.

    Example response:
    {
        "id": 42,
        "product": 5,
        "product_name": "Standard Business Card",
        "quantity": 200,
        "pricing_mode": "SHEET",
        "sides": "DUPLEX",
        "color_mode": "COLOR",
        "paper": 9,
        "paper_label": "SRA3 300gsm GLOSS",
        "machine": 1,
        "finishings": [{"finishing_rate": 1, "name": "Lamination", "cost": "250.00"}],
        "unit_price": "12.40",
        "line_total": "2480.00",
        "pricing_snapshot": { ...full breakdown... },
        "special_instructions": "",
        "created_at": "2026-03-03T..."
    }
    """
    product_name = serializers.SerializerMethodField()
    paper_label = serializers.SerializerMethodField()
    material_label = serializers.SerializerMethodField()
    finishings = serializers.SerializerMethodField()

    class Meta:
        model = QuoteItem
        fields = [
            "id",
            "item_type",
            "product",
            "product_name",
            "quantity",
            "pricing_mode",
            "sides",
            "color_mode",
            "paper",
            "paper_label",
            "material",
            "material_label",
            "machine",
            "chosen_width_mm",
            "chosen_height_mm",
            "finishings",
            "unit_price",
            "line_total",
            "pricing_snapshot",
            "special_instructions",
            "has_artwork",
            "created_at",
        ]

    def get_product_name(self, obj):
        if obj.product_id:
            return obj.product.name
        return obj.title or ""

    def get_paper_label(self, obj):
        if obj.paper_id:
            p = obj.paper
            return f"{p.sheet_size} {p.gsm}gsm {p.get_paper_type_display()}"
        return None

    def get_material_label(self, obj):
        if obj.material_id:
            return f"{obj.material.material_type} ({obj.material.unit})"
        return None

    def get_finishings(self, obj):
        result = []
        snapshot = obj.pricing_snapshot or {}
        finishing_lines = {fl["name"]: fl["computed_cost"] for fl in snapshot.get("finishing_lines", [])}
        for qif in obj.finishings.select_related("finishing_rate").all():
            result.append({
                "finishing_rate": qif.finishing_rate_id,
                "name": qif.finishing_rate.name,
                "charge_unit": qif.finishing_rate.charge_unit,
                "cost": finishing_lines.get(qif.finishing_rate.name, str(qif.finishing_rate.price)),
            })
        return result


class QuoteCalculatorInputSerializer(serializers.Serializer):
    """Input for POST /api/calculator/quote-item/ — staff-only preview."""

    product_id = serializers.IntegerField(required=True)
    quantity = serializers.IntegerField(required=True, min_value=1)
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    paper_id = serializers.IntegerField(required=False, allow_null=True)
    grammage = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    paper_type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    sheet_size = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    finishing_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=True,
        default=list,
    )
    machine_id = serializers.IntegerField(required=False, allow_null=True)
    sides = serializers.ChoiceField(
        choices=[("SIMPLEX", "Simplex"), ("DUPLEX", "Duplex")],
        required=False,
        default="SIMPLEX",
    )
    color_mode = serializers.ChoiceField(
        choices=[("COLOR", "Color"), ("BW", "B&W")],
        required=False,
        default="COLOR",
    )
    overhead_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
    )
    margin_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        required=False,
        allow_null=True,
    )

    def validate(self, attrs):
        paper_id = attrs.get("paper_id")
        grammage = attrs.get("grammage")
        paper_type = attrs.get("paper_type") or ""
        if not paper_id and (grammage is None or not paper_type.strip()):
            raise serializers.ValidationError(
                {"paper_id": "Provide paper_id or both grammage and paper_type."}
            )
        return attrs


class GalleryProductOptionsSerializer(serializers.ModelSerializer):
    """
    Gallery product with available tweaking options (papers, finishings, machines, materials).
    No user-specific computed totals — just the template + available choices.
    """
    finishing_options = FinishingOptionSerializer(many=True, read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    primary_image = serializers.SerializerMethodField()
    available_papers = serializers.SerializerMethodField()
    available_machines = serializers.SerializerMethodField()
    available_materials = serializers.SerializerMethodField()
    available_finishings = serializers.SerializerMethodField()
    imposition_summary = serializers.SerializerMethodField()
    final_size = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "name", "description", "category", "pricing_mode",
            "default_finished_width_mm", "default_finished_height_mm",
            "default_bleed_mm", "default_sides", "default_machine", "min_quantity",
            "min_gsm", "max_gsm", "min_area_m2",
            "allow_simplex", "allow_duplex",
            "finishing_options", "images", "primary_image",
            "imposition_summary", "final_size",
            "available_papers", "available_machines",
            "available_materials", "available_finishings",
        ]

    def get_primary_image(self, obj):
        img = obj.get_primary_image()
        return img.image.name if img and img.image else None

    def get_imposition_summary(self, obj):
        if obj.pricing_mode != "SHEET":
            return None
        try:
            from inventory.choices import SHEET_SIZE_DIMENSIONS
            ss = (obj.default_sheet_size or "").strip() or "SRA3"
            dims = SHEET_SIZE_DIMENSIONS.get(ss)
            if dims:
                cps = obj.get_copies_per_sheet(ss, dims[0], dims[1])
                return f"{ss}: {cps}-up"
        except Exception:
            pass
        return None

    def get_final_size(self, obj):
        w, h = obj.default_finished_width_mm, obj.default_finished_height_mm
        return f"{w}×{h}mm" if w and h else None

    def get_available_papers(self, obj):
        papers = Paper.objects.filter(shop=obj.shop, is_active=True, selling_price__gt=0)
        if obj.min_gsm:
            papers = papers.filter(gsm__gte=obj.min_gsm)
        if obj.max_gsm:
            papers = papers.filter(gsm__lte=obj.max_gsm)
        return [
            {"id": p.id, "sheet_size": p.sheet_size, "gsm": p.gsm,
             "paper_type": p.get_paper_type_display(), "selling_price": str(p.selling_price)}
            for p in papers[:20]
        ]

    def get_available_machines(self, obj):
        machines = Machine.objects.filter(shop=obj.shop, is_active=True)
        return [{"id": m.id, "name": m.name, "machine_type": m.machine_type} for m in machines[:10]]

    def get_available_materials(self, obj):
        if obj.pricing_mode != "LARGE_FORMAT":
            return []
        materials = Material.objects.filter(shop=obj.shop, is_active=True, selling_price__gt=0)
        return [
            {"id": m.id, "material_type": m.material_type, "unit": m.unit,
             "selling_price": str(m.selling_price)}
            for m in materials[:10]
        ]

    def get_available_finishings(self, obj):
        frs = FinishingRate.objects.filter(shop=obj.shop, is_active=True).select_related("category")
        return [
            {"id": f.id, "name": f.name, "charge_unit": f.charge_unit,
             "price": str(f.price), "category": f.category.name if f.category else None}
            for f in frs[:30]
        ]
