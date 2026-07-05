from django.contrib import admin
from django.utils.html import format_html

from .models import Product, ProductCategory, ProductFinishingOption, ProductImage
from .models import BLEED_MM
from inventory.choices import SHEET_SIZE_DIMENSIONS


class ProductFinishingOptionInline(admin.TabularInline):
    model = ProductFinishingOption
    extra = 0
    fields = ["finishing_rate", "apply_to_sides", "is_default"]


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ["image", "is_primary", "display_order"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "pricing_mode",
        "standard_turnaround_hours",
        "rush_available",
        "min_gsm",
        "max_gsm",
        "default_finished_width_mm",
        "default_finished_height_mm",
        "is_active",
    ]
    list_filter = ["pricing_mode", "product_kind", "is_active"]
    search_fields = ["name", "category__name"]
    inlines = [ProductFinishingOptionInline, ProductImageInline]
    readonly_fields = ["imposition_preview", "calculation_formula_display", "price_status_display"]

    fieldsets = (
        (None, {"fields": ("name", "description", "category", "slug", "pricing_mode", "product_kind", "is_active")}),
        (
            "Dimensions",
            {
                "fields": (
                    "default_finished_width_mm",
                    "default_finished_height_mm",
                    "default_sheet_size",
                    "default_sides",
                    "min_quantity",
                    "min_width_mm",
                    "min_height_mm",
                    "max_width_mm",
                    "max_height_mm",
                ),
                "description": f"Bleed is {BLEED_MM}mm (used for auto imposition). min/max for size rules (e.g. business cards max A6).",
            },
        ),
        (
            "Paper constraints",
            {
                "fields": ("min_gsm", "max_gsm", "allowed_sheet_sizes", "allow_simplex", "allow_duplex"),
                "description": "e.g. business card 250–350 gsm; flyer 130–170 gsm. allowed_sheet_sizes: JSON list like [\"A4\",\"A3\",\"SRA3\"] or empty.",
            },
        ),
        (
            "Turnaround",
            {
                "fields": (
                    "turnaround_days",
                    "standard_turnaround_hours",
                    "rush_available",
                    "rush_turnaround_hours",
                    "queue_hours",
                    "buffer_hours",
                ),
            },
        ),
        (
            "Gallery display",
            {"fields": ("dimensions_label", "weight_label", "is_popular", "is_best_value", "is_new")},
        ),
        (
            "Booklet configuration",
            {
                "fields": (
                    "default_binding_type",
                    "booklet_min_pages",
                    "booklet_max_pages",
                    "booklet_page_multiple",
                    "saddle_stitch_recommended_max_pages",
                    "perfect_bind_recommended_min_pages",
                    "creep_warning_start_pages",
                ),
                "description": (
                    "Only relevant when product_kind = BOOKLET. "
                    "Thresholds are transparency signals used by the service layer for warnings and binding recommendations, "
                    "not hard limits."
                ),
                "classes": ["collapse"],
            },
        ),
        (
            "Calculated",
            {
                "fields": ("imposition_preview", "calculation_formula_display", "price_status_display"),
                "description": "Auto-calculated from dimensions.",
            },
        ),
    )

    def imposition_preview(self, obj):
        if not obj.pk or not obj.default_finished_width_mm or not obj.default_finished_height_mm:
            return "—"
        parts = []
        for sz, (sw, sh) in SHEET_SIZE_DIMENSIONS.items():
            cps = obj.get_copies_per_sheet(sz, sw, sh)
            parts.append(f"{sz}: {cps} up")
        return ", ".join(parts)

    imposition_preview.short_description = "Copies per sheet (auto)"

    def calculation_formula_display(self, obj):
        if not obj.pk:
            return "—"
        return format_html("<pre style='font-size:11px;'>{}</pre>", obj.get_calculation_formula())

    calculation_formula_display.short_description = "Calculation formula"

    def price_status_display(self, obj):
        if not obj.pk:
            return "—"
        from catalog.services import get_product_price_range
        result = get_product_price_range(obj)
        if result["can_calculate"]:
            return format_html(
                "From {} to {}",
                result["lowest_price"],
                result["highest_price"],
            )
        parts = ["<strong>Missing for price range:</strong><ul>"]
        for f in result["missing_fields"]:
            parts.append(f"<li><code>{f}</code></li>")
        parts.append("</ul>")
        return format_html("".join(parts))

    price_status_display.short_description = "Price range / missing fields"


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "slug"]
    list_filter = ["shop"]
    search_fields = ["name"]


@admin.register(ProductFinishingOption)
class ProductFinishingOptionAdmin(admin.ModelAdmin):
    list_display = ["product", "finishing_rate", "apply_to_sides", "is_default"]
    list_filter = ["apply_to_sides", "is_default"]


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ["product", "is_primary", "display_order", "image"]
    list_filter = ["is_primary"]
    search_fields = ["product__name"]
