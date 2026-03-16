"""
Production tracking admin.
"""
from django.contrib import admin
from .models import (
    Customer,
    ProductionOrder,
    JobProcess,
    Operator,
    PriceCard,
    PricingMethod,
    Process,
    ProductionMaterial,
    ProductionProduct,
    WastageStage,
)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "email", "phone"]
    list_filter = ["shop"]
    search_fields = ["name", "email"]


@admin.register(ProductionProduct)
class ProductionProductAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "catalog_product"]
    list_filter = ["shop"]


@admin.register(ProductionMaterial)
class ProductionMaterialAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "unit"]
    list_filter = ["shop", "unit"]


@admin.register(Process)
class ProcessAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "shop", "display_order"]
    list_filter = ["shop"]


@admin.register(Operator)
class OperatorAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "user", "is_active"]
    list_filter = ["shop", "is_active"]


@admin.register(PricingMethod)
class PricingMethodAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "shop"]
    list_filter = ["shop"]


@admin.register(WastageStage)
class WastageStageAdmin(admin.ModelAdmin):
    list_display = ["name", "shop", "process"]
    list_filter = ["shop"]


@admin.register(PriceCard)
class PriceCardAdmin(admin.ModelAdmin):
    list_display = ["process", "pricing_method", "default_rate", "material", "shop"]
    list_filter = ["shop"]


class JobProcessInline(admin.TabularInline):
    model = JobProcess
    extra = 0
    fields = [
        "process", "operator", "material", "pricing_method", "date",
        "qty_input", "waste", "default_rate", "applied_rate",
        "billable_units", "line_total", "notes",
    ]


@admin.register(ProductionOrder)
class ProductionOrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "title", "customer", "product", "quantity", "status", "shop", "due_date"]
    list_filter = ["shop", "status"]
    search_fields = ["order_number", "title"]
    inlines = [JobProcessInline]


@admin.register(JobProcess)
class JobProcessAdmin(admin.ModelAdmin):
    list_display = ["production_order", "process", "operator", "date", "qty_input", "waste", "line_total"]
    list_filter = ["process", "production_order__shop"]
