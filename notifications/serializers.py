"""Notification serializers."""
import logging

from rest_framework import serializers

from accounts.services.roles import (
    CANONICAL_CLIENT_ROLE,
    CANONICAL_PARTNER_ROLE,
    CANONICAL_PRODUCTION_ROLE,
    CANONICAL_SUPER_ADMIN_ROLE,
    resolve_user_roles,
)
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
        """Build frontend URL for the notification target (recipient-specific).

        Frontend dashboard routes live under /app/* (single-page dashboards), so
        deep links resolve to the role dashboard rather than a legacy /dashboard/* URL.
        """
        try:
            if not obj.object_type or obj.object_id is None:
                return None
            ot, oid = obj.object_type, obj.object_id
            recipient = getattr(obj, "user", None)
            roles = set(resolve_user_roles(recipient))

            if ot == "quote_request":
                try:
                    from quotes.models import QuoteRequest

                    qr = QuoteRequest.objects.select_related(
                        "shop",
                        "created_by",
                        "assigned_manager",
                        "on_behalf_of",
                    ).get(pk=oid)
                    if CANONICAL_SUPER_ADMIN_ROLE in roles:
                        return "/app/admin"
                    if qr.shop and qr.shop.owner_id == obj.user_id:
                        return "/app/printer"
                    if qr.assigned_manager_id == obj.user_id or CANONICAL_PARTNER_ROLE in roles:
                        return "/app/manager"
                    if qr.created_by_id == obj.user_id or qr.on_behalf_of_id == obj.user_id or CANONICAL_CLIENT_ROLE in roles:
                        return "/app/buyer?tab=quote"
                    if CANONICAL_PRODUCTION_ROLE in roles:
                        return "/app/printer"
                    return None
                except QuoteRequest.DoesNotExist:
                    return None

            if ot == "quote":
                try:
                    from quotes.models import Quote

                    sq = Quote.objects.select_related(
                        "shop",
                        "quote_request",
                        "quote_request__created_by",
                        "quote_request__assigned_manager",
                        "quote_request__on_behalf_of",
                    ).get(pk=oid)
                    quote_request = sq.quote_request
                    if CANONICAL_SUPER_ADMIN_ROLE in roles:
                        return "/app/admin"
                    if sq.shop and sq.shop.owner_id == obj.user_id:
                        return "/app/printer"
                    if quote_request and (quote_request.assigned_manager_id == obj.user_id or CANONICAL_PARTNER_ROLE in roles):
                        return "/app/manager"
                    if quote_request and (quote_request.created_by_id == obj.user_id or quote_request.on_behalf_of_id == obj.user_id or CANONICAL_CLIENT_ROLE in roles):
                        return "/app/buyer?tab=quote"
                    if CANONICAL_PRODUCTION_ROLE in roles:
                        return "/app/printer"
                    return None
                except Quote.DoesNotExist:
                    return None

            if ot == "production_order":
                if CANONICAL_SUPER_ADMIN_ROLE in roles:
                    return "/app/admin"
                return "/app/printer"

            if ot == "managed_job":
                if CANONICAL_SUPER_ADMIN_ROLE in roles:
                    return "/app/admin"
                if CANONICAL_PRODUCTION_ROLE in roles:
                    return "/app/printer"
                if CANONICAL_PARTNER_ROLE in roles:
                    return "/app/manager"
                return "/app/buyer"
            return None
        except Exception as exc:
            logger.warning("Failed to build notification target URL: %s", exc)
            return None