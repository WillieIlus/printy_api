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
        "duplex_surcharge",
        "duplex_surcharge_enabled",
        "duplex_surcharge_min_gsm",
        "is_active",
        "is_default",
    ]
    list_filter = ["sheet_size", "color_mode", "duplex_surcharge_enabled", "is_active", "is_default"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "machine",
                    "sheet_size",
                    "color_mode",
                    "is_active",
                    "is_default",
                )
            },
        ),
        (
            "Printing Pricing",
            {
                "fields": (
                    "single_price",
                    "double_price",
                    "duplex_surcharge_enabled",
                    "duplex_surcharge",
                    "duplex_surcharge_min_gsm",
                ),
                "description": "Set the print charge per side. Leave duplex override blank to calculate duplex as one side + back side + optional duplex surcharge.",
            },
        ),
    )


@admin.register(FinishingRate)
class FinishingRateAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "shop",
        "category",
        "charge_unit",
        "billing_basis",
        "side_mode",
        "thickness_microns",
        "is_single_sided_only",
        "price",
        "double_side_price",
        "minimum_charge",
        "setup_fee",
        "is_active",
    ]
    list_filter = ["charge_unit", "billing_basis", "side_mode", "is_active", "category"]
    search_fields = ["name", "slug"]
    autocomplete_fields = ["category"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "shop",
                    "name",
                    "slug",
                    "category",
                    "charge_unit",
                    "billing_basis",
                    "side_mode",
                    "is_active",
                )
            },
        ),
        (
            "Pricing",
            {
                "fields": (
                    "price",
                    "double_side_price",
                    "setup_fee",
                    "min_qty",
                    "minimum_charge",
                    "display_unit_label",
                    "help_text",
                ),
                "description": "Lamination should use per-sheet billing. One side uses the base rate, and both sides can use 2x the base rate or an optional both-side rate. Use flat_per_job, flat_per_group, or flat_per_line for flat logic.",
            },
        ),
        (
            "Lamination",
            {
                "fields": ("thickness_microns", "is_single_sided_only"),
                "description": "Use these only for lamination-like finishings. Legacy records are still accepted, but new setup should use simple per-sheet billing.",
                "classes": ("collapse",),
            },
        ),
        (
            "Targeting",
            {
                "fields": ("applies_to_product_types",),
                "classes": ("collapse",),
            },
        ),
    )


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ["material_type", "shop", "production_size", "unit", "selling_price", "print_price_per_sqm", "is_active"]
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
