"""
Production tracking serializers.
"""
from decimal import Decimal

from rest_framework import serializers

from .models import (
    Customer,
    ProductionOrder,
    JobProcess,
    Operator,
    PriceCard,
    PricingMethod,
    Process,
    ProductionMaterial,
    ProductionProduct,
    WastageStage,
)


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ["id", "name", "email", "phone", "address", "notes"]


class ProductionProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionProduct
        fields = ["id", "name", "catalog_product", "description"]


class ProductionMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionMaterial
        fields = ["id", "name", "pricing_material", "unit"]


class ProcessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Process
        fields = ["id", "name", "slug", "display_order"]


class OperatorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Operator
        fields = ["id", "name", "user", "is_active"]


class PricingMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricingMethod
        fields = ["id", "name", "slug", "unit_label"]


class WastageStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = WastageStage
        fields = ["id", "name", "process"]


class PriceCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceCard
        fields = ["id", "process", "pricing_method", "default_rate", "material"]


class JobProcessSerializer(serializers.ModelSerializer):
    good_qty = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    process_name = serializers.CharField(source="process.name", read_only=True)
    operator_name = serializers.CharField(source="operator.name", read_only=True, allow_null=True)
    material_name = serializers.CharField(source="material.name", read_only=True, allow_null=True)
    pricing_method_name = serializers.CharField(
        source="pricing_method.name", read_only=True, allow_null=True
    )

    class Meta:
        model = JobProcess
        fields = [
            "id",
            "process",
            "process_name",
            "operator",
            "operator_name",
            "material",
            "material_name",
            "pricing_method",
            "pricing_method_name",
            "date",
            "qty_input",
            "waste",
            "good_qty",
            "default_rate",
            "applied_rate",
            "billable_units",
            "line_total",
            "notes",
        ]

    def validate(self, attrs):
        qty_input = attrs.get("qty_input") or Decimal("0")
        waste = attrs.get("waste") or Decimal("0")
        if waste > qty_input:
            raise serializers.ValidationError({"waste": "Waste cannot exceed qty_input."})
        return attrs

    def save(self, **kwargs):
        instance = super().save(**kwargs)
        if instance.applied_rate is None or instance.applied_rate == 0:
            instance.applied_rate = instance.default_rate or Decimal("0")
        instance.line_total = (instance.billable_units or Decimal("0")) * (
            instance.applied_rate or Decimal("0")
        )
        instance.save()
        return instance


class JobProcessWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobProcess
        fields = [
            "process",
            "operator",
            "material",
            "pricing_method",
            "date",
            "qty_input",
            "waste",
            "default_rate",
            "applied_rate",
            "billable_units",
            "line_total",
            "notes",
        ]

    def validate(self, attrs):
        qty_input = attrs.get("qty_input") or Decimal("0")
        waste = attrs.get("waste") or Decimal("0")
        if waste > qty_input:
            raise serializers.ValidationError({"waste": "Waste cannot exceed qty_input."})
        return attrs


class ProductionOrderListSerializer(serializers.ModelSerializer):
    """Job list — lightweight for shop dashboard."""

    customer_name = serializers.CharField(source="customer.name", read_only=True, allow_null=True)

    class Meta:
        model = ProductionOrder
        fields = [
            "id",
            "shop_quote",
            "customer",
            "customer_name",
            "order_number",
            "title",
            "quantity",
            "status",
            "delivery_status",
            "due_date",
            "completed_at",
            "delivered_at",
            "created_at",
        ]


class ProductionOrderSerializer(serializers.ModelSerializer):
    """Job detail — full with processes, shop_quote link, delivery info."""

    processes = JobProcessSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True, allow_null=True)
    total_revenue = serializers.SerializerMethodField()
    shop_quote_total = serializers.SerializerMethodField()

    def get_shop_quote_total(self, obj):
        return obj.shop_quote.total if obj.shop_quote_id else None

    def get_total_revenue(self, obj):
        return getattr(obj, "total_revenue", Decimal("0")) or sum(
            (jp.line_total or Decimal("0")) for jp in obj.processes.all()
        )

    class Meta:
        model = ProductionOrder
        fields = [
            "id",
            "shop_quote",
            "shop_quote_total",
            "customer",
            "customer_name",
            "product",
            "product_name",
            "order_number",
            "title",
            "quantity",
            "status",
            "delivery_status",
            "delivered_at",
            "due_date",
            "completed_at",
            "notes",
            "processes",
            "total_revenue",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class ProductionOrderWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionOrder
        fields = [
            "customer",
            "product",
            "shop_quote",
            "order_number",
            "title",
            "quantity",
            "status",
            "delivery_status",
            "delivered_at",
            "due_date",
            "completed_at",
            "notes",
        ]
        extra_kwargs = {"shop_quote": {"required": False}}

    def create(self, validated_data):
        request = self.context.get("request")
        shop_quote = validated_data.pop("shop_quote", None)
        if shop_quote:
            shop = shop_quote.shop
            qr = shop_quote.quote_request
            customer = validated_data.get("customer")
            if not customer and qr:
                customer = self._get_or_create_customer(qr, shop)
                validated_data["customer"] = customer
            if not validated_data.get("title") and qr:
                validated_data["title"] = f"Job from Quote #{qr.id}"
            if validated_data.get("quantity") is None and qr:
                validated_data["quantity"] = sum(
                    i.quantity for i in qr.items.all()
                )
        else:
            shop = self._get_shop_from_request(request)
            if not shop:
                raise serializers.ValidationError(
                    {"shop": "Shop required. Provide ?shop=<slug> or shop in body."}
                )
        validated_data["shop"] = shop
        validated_data["shop_quote"] = shop_quote
        if request and request.user:
            validated_data["created_by"] = request.user
        return super().create(validated_data)

    def _get_shop_from_request(self, request):
        """Only return shop if user owns it (or is staff)."""
        if not request or not request.user or not request.user.is_authenticated:
            return None
        from shops.models import Shop
        user = request.user
        shop_slug = request.query_params.get("shop") or request.data.get("shop")
        if shop_slug:
            shop = Shop.objects.filter(slug=shop_slug, is_active=True).first()
            if not shop:
                return None
            if user.is_staff or shop.owner_id == user.id:
                return shop
            return None
        return user.owned_shops.filter(is_active=True).first()

    def _get_or_create_customer(self, quote_request, shop):
        if quote_request.customer_id:
            return quote_request.customer
        name = quote_request.customer_name or "Customer"
        email = quote_request.customer_email or ""
        if not name and quote_request.created_by_id:
            name = getattr(quote_request.created_by, "email", "") or "Customer"
        customer, _ = Customer.objects.get_or_create(
            shop=shop,
            name=name[:255],
            defaults={"email": email, "phone": quote_request.customer_phone or ""},
        )
        return customer
