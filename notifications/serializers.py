"""Notification serializers."""
import logging

from rest_framework import serializers

from api.visibility import can_actor_view_email, resolve_actor
from .models import Notification

logger = logging.getLogger(__name__)


class NotificationSerializer(serializers.ModelSerializer):
    notification_type_display = serializers.CharField(
        source="get_notification_type_display", read_only=True
    )
    is_read = serializers.BooleanField(read_only=True)
    actor_email = serializers.SerializerMethodField()
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

    def get_actor_email(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if not can_actor_view_email(actor=actor, topology_mode="managed"):
            return None
        actor_user = getattr(obj, "actor", None)
        return getattr(actor_user, "email", None)

    def get_target_url(self, obj):
        """Build frontend URL for the notification target (recipient-specific)."""
        try:
            if not obj.object_type or obj.object_id is None:
                return None
            ot, oid = obj.object_type, obj.object_id
            user_id = obj.user_id
            if ot == "quote_request":
                try:
                    from quotes.models import QuoteRequest
                    qr = QuoteRequest.objects.select_related("shop").get(pk=oid)
                    if qr.shop and qr.shop.owner_id == user_id:
                        return f"/dashboard/shops/{qr.shop.slug}/incoming-requests/{oid}"
                    return f"/quotes/{oid}"
                except QuoteRequest.DoesNotExist:
                    return None
            if ot == "quote":
                try:
                    from quotes.models import Quote
                    sq = Quote.objects.select_related("shop", "quote_request").get(pk=oid)
                    if sq.shop and sq.shop.owner_id == user_id:
                        return f"/dashboard/shops/{sq.shop.slug}/sent-quotes/{oid}"
                    return f"/quotes/{sq.quote_request_id}"
                except Quote.DoesNotExist:
                    return None
            if ot == "production_order":
                return f"/dashboard/jobs/{oid}"
            if ot == "managed_job":
                return f"/dashboard/jobs/{oid}"
            return None
        except Exception as exc:
            logger.warning("Failed to build notification target URL: %s", exc)
            return None
