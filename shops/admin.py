"""Shop admin with canonical shop-scoped resource inlines."""
from django.contrib import admin

from inventory.models import Machine, Paper
from pricing.models import FinishingRate

from .models import Shop


class MachineInline(admin.TabularInline):
    model = Machine
    extra = 1
    fields = ["name", "machine_type", "max_width_mm", "max_height_mm", "min_gsm", "max_gsm", "is_active"]
    show_change_link = True


class PaperInline(admin.TabularInline):
    model = Paper
    extra = 1
    fields = ["display_name", "sheet_size", "gsm", "category", "paper_type", "buying_price", "selling_price", "is_active", "is_default"]
    show_change_link = True


class FinishingRateInline(admin.TabularInline):
    model = FinishingRate
    extra = 1
    fields = ["name", "charge_unit", "billing_basis", "side_mode", "price", "double_side_price", "is_active"]
    show_change_link = True


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "owner", "currency", "is_active", "timezone", "opening_time", "closing_time", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug", "owner__email"]
    inlines = [MachineInline, PaperInline, FinishingRateInline]
