from rest_framework import serializers

from .models import ContactMessage


class ContactMessageSerializer(serializers.ModelSerializer):
    """Validation mirrors the frontend's own rules (message >= 8 chars)."""

    topic = serializers.ChoiceField(
        choices=ContactMessage.Topic.choices,
        required=False,
        default=ContactMessage.Topic.QUOTE,
    )

    class Meta:
        model = ContactMessage
        fields = ["id", "topic", "name", "email", "message", "created_at"]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {
            "name": {"min_length": 2, "trim_whitespace": True, "max_length": 120},
            "message": {"min_length": 8, "trim_whitespace": True, "max_length": 5000},
        }