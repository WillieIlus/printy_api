from django.contrib import admin

from .models import FinishingCategory, FinishingRate, Material, PrintingRate, ServiceRate, ServiceRateTier, VolumeDiscount


@admin.register(FinishingCategory)
class FinishingCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "description"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(PrintingRate)
class PrintingRateAdmin(admin.ModelAdmin):
    list_display = [
        "machine",
        "sheet_size",
        "color_mode",
        "single_price",
        "double_price",
        "is_active",
        "is_default",
    ]
    list_filter = ["sheet_size", "color_mode", "is_active", "is_default"]


@admin.register(FinishingRate)
class FinishingRateAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "shop",
        "category",
        "charge_unit",
        "thickness_microns",
        "is_single_sided_only",
        "price",
        "double_side_price",
        "setup_fee",
        "is_active",
    ]
    list_filter = ["charge_unit", "is_active", "category"]
    search_fields = ["name"]
    autocomplete_fields = ["category"]
    fieldsets = (
        (None, {"fields": ("shop", "name", "category", "charge_unit", "is_active")}),
        (
            "Pricing",
            {
                "fields": ("price", "double_side_price", "setup_fee", "min_qty"),
                "description": "double_side_price: for lamination etc. when applied to both sides. Blank = 2× single.",
            },
        ),
        (
            "Lamination",
            {
                "fields": ("thickness_microns", "is_single_sided_only"),
                "description": "thickness_microns: e.g. 12, 25, 50. is_single_sided_only: can only apply to one side.",
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["material_type", "shop", "production_size", "unit", "selling_price", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["material_type"]
    autocomplete_fields = ["production_size"]


class ServiceRateTierInline(admin.TabularInline):
    model = ServiceRateTier
    extra = 0
    fields = ["min_km", "max_km", "price"]


@admin.register(ServiceRate)
class ServiceRateAdmin(admin.ModelAdmin):
    list_display = [
        "code",
        "name",
        "shop",
        "pricing_type",
        "price",
        "is_optional",
        "is_negotiable",
        "is_active",
    ]
    list_filter = ["pricing_type", "code", "is_active"]
    search_fields = ["name", "code"]
    inlines = [ServiceRateTierInline]
    fieldsets = (
        (None, {"fields": ("shop", "code", "name", "pricing_type", "is_active")}),
        (
            "Pricing",
            {
                "fields": ("price", "is_optional", "is_negotiable"),
                "description": "price: for FIXED. Tiers: for TIERED_DISTANCE.",
            },
        ),
    )


@admin.register(ServiceRateTier)
class ServiceRateTierAdmin(admin.ModelAdmin):
    list_display = ["service_rate", "min_km", "max_km", "price"]
    list_filter = ["service_rate__code"]


@admin.register(VolumeDiscount)
class VolumeDiscountAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "min_quantity", "discount_percent", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name"]
