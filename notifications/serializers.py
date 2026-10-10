"""Notification serializers."""
import logging

from rest_framework import serializers

from api.visibility import can_actor_view_email, resolve_actor
from .models import Notification
from .rendering import render_notification

logger = logging.getLogger(__name__)


class NotificationSerializer(serializers.ModelSerializer):
    """Structured notification rendered per recipient-role and language.

    The stored row carries a ``template_key`` + ``params``; ``title``/``body``/
    ``action_*`` are computed at read time so wording (and Swahili) is never
    baked into old rows and one event renders differently per audience.
    """

    notification_type_display = serializers.CharField(
        source="get_notification_type_display", read_only=True
    )
    type = serializers.CharField(source="notification_type", read_only=True)
    is_read = serializers.BooleanField(read_only=True)
    actor_email = serializers.SerializerMethodField()
    target_url = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    body = serializers.SerializerMethodField()
    action_url = serializers.SerializerMethodField()
    action_label = serializers.SerializerMethodField()
    entity = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = [
            "id",
            "type",
            "notification_type",
            "notification_type_display",
            "template_key",
            "priority",
            "title",
            "body",
            "message",
            "entity",
            "object_type",
            "object_id",
            "actor",
            "actor_email",
            "is_read",
            "read_at",
            "created_at",
            "target_url",
            "action_url",
            "action_label",
        ]
        read_only_fields = fields

    def _rendered(self, obj):
        cached = getattr(obj, "_rendered_notification", None)
        if cached is None:
            request = self.context.get("request")
            user = getattr(request, "user", None)
            lang = getattr(user, "preferred_language", "") or None
            cached = render_notification(obj, user=user, lang=lang)
            obj._rendered_notification = cached
        return cached

    def get_actor_email(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if not can_actor_view_email(actor=actor, topology_mode="managed"):
            return None
        actor_user = getattr(obj, "actor", None)
        return getattr(actor_user, "email", None)

    def get_target_url(self, obj):
        return self._rendered(obj).get("action_url")

    def get_title(self, obj):
        return self._rendered(obj).get("title")

    def get_body(self, obj):
        return self._rendered(obj).get("body") or []

    def get_action_url(self, obj):
        return self._rendered(obj).get("action_url")

    def get_action_label(self, obj):
        return self._rendered(obj).get("action_label")

    def get_entity(self, obj):
        if not obj.object_type or obj.object_id is None:
            return None
        return {"type": obj.object_type, "id": obj.object_id}
