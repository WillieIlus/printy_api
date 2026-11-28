"""Production serializers for shop-side ProductionOrder fulfillment."""
from rest_framework import serializers

from .models import ProductionOrder


class ProductionOrderListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionOrder
        fields = [
            "id",
            "shop",
            "quote",
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
    class Meta:
        model = ProductionOrder
        fields = [
            "id",
            "shop",
            "quote",
            "order_number",
            "title",
            "quantity",
            "status",
            "delivery_status",
            "delivered_at",
            "due_date",
            "completed_at",
            "notes",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_by", "created_at", "updated_at"]


class ProductionOrderWriteSerializer(serializers.ModelSerializer):
    """Create/update a ProductionOrder for the requesting printer's own shop.

    Audit #5:
    - ``shop`` is never taken from the client. It is resolved from the quote's
      shop assignment (or the printer's only shop when no quote is given).
    - The quote must belong to that shop: if the quote has job assignments the
      shop must be one of the assigned shops, otherwise it must be the quote's
      own shop. The printer must own that shop (platform admins excepted).
    - An existing order can never be moved to another shop or quote.
    """

    shop = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = ProductionOrder
        fields = [
            "shop",
            "quote",
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

    # -- helpers ----------------------------------------------------------
    def _user(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    @staticmethod
    def _is_platform_admin(user) -> bool:
        return bool(user and getattr(user, "is_authenticated", False) and (user.is_staff or user.is_superuser))

    @staticmethod
    def _shops_for_quote(quote):
        """Shops the quote may be produced by (assignment first, else quote.shop)."""
        from jobs.models import JobAssignment

        assigned = list(
            JobAssignment.objects.filter(source_quote=quote, assigned_shop__isnull=False)
            .values_list("assigned_shop_id", flat=True)
            .distinct()
        )
        if assigned:
            return set(assigned)
        return {quote.shop_id} if quote.shop_id else set()

    def _resolve_shop(self, quote):
        from shops.models import Shop

        user = self._user()
        if user is None or not getattr(user, "is_authenticated", False):
            raise serializers.ValidationError("Authentication required.")
        is_admin = self._is_platform_admin(user)
        if quote is not None:
            allowed = self._shops_for_quote(quote)
            if not is_admin:
                allowed = set(Shop.objects.filter(id__in=allowed, owner=user).values_list("id", flat=True))
            if not allowed:
                raise serializers.ValidationError({"quote": "This quote does not belong to your shop."})
            if len(allowed) > 1:
                raise serializers.ValidationError({"quote": "This quote is assigned to several shops; ask an admin."})
            return Shop.objects.get(pk=next(iter(allowed)))
        if is_admin:
            raise serializers.ValidationError({"quote": "Platform admins must create orders from a quote."})
        owned = list(Shop.objects.filter(owner=user).order_by("id")[:2])
        if not owned:
            raise serializers.ValidationError({"shop": "You do not own a shop."})
        if len(owned) > 1:
            raise serializers.ValidationError({"quote": "You own several shops; create the order from a quote."})
        return owned[0]

    # -- validation -------------------------------------------------------
    def validate(self, attrs):
        attrs = super().validate(attrs)
        initial = getattr(self, "initial_data", None) or {}
        if self.instance is None:
            shop = self._resolve_shop(attrs.get("quote"))
            raw_shop = initial.get("shop") if hasattr(initial, "get") else None
            if raw_shop not in (None, "") and str(raw_shop) != str(shop.pk):
                raise serializers.ValidationError({"shop": "You can only create production orders for your own shop."})
            attrs["shop"] = shop
            return attrs

        # Update: shop and quote are immutable once the order exists.
        if "shop" in initial:
            raw_shop = initial.get("shop")
            if str(raw_shop if raw_shop is not None else "") != str(self.instance.shop_id):
                raise serializers.ValidationError({"shop": "A production order cannot be moved to another shop."})
        if "quote" in attrs:
            new_quote_id = attrs["quote"].pk if attrs["quote"] is not None else None
            if new_quote_id != self.instance.quote_id:
                raise serializers.ValidationError({"quote": "A production order cannot be moved to another quote."})
        return attrs
