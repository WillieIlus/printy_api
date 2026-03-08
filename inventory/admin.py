from django.contrib import admin

from pricing.models import PrintingRate

from .models import FinalPaperSize, Machine, Paper, ProductionPaperSize


@admin.register(ProductionPaperSize)
class ProductionPaperSizeAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "width_mm", "height_mm"]
    search_fields = ["code", "name"]
    ordering = ["code"]


@admin.register(FinalPaperSize)
class FinalPaperSizeAdmin(admin.ModelAdmin):
    list_display = ["name", "width_mm", "height_mm"]
    search_fields = ["name"]
    ordering = ["width_mm", "height_mm"]


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
        "sheet_size",
        "production_size",
        "gsm",
        "paper_type",
        "shop",
        "selling_price",
        "quantity_in_stock",
        "is_active",
        "is_default",
    ]
    list_filter = ["sheet_size", "paper_type", "is_active", "is_default"]
    search_fields = ["sheet_size", "paper_type"]
    autocomplete_fields = ["production_size"]
