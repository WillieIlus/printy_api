from django.contrib import admin

from .models import (
    DemoPaper,
    DemoPrintingRate,
    DemoFinishingRate,
    DemoMaterial,
    DemoProduct,
    DemoProductFinishingOption,
)


@admin.register(DemoPaper)
class DemoPaperAdmin(admin.ModelAdmin):
    list_display = ["sheet_size", "gsm", "paper_type", "selling_price", "is_active"]
    list_filter = ["sheet_size", "paper_type", "is_active"]
    search_fields = ["sheet_size", "paper_type"]


@admin.register(DemoPrintingRate)
class DemoPrintingRateAdmin(admin.ModelAdmin):
    list_display = ["sheet_size", "color_mode", "single_price", "double_price", "is_active"]
    list_filter = ["sheet_size", "color_mode", "is_active"]


@admin.register(DemoFinishingRate)
class DemoFinishingRateAdmin(admin.ModelAdmin):
    list_display = ["name", "charge_unit", "price", "setup_fee", "min_qty", "is_active"]
    list_filter = ["charge_unit", "is_active"]
    search_fields = ["name"]


@admin.register(DemoMaterial)
class DemoMaterialAdmin(admin.ModelAdmin):
    list_display = ["material_type", "unit", "selling_price", "is_active"]
    list_filter = ["is_active"]


class DemoProductFinishingOptionInline(admin.TabularInline):
    model = DemoProductFinishingOption
    extra = 0
    autocomplete_fields = ["finishing_rate"]


@admin.register(DemoProduct)
class DemoProductAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "category",
        "pricing_mode",
        "size_display",
        "min_quantity",
        "copies_per_sheet",
        "gsm_range",
        "display_order",
        "is_active",
    ]
    list_filter = ["category", "pricing_mode", "is_active"]
    search_fields = ["name", "description"]
    ordering = ["display_order", "name"]
    inlines = [DemoProductFinishingOptionInline]
    fieldsets = (
        (None, {
            "fields": ("name", "description", "category", "badge", "display_order", "is_active"),
        }),
        ("Dimensions", {
            "fields": (
                "pricing_mode",
                "default_finished_width_mm",
                "default_finished_height_mm",
                "default_sides",
                "min_quantity",
                "default_sheet_size",
                "copies_per_sheet",
            ),
        }),
        ("Paper constraints", {
            "fields": ("min_gsm", "max_gsm"),
        }),
    )

    def size_display(self, obj):
        if obj.default_finished_width_mm and obj.default_finished_height_mm:
            return f"{obj.default_finished_width_mm}×{obj.default_finished_height_mm}mm"
        return "—"

    size_display.short_description = "Size"

    def gsm_range(self, obj):
        if obj.min_gsm and obj.max_gsm:
            return f"{obj.min_gsm}–{obj.max_gsm} gsm"
        if obj.min_gsm:
            return f"≥{obj.min_gsm} gsm"
        if obj.max_gsm:
            return f"≤{obj.max_gsm} gsm"
        return "—"

    gsm_range.short_description = "GSM"


@admin.register(DemoProductFinishingOption)
class DemoProductFinishingOptionAdmin(admin.ModelAdmin):
    list_display = ["product", "finishing_rate", "is_default", "price_adjustment"]
    list_filter = ["product", "finishing_rate"]
    autocomplete_fields = ["product", "finishing_rate"]
