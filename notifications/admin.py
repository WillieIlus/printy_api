"""Notification admin."""
from django.contrib import admin
from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "notification_type", "actor", "object_type", "object_id", "read_at", "created_at"]
    list_filter = ["notification_type", "read_at"]
    search_fields = ["user__email", "message"]
    readonly_fields = ["created_at"]
