"""Notification serializers."""
from rest_framework import serializers

from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    notification_type_display = serializers.CharField(
        source="get_notification_type_display", read_only=True
    )
    is_read = serializers.BooleanField(read_only=True)
    actor_email = serializers.CharField(source="actor.email", read_only=True, allow_null=True)
    target_url = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "notification_type",
            "notification_type_display",
            "message",
            "object_type",
            "object_id",
            "actor",
            "actor_email",
            "is_read",
            "read_at",
            "created_at",
            "target_url",
        ]
        read_only_fields = fields

    def get_target_url(self, obj):
        """Build frontend URL for the notification target (recipient-specific)."""
        if not obj.object_type or obj.object_id is None:
            return None
        ot, oid = obj.object_type, obj.object_id
        user_id = obj.user_id
        if ot == "quote_request":
            try:
                from quotes.models import QuoteRequest
                qr = QuoteRequest.objects.select_related("shop").get(pk=oid)
                if qr.shop.owner_id == user_id:
                    return f"/dashboard/shops/{qr.shop.slug}/incoming-requests/{oid}"
                return f"/quotes/{oid}"
            except QuoteRequest.DoesNotExist:
                return None
        if ot == "shop_quote":
            try:
                from quotes.models import ShopQuote
                sq = ShopQuote.objects.select_related("shop", "quote_request").get(pk=oid)
                if sq.shop.owner_id == user_id:
                    return f"/dashboard/shops/{sq.shop.slug}/sent-quotes/{oid}"
                return f"/quotes/{sq.quote_request_id}"
            except ShopQuote.DoesNotExist:
                return None
        if ot == "production_order":
            return f"/dashboard/jobs/{oid}"
        return None
