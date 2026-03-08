"""
Shop admin with inlines for shop-scoped resources.

TabularInline is formset-like: it renders multiple related rows in a table,
allowing add/edit/delete of Paper, FinishingRate, Material, Machine, etc.
directly on the Shop edit page. Yes, finishing rates should be inline—shops
typically have a small, fixed set of finishing options (lamination, cutting,
etc.) that are best managed alongside the shop.
"""
from django.contrib import admin

from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, Material, ServiceRate

from .models import Shop


class MachineInline(admin.TabularInline):
    model = Machine
    extra = 1
    fields = ["name", "machine_type", "max_width_mm", "max_height_mm", "min_gsm", "max_gsm", "is_active"]
    show_change_link = True
    verbose_name_plural = "Machines (set printing rates on each machine)"


class PaperInline(admin.TabularInline):
    model = Paper
    extra = 1
    fields = ["sheet_size", "gsm", "paper_type", "buying_price", "selling_price", "quantity_in_stock", "is_active", "is_default"]
    show_change_link = True
    verbose_name_plural = "Paper stock"


class FinishingRateInline(admin.TabularInline):
    """Formset-like: add/edit finishing options (lamination, cutting, etc.) per shop."""
    model = FinishingRate
    extra = 1
    fields = ["name", "charge_unit", "price", "double_side_price", "setup_fee", "min_qty", "is_active"]
    show_change_link = True
    verbose_name_plural = "Finishing rates"


class MaterialInline(admin.TabularInline):
    model = Material
    extra = 1
    fields = ["material_type", "unit", "buying_price", "selling_price", "is_active"]
    show_change_link = True
    verbose_name_plural = "Materials (large-format)"


class ServiceRateInline(admin.TabularInline):
    model = ServiceRate
    extra = 0
    fields = ["code", "name", "pricing_type", "price", "is_optional", "is_negotiable", "is_active"]
    show_change_link = True
    verbose_name_plural = "Service rates (design, delivery, etc.)"


class ProductInline(admin.TabularInline):
    model = Product
    extra = 0
    fields = [
        "name",
        "pricing_mode",
        "default_finished_width_mm",
        "default_finished_height_mm",
        "default_sides",
        "min_quantity",
        "lowest_price",
        "highest_price",
        "is_active",
    ]
    show_change_link = True
    verbose_name_plural = "Products"


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "owner", "currency", "is_active", "latitude", "longitude", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug", "owner__email"]
    inlines = [MachineInline, PaperInline, FinishingRateInline, MaterialInline, ServiceRateInline, ProductInline]
