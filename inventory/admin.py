from django.contrib import admin

from pricing.models import PrintingRate

from .models import Machine, Paper


class PrintingRateInline(admin.TabularInline):
    """Inline printing rates on Machine admin (one per sheet_size + color_mode)."""
    model = PrintingRate
    extra = 0
    fields = ["sheet_size", "color_mode", "single_price", "double_price", "is_active", "is_default"]


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "machine_type", "max_width_mm", "max_height_mm", "is_active"]
    list_filter = ["machine_type", "is_active"]
    search_fields = ["name"]
    inlines = [PrintingRateInline]


@admin.register(Paper)
class PaperAdmin(admin.ModelAdmin):
    list_display = [
        "display_name",
        "name",
        "sheet_size",
        "gsm",
        "category",
        "paper_type",
        "is_cover_stock",
        "is_insert_stock",
        "is_sticker_stock",
        "shop",
        "selling_price",
        "quantity_in_stock",
        "is_active",
        "is_default",
    ]
    list_filter = ["sheet_size", "category", "paper_type", "is_cover_stock", "is_insert_stock", "is_sticker_stock", "is_active", "is_default"]
    search_fields = ["name", "display_name", "sheet_size", "paper_type", "category"]
