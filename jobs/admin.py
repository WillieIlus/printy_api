"""JobShare admin."""
from django.contrib import admin
from django.utils.html import format_html

from jobs.models import JobClaim, JobNotification, JobRequest


@admin.register(JobRequest)
class JobRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "status", "created_by", "created_at", "public_link"]
    list_filter = ["status"]
    search_fields = ["title", "location"]
    readonly_fields = ["public_token", "created_at", "updated_at"]

    def public_link(self, obj):
        if obj.public_token:
            from django.conf import settings
            url = f"{getattr(settings, 'FRONTEND_URL', '')}/job/{obj.public_token}"
            return format_html('<a href="{}" target="_blank">{}</a>', url, "View")
        return "—"
    public_link.short_description = "Public link"


@admin.register(JobClaim)
class JobClaimAdmin(admin.ModelAdmin):
    list_display = ["id", "job_request", "claimed_by", "status", "price_offered", "created_at"]
    list_filter = ["status"]


@admin.register(JobNotification)
class JobNotificationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "job_request", "read_at", "created_at"]
    list_filter = ["read_at"]
