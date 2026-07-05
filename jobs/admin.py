"""Admin registrations for canonical managed job models."""
from django.contrib import admin

from jobs.models import JobAssignment, JobFile, ManagedJob, ManagedJobPayout, JobStatusEvent


@admin.register(ManagedJob)
class ManagedJobAdmin(admin.ModelAdmin):
    list_display = ["managed_reference", "title", "assigned_shop", "status", "payment_status", "assignment_status", "created_at"]
    list_filter = ["status", "payment_status", "assignment_status", "assigned_shop"]
    search_fields = ["managed_reference", "title"]


@admin.register(JobAssignment)
class JobAssignmentAdmin(admin.ModelAdmin):
    list_display = ["managed_job", "assigned_shop", "status", "due_at", "created_at"]
    list_filter = ["status", "assigned_shop"]


@admin.register(ManagedJobPayout)
class ManagedJobPayoutAdmin(admin.ModelAdmin):
    list_display = ["managed_job", "recipient_role", "recipient", "amount", "status", "released_at"]
    list_filter = ["recipient_role", "status", "released_at"]
    search_fields = ["managed_job__managed_reference", "recipient__email", "release_reference"]
    readonly_fields = ["created_at", "updated_at", "released_at"]


@admin.register(JobFile)
class JobFileAdmin(admin.ModelAdmin):
    list_display = ["managed_job", "original_filename", "file_type", "visibility", "status", "created_at"]
    list_filter = ["file_type", "visibility", "status"]


@admin.register(JobStatusEvent)
class JobStatusEventAdmin(admin.ModelAdmin):
    list_display = ["managed_job", "event_type", "actor", "created_at"]
    list_filter = ["event_type"]
