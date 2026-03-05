from django.contrib import admin
from django.utils.html import format_html

from .models import Product, ProductCategory


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "shop", "product_count"]
    list_filter = ["shop"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {}

    def product_count(self, obj):
        return obj.products.count()

    product_count.short_description = "Products"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "category", "shop", "is_active", "is_popular", "is_best_value", "is_new"]
    list_filter = ["is_active", "is_popular", "is_best_value", "is_new", "category", "shop"]
    search_fields = ["title", "slug"]
    readonly_fields = ["preview_image_display"]

    def preview_image_display(self, obj):
        if obj.preview_image:
            return format_html('<img src="{}" width="200" />', obj.preview_image.url)
        return "—"

    preview_image_display.short_description = "Preview"
