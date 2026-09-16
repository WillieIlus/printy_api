from django.contrib import admin

from .models import WorkflowJob, WorkflowManager, WorkflowPrinter


@admin.register(WorkflowManager)
class WorkflowManagerAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "initials", "tag", "on_time", "hue")
    search_fields = ("name", "tag")


@admin.register(WorkflowPrinter)
class WorkflowPrinterAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "contact", "city", "verified", "rating", "jobs_done", "on_time")
    search_fields = ("name", "contact", "city")


@admin.register(WorkflowJob)
class WorkflowJobAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "title", "stage", "status", "custody", "value")
    list_filter = ("stage", "status", "custody")
    search_fields = ("code", "title")