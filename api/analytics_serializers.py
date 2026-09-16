"""Serializers for analytics event ingestion."""
from rest_framework import serializers

from common.models import AnalyticsEvent


class AnalyticsEventIngestSerializer(serializers.Serializer):
    """Write-only payload for frontend/backend analytics tracking."""

    event_type = serializers.ChoiceField(choices=AnalyticsEvent.EventType.choices)
    path = serializers.CharField(required=False, allow_blank=True, max_length=1024)
    metadata = serializers.JSONField(required=False)
    status_code = serializers.IntegerField(required=False, min_value=100, max_value=599)
    error = serializers.JSONField(required=False)

    def validate_metadata(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("metadata must be a JSON object.")
        return value

    def validate(self, attrs):
        metadata = attrs.get("metadata")
        if metadata is None:
            attrs["metadata"] = {}

        path = attrs.get("path", "")
        if path:
            attrs["path"] = path.strip()

        return attrs
