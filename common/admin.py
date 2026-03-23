from django.contrib import admin

from .models import AnalyticsEvent


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = [
        "event_type",
        "user",
        "path",
        "method",
        "status_code",
        "country",
        "city",
        "created_at",
    ]
    list_filter = ["event_type", "method", "status_code", "country", "created_at"]
    search_fields = ["path", "visitor_id", "session_key", "user__email", "referer"]
    readonly_fields = [field.name for field in AnalyticsEvent._meta.fields]
    ordering = ["-created_at"]
