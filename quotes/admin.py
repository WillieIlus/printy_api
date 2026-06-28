from django.contrib import admin
from django.utils.html import format_html

from .models import (
    CalculatorDraft,
    ProductionOption,
    QuoteFinancialSplit,
    QuoteItem,
    QuoteItemFinishing,
    QuoteRequest,
    QuoteRequestAttachment,
    QuoteShareLink,
    Quote,
    QuoteAttachment,
)
from .services import (
    calculate_quote_item,
    get_quote_item_calculation_description,
    get_quote_item_missing_fields,
)


def _price_diagnostic(item):
    """Show missing fields or calculation when price is 0."""
    if not item.pk:
        return ""
    unit_price, line_total = calculate_quote_item(item, force=True)
    if line_total and line_total > 0:
        return format_html(
            "<strong>OK</strong> — unit: {}, total: {}",
            unit_price,
            line_total,
        )
    missing = get_quote_item_missing_fields(item)
    if missing:
        parts = ["<strong>Fill to get price:</strong><ul>"]
        for model_label, field_name in missing:
            parts.append(f"<li><code>{model_label}.{field_name}</code></li>")
        parts.append("</ul>")
        return format_html("".join(parts))
    return ""


def _calculation_help(item):
    """Show calculation formula for this item."""
    if not item.pk:
        return "Save the item first to see calculation."
    desc = get_quote_item_calculation_description(item)
    return format_html("<pre style='font-size:11px;'>{}</pre>", desc)


class QuoteItemInline(admin.TabularInline):
    model = QuoteItem
    extra = 0
    fields = [
        "product",
        "quantity",
        "pricing_mode",
        "paper",
        "chosen_width_mm",
        "chosen_height_mm",
        "sides",
        "color_mode",
        "machine",
        "special_instructions",
        "unit_price",
        "line_total",
        "price_diagnostic",
        "calculation_help",
    ]
    readonly_fields = ["unit_price", "line_total", "price_diagnostic", "calculation_help"]

    def price_diagnostic(self, obj):
        return _price_diagnostic(obj)

    price_diagnostic.short_description = "Price status (missing fields)"

    def calculation_help(self, obj):
        return _calculation_help(obj)

    calculation_help.short_description = "Calculation formula"


class QuoteItemFinishingInline(admin.TabularInline):
    model = QuoteItemFinishing
    extra = 0
    fields = ["finishing_rate", "apply_to_sides", "coverage_qty", "price_override"]


class QuoteRequestAttachmentInline(admin.TabularInline):
    model = QuoteRequestAttachment
    extra = 0
    fields = ["file", "name"]


@admin.register(QuoteRequest)
class QuoteRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "shop", "assigned_manager", "created_by", "customer_name", "status", "delivery_preference", "created_at"]
    list_filter = ["shop", "assigned_manager", "status", "delivery_preference"]
    search_fields = ["customer_name", "customer_email", "request_reference"]
    inlines = [QuoteItemInline, QuoteRequestAttachmentInline]
    fieldsets = (
        (None, {"fields": ("shop", "assigned_manager", "created_by", "customer_name", "customer_email", "customer_phone", "status", "notes")}),
        ("Delivery", {"fields": ("delivery_preference", "delivery_address")}),
    )

    def save_formset(self, request, form, formset, change):
        """Save inline items, then recalculate unit_price/line_total for each."""
        super().save_formset(request, form, formset, change)
        if formset.model == QuoteItem and form.instance.pk:
            # Recalculate all items (force=True to always recalc in admin)
            for item in form.instance.items.prefetch_related(
                "paper", "machine", "finishings__finishing_rate", "product"
            ):
                unit_price, line_total = calculate_quote_item(item, force=True)
                QuoteItem.objects.filter(pk=item.pk).update(
                    unit_price=unit_price, line_total=line_total
                )


