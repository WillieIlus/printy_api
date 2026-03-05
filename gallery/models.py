"""Product Gallery models — categories and products."""
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.slug import AutoSlugMixin
from shops.models import Shop


class ProductCategory(AutoSlugMixin, models.Model):
    """Category for gallery products. shop=null means global category."""

    slug_source_field = "name"

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="gallery_categories",
        verbose_name=_("shop"),
        help_text=_("Null = global category."),
    )
    name = models.CharField(max_length=255, verbose_name=_("name"))
    slug = models.SlugField(
        max_length=100,
        verbose_name=_("slug"),
        help_text=_("Unique per shop or global."),
    )
    icon_svg_path = models.TextField(
        blank=True,
        default="",
        verbose_name=_("icon SVG path"),
    )
    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
    )

    class Meta:
        verbose_name = _("product category")
        verbose_name_plural = _("product categories")
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "slug"],
                name="gallery_category_shop_slug_unique",
            ),
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(shop__isnull=True),
                name="gallery_category_global_slug_unique",
            ),
        ]

    def get_slug_scope(self):
        return {"shop_id": self.shop_id} if self.shop_id else {"shop__isnull": True}

    def __str__(self):
        return self.name


class Product(AutoSlugMixin, models.Model):
    """Gallery product. shop=null for global products."""

    slug_source_field = "title"

    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.CASCADE,
        related_name="products",
        verbose_name=_("category"),
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="gallery_products",
        verbose_name=_("shop"),
        help_text=_("Null = global product."),
    )
    title = models.CharField(max_length=255, verbose_name=_("title"))
    slug = models.SlugField(max_length=100, verbose_name=_("slug"))
    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
    )
    preview_image = models.ImageField(
        upload_to="products/previews/",
        blank=True,
        null=True,
        verbose_name=_("preview image"),
    )
    dimensions_label = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("dimensions label"),
        help_text=_("e.g. 90 × 55 mm"),
    )
    weight_label = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("weight label"),
        help_text=_("e.g. 350gsm"),
    )
    is_popular = models.BooleanField(default=False, verbose_name=_("is popular"))
    is_best_value = models.BooleanField(default=False, verbose_name=_("is best value"))
    is_new = models.BooleanField(default=False, verbose_name=_("is new"))
    is_active = models.BooleanField(default=True, verbose_name=_("is active"))

    class Meta:
        verbose_name = _("gallery product")
        verbose_name_plural = _("gallery products")
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "slug"],
                name="gallery_product_shop_slug_unique",
            ),
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(shop__isnull=True),
                name="gallery_product_global_slug_unique",
            ),
        ]

    def get_slug_scope(self):
        return {"shop_id": self.shop_id} if self.shop_id else {"shop__isnull": True}

    def __str__(self):
        return self.title
