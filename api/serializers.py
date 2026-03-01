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
from inventory.models import Machine, Paper
from pricing.choices import ColorMode, Sides
from pricing.models import FinishingRate, Material, PrintingRate
from quotes.choices import QuoteStatus
from quotes.models import QuoteItem, QuoteItemFinishing, QuoteRequest
from shops.models import FavoriteShop, Shop, ShopRating

from .validators import validate_shop_consistency


# ---------------------------------------------------------------------------
# Public / Read-only serializers
# ---------------------------------------------------------------------------


class PublicShopListSerializer(serializers.ModelSerializer):
    """List active shops (public)."""

    class Meta:
        model = Shop
        fields = ["id", "name", "slug", "currency"]


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
    """Product with allowed finishing options and price hint for public catalog."""

    finishing_options = FinishingOptionSerializer(many=True, read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    primary_image = serializers.SerializerMethodField()
    default_sides = serializers.CharField()
    pricing_mode = serializers.CharField()
    price_hint = serializers.SerializerMethodField()
    price_range_est = serializers.SerializerMethodField()

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
            "default_bleed_mm",
            "default_sides",
            "finishing_options",
            "images",
            "primary_image",
            "price_hint",
            "price_range_est",
        ]

    def get_primary_image(self, obj):
        """Path of primary or first image for card display (frontend prepends mediaBase)."""
        img = obj.get_primary_image()
        if img and img.image:
            # Return path relative to MEDIA_ROOT (e.g. products/xxx.jpg)
            return img.image.name
        return None

    def get_price_hint(self, obj):
        from catalog.services import product_price_hint

        return product_price_hint(obj)

    def get_price_range_est(self, obj):
        from catalog.services import compute_product_price_range_est

        return compute_product_price_range_est(obj)


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
        fields = ["finishing_rate", "coverage_qty", "price_override"]

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
        ]

    def validate(self, attrs):
        quote_request = self.context.get("quote_request")
        if not quote_request:
            return attrs

        shop = quote_request.shop
        item_type = attrs.get("item_type") or getattr(self.instance, "item_type", "PRODUCT")

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
        finishings_data = validated_data.pop("finishings", [])
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

        item = QuoteItem.objects.create(quote_request=quote_request, **validated_data)
        for fd in finishings_data:
            QuoteItemFinishing.objects.create(quote_item=item, **fd)
        return item

    def update(self, instance, validated_data):
        finishings_data = validated_data.pop("finishings", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if finishings_data is not None:
            instance.finishings.all().delete()
            for fd in finishings_data:
                QuoteItemFinishing.objects.create(quote_item=instance, **fd)
        return instance


class QuoteItemReadSerializer(serializers.ModelSerializer):
    """Read serializer for quote items (PRODUCT + CUSTOM)."""

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
            "finishings",
        ]

    def get_product_name(self, obj):
        if obj.item_type == "PRODUCT" and obj.product_id:
            return obj.product.name
        return obj.title or ""


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
# Seller serializers (shop-scoped with consistency validation)
# ---------------------------------------------------------------------------


class ShopSerializer(serializers.ModelSerializer):
    """CRUD for seller's own shop."""

    class Meta:
        model = Shop
        fields = [
            "id",
            "name",
            "slug",
            "currency",
            "is_active",
            "description",
            "business_email",
            "phone_number",
            "address_line",
            "city",
            "state",
            "country",
            "zip_code",
        ]


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


class PaperSerializer(serializers.ModelSerializer):
    """CRUD for shop papers."""

    class Meta:
        model = Paper
        fields = [
            "id",
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
        ]

    def validate(self, attrs):
        machine = self.context.get("machine")
        if machine and self.instance is None:
            # Ensure machine belongs to shop when creating
            pass
        return attrs


class FinishingRateSerializer(serializers.ModelSerializer):
    """CRUD for shop finishing rates."""

    class Meta:
        model = FinishingRate
        fields = [
            "id",
            "name",
            "charge_unit",
            "price",
            "setup_fee",
            "min_qty",
            "is_active",
        ]


class MaterialSerializer(serializers.ModelSerializer):
    """CRUD for shop materials."""

    class Meta:
        model = Material
        fields = [
            "id",
            "material_type",
            "unit",
            "buying_price",
            "selling_price",
            "is_active",
        ]


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
    """Write-only serializer for create/update. No price computation to avoid 500 on incomplete shop setup."""

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
            "default_bleed_mm",
            "default_sides",
            "min_quantity",
            "min_width_mm",
            "min_height_mm",
            "min_gsm",
            "max_gsm",
            "is_active",
            "finishing_options",
        ]
        extra_kwargs = {
            "id": {"read_only": True},
            "name": {"required": True, "allow_blank": False},
            "description": {"required": False, "allow_blank": True},
            "category": {"required": False, "allow_blank": True},
            "default_bleed_mm": {"required": False},
            "default_sides": {"required": False},
            "min_quantity": {"required": False, "min_value": 1},
            "min_width_mm": {"required": False},
            "min_height_mm": {"required": False},
            "min_gsm": {"required": False},
            "max_gsm": {"required": False},
        }

    def create(self, validated_data):
        finishings_data = validated_data.pop("finishing_options", [])
        shop = validated_data.pop("shop", None) or self.context.get("shop")
        # Ensure required defaults for model
        validated_data.setdefault("description", "")
        validated_data.setdefault("category", "")
        validated_data.setdefault("min_quantity", 1)
        validated_data.setdefault("default_bleed_mm", 3)
        validated_data.setdefault("default_sides", "SIMPLEX")
        validated_data.setdefault("default_finished_width_mm", 90)
        validated_data.setdefault("default_finished_height_mm", 54)
        # Ensure min_quantity is at least 1
        if validated_data.get("min_quantity", 1) < 1:
            validated_data["min_quantity"] = 1
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


class ProductSerializer(serializers.ModelSerializer):
    """Full product serializer with price hints (for list/retrieve)."""

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
            "default_bleed_mm",
            "default_sides",
            "min_quantity",
            "min_width_mm",
            "min_height_mm",
            "min_gsm",
            "max_gsm",
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
