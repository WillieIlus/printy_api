from django.contrib import admin
from django.utils.html import format_html

from .models import Imposition, Product, ProductCategory, ProductFinishingOption, ProductImage
from .models import BLEED_MM
from inventory.choices import SHEET_SIZE_DIMENSIONS


class ImpositionInline(admin.TabularInline):
    model = Imposition
    extra = 0
    fields = ["sheet_size", "copies_per_sheet", "is_default"]
    readonly_fields = ["copies_per_sheet"]


class ProductFinishingOptionInline(admin.TabularInline):
    model = ProductFinishingOption
    extra = 0
    fields = ["finishing_rate", "apply_to_sides", "is_default", "price_adjustment"]


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    fields = ["image", "is_primary", "display_order"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "shop",
        "pricing_mode",
        "standard_turnaround_hours",
        "rush_available",
        "min_gsm",
        "max_gsm",
        "lowest_price",
        "highest_price",
        "default_finished_width_mm",
        "default_finished_height_mm",
        "is_active",
    ]
    list_filter = ["pricing_mode", "is_active"]
    search_fields = ["name", "category__name"]
    inlines = [ImpositionInline, ProductFinishingOptionInline, ProductImageInline]
    readonly_fields = ["imposition_preview", "calculation_formula_display", "price_status_display"]

    fieldsets = (
        (None, {"fields": ("shop", "name", "description", "category", "slug", "pricing_mode", "is_active")}),
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
            "Price range (est.)",
            {"fields": ("lowest_price", "highest_price")},
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


@admin.register(Imposition)
class ImpositionAdmin(admin.ModelAdmin):
    list_display = ["product", "sheet_size", "copies_per_sheet_display", "is_default"]
    list_filter = ["sheet_size"]
    search_fields = ["product__name"]
    readonly_fields = ["copies_per_sheet"]

    def copies_per_sheet_display(self, obj):
        return obj.product.get_copies_per_sheet(obj.sheet_size) if obj.product_id else obj.copies_per_sheet

    copies_per_sheet_display.short_description = "Copies/sheet (auto)"


@admin.register(ProductFinishingOption)
class ProductFinishingOptionAdmin(admin.ModelAdmin):
    list_display = ["product", "finishing_rate", "apply_to_sides", "is_default", "price_adjustment"]
    list_filter = ["apply_to_sides", "is_default"]


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ["product", "is_primary", "display_order", "image"]
    list_filter = ["is_primary"]
    search_fields = ["product__name"]
