from django.contrib import admin
from .models import Location


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "location_type", "is_active", "parent")
    list_filter = ("is_active", "location_type")
    search_fields = ("name", "slug")
    prepopulated_fields = {}
    readonly_fields = ("created_at", "updated_at")