@admin.register(QuoteItem)
class QuoteItemAdmin(admin.ModelAdmin):
    list_display = ["id", "quote_request", "product", "quantity", "unit_price", "line_total"]
    list_filter = ["quote_request__shop"]
    inlines = [QuoteItemFinishingInline]
    readonly_fields = ["price_diagnostic", "calculation_help"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "quote_request",
                    "product",
                    "quantity",
                    "pricing_mode",
                    "special_instructions",
                )
            },
        ),
        (
            "SHEET mode (paper products)",
            {
                "fields": ("paper", "machine", "sides", "color_mode"),
                "description": "Paper.selling_price + PrintingRate (machine+sheet_size+color_mode). "
                "Imposition: copies per sheet from catalog.Imposition.",
            },
        ),
        (
            "Booklet",
            {
                "fields": ("input_pages", "normalized_pages", "binding_type"),
                "description": "Booklet-specific fields. Leave blank for flat jobs.",
                "classes": ["collapse"],
            },
        ),
        (
            "Calculated",
            {
                "fields": ("unit_price", "line_total", "price_diagnostic", "calculation_help"),
                "description": "Auto-calculated on save. If 0, see 'Price status' for missing fields.",
            },
        ),
    )

    def price_diagnostic(self, obj):
        return _price_diagnostic(obj)

    price_diagnostic.short_description = "Price status (missing fields)"

    def calculation_help(self, obj):
        return _calculation_help(obj)

    calculation_help.short_description = "Calculation formula"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        unit_price, line_total = calculate_quote_item(obj, force=True)
        obj.unit_price = unit_price
        obj.line_total = line_total
        obj.save(update_fields=["unit_price", "line_total"])


@admin.register(CalculatorDraft)
class CalculatorDraftAdmin(admin.ModelAdmin):
    list_display = ["id", "draft_reference", "user", "calculator_context", "intent", "status", "created_at"]
    list_filter = ["calculator_context", "intent", "status", "created_at"]
    search_fields = ["draft_reference", "title", "user__email"]
    readonly_fields = ["draft_reference", "created_at", "updated_at"]


class QuoteAttachmentInline(admin.TabularInline):
    model = QuoteAttachment
    extra = 0
    fields = ["file", "name"]


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ["id", "quote_request", "shop", "status", "total", "turnaround_hours", "turnaround_days", "turnaround_label", "revision_number", "sent_at", "created_at"]
    list_filter = ["shop", "status"]
    search_fields = ["quote_reference", "quote_request__customer_name", "quote_request__customer_email"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [QuoteAttachmentInline]


@admin.register(ProductionOption)
class ProductionOptionAdmin(admin.ModelAdmin):
    list_display = ["quote_request", "shop", "production_cost", "status", "score", "created_by", "created_at"]
    list_filter = ["status", "created_at"]
    search_fields = ["quote_request__customer_name", "shop__name"]
    autocomplete_fields = ["quote_request", "shop", "created_by"]


@admin.register(QuoteFinancialSplit)
class QuoteFinancialSplitAdmin(admin.ModelAdmin):
    list_display = [
        "quote",
        "production_cost",
        "broker_client_price",
        "printy_fee",
        "shop_payout",
        "broker_payout",
        "client_total",
        "calculated_at",
    ]
    readonly_fields = [
        "production_cost",
        "broker_client_price",
        "gross_margin",
        "printer_side_fee",
        "broker_margin_fee",
        "printy_fee",
        "shop_payout",
        "broker_payout",
        "client_total",
        "max_allowed_client_price",
        "applied_markup_multiple",
        "calculated_at",
    ]
    autocomplete_fields = ["quote", "policy_used", "production_option"]


@admin.register(QuoteShareLink)
class QuoteShareLinkAdmin(admin.ModelAdmin):
    list_display = ["id", "quote", "token", "created_by", "created_at", "expires_at"]
    list_filter = ["created_at"]
    readonly_fields = ["token", "created_at"]
    search_fields = ["token"]
