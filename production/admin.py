"""Admin registration for the temporarily retained production order anchor."""
from django.contrib import admin

from .models import ProductionOrder


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "title", "quantity", "status", "shop", "due_date"]
    list_filter = ["shop", "status"]
    search_fields = ["order_number", "title"]
