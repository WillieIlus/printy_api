from django.contrib import admin

from .models import (
    FinishingRate,
    PlatformFeePolicy,
    PrintingRate,
    QuantityPricingTier,
    ShopPricingSettings,
    SetupCostPolicy,
    VolumeDiscount,
    WastePolicy,
)


@admin.register(ShopPricingSettings)
class ShopPricingSettingsAdmin(admin.ModelAdmin):
    list_display = [
        "shop",
        "is_active",
    ]
    list_filter = ["is_active"]
    search_fields = ["shop__name", "shop__slug"]


@admin.register(PlatformFeePolicy)
class PlatformFeePolicyAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "policy_version",
        "currency",
        "is_active",
        "under_production_threshold",
        "under_production_fee_rate",
        "standard_production_fee_rate",
        "standard_markup_fee_rate",
        "high_markup_threshold",
        "high_markup_fee_rate",
        "high_production_threshold",
        "high_production_fee_rate",
        "high_production_markup_fee_rate",
        "maximum_manager_markup_multiple",
        "effective_from",
        "updated_at",
    ]
    list_filter = ["is_active", "currency", "policy_version"]
    search_fields = ["name"]


@admin.register(WastePolicy)
class WastePolicyAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "is_active",
        "fixed_waste_sheets",
        "variable_waste_rate",
        "minimum_billable_sheets",
        "updated_at",
    ]
    list_filter = ["is_active"]
    search_fields = ["name"]


@admin.register(SetupCostPolicy)
class SetupCostPolicyAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "is_active",
        "setup_minutes",
        "labor_rate_per_hour",
        "machine_setup_fee",
        "admin_handling_fee",
        "file_check_fee",
        "updated_at",
    ]
    list_filter = ["is_active"]
    search_fields = ["name"]


@admin.register(QuantityPricingTier)
class QuantityPricingTierAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "is_active",
        "min_sheets",
        "max_sheets",
        "multiplier",
        "minimum_order_floor",
        "updated_at",
    ]
    list_filter = ["is_active"]
    search_fields = ["name"]


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
    list_filter = ["charge_unit", "billing_basis", "side_mode", "is_active"]
    search_fields = ["name", "slug"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "shop",
                    "name",
                    "slug",
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


@admin.register(VolumeDiscount)
class VolumeDiscountAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "min_quantity", "discount_percent", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name"]
