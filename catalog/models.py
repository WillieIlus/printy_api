from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel
from inventory.choices import SheetSize, SHEET_SIZE_DIMENSIONS
from pricing.choices import FinishingSides, Sides
from pricing.models import FinishingRate
from shops.models import Shop

from .choices import PricingMode
from .imposition import pieces_per_sheet as imposition_pieces_per_sheet

# Standard bleed for imposition calculation (mm)
BLEED_MM = 3


class Product(TimeStampedModel):
    """Product in a shop's catalog."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="products",
        verbose_name=_("shop"),
        help_text=_("Shop that owns this product."),
    )
    name = models.CharField(
        max_length=255,
        default="",
        verbose_name=_("name"),
        help_text=_("Display name of the product."),
    )
    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
        help_text=_("Product description."),
    )
    category = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("category"),
        help_text=_("Product category."),
    )
    pricing_mode = models.CharField(
        max_length=20,
        choices=PricingMode.choices,
        default=PricingMode.SHEET,
        verbose_name=_("pricing mode"),
        help_text=_("Sheet or large format pricing."),
    )
    default_finished_width_mm = models.PositiveIntegerField(
        default=0,
        verbose_name=_("default finished width (mm)"),
        help_text=_("Default finished width in millimeters. Required for price range."),
    )
    default_finished_height_mm = models.PositiveIntegerField(
        default=0,
        verbose_name=_("default finished height (mm)"),
        help_text=_("Default finished height in millimeters. Required for price range."),
    )
    default_bleed_mm = models.PositiveIntegerField(
        default=BLEED_MM,
        verbose_name=_("bleed (mm)"),
        help_text=_("Bleed for imposition (default 3mm). Used to calculate copies per sheet."),
    )
    default_sides = models.CharField(
        max_length=10,
        choices=Sides.choices,
        default=Sides.SIMPLEX,
        verbose_name=_("default sides"),
        help_text=_("Default simplex or duplex."),
    )
    min_quantity = models.PositiveIntegerField(
        default=100,
        verbose_name=_("min quantity"),
        help_text=_("Minimum order quantity for price range calculation."),
    )
    default_sheet_size = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name=_("default sheet size"),
        help_text=_("Preferred sheet size for price range est. (e.g. SRA3). Blank = infer from shop."),
    )
    min_width_mm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("min width (mm)"),
        help_text=_("Min width for LARGE_FORMAT price range (defaults to default_finished_width_mm)."),
    )
    min_height_mm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("min height (mm)"),
        help_text=_("Min height for LARGE_FORMAT price range (defaults to default_finished_height_mm)."),
    )
    min_gsm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("min GSM"),
        help_text=_("Minimum paper grammage allowed (e.g. 250 for business cards)."),
    )
    max_gsm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("max GSM"),
        help_text=_("Maximum paper grammage allowed (e.g. 350 for business cards, 170 for flyers)."),
    )
    allow_simplex = models.BooleanField(
        default=True,
        verbose_name=_("allow simplex"),
        help_text=_("Allow single-sided printing."),
    )
    allow_duplex = models.BooleanField(
        default=True,
        verbose_name=_("allow duplex"),
        help_text=_("Allow double-sided printing."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether this product is active."),
    )
    lowest_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("lowest price (est.)"),
        help_text=_("Estimated lowest price for this product (display only)."),
    )
    highest_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("highest price (est.)"),
        help_text=_("Estimated highest price for this product (display only)."),
    )

    class Meta:
        ordering = ["shop", "name"]
        verbose_name = _("product")
        verbose_name_plural = _("products")

    def __str__(self):
        return f"{self.name} ({self.shop.name})"

    def get_primary_image(self):
        """Return the primary image, or the first image, or None."""
        img = self.images.filter(is_primary=True).first()
        if img:
            return img
        return self.images.order_by("display_order", "id").first()

    def clean(self):
        super().clean()
        if self.min_gsm is not None and self.max_gsm is not None:
            if self.min_gsm > self.max_gsm:
                raise ValidationError(
                    {"max_gsm": _("Max GSM must be >= min GSM.")}
                )

    def get_copies_per_sheet(self, sheet_size: str, sheet_width_mm: int = None, sheet_height_mm: int = None) -> int:
        """Compute copies per sheet from product dimensions. Uses imposition helper."""
        if sheet_width_mm is None or sheet_height_mm is None:
            dims = SHEET_SIZE_DIMENSIONS.get(sheet_size)
            if not dims:
                return 1
            sheet_width_mm, sheet_height_mm = dims
        return imposition_pieces_per_sheet(
            self.default_finished_width_mm,
            self.default_finished_height_mm,
            sheet_width_mm,
            sheet_height_mm,
            self.default_bleed_mm or BLEED_MM,
        )

    def get_calculation_formula(self) -> str:
        """Human-readable formula for price calculation."""
        if self.pricing_mode == PricingMode.SHEET:
            return (
                "SHEET: copies_per_sheet = auto from (width+6)×(height+6) on sheet. "
                "sheets = ceil(qty/copies_per_sheet). "
                "cost = paper.selling_price×sheets + PrintingRate×sheets + finishing."
            )
        if self.pricing_mode == PricingMode.LARGE_FORMAT:
            return (
                "LARGE_FORMAT: area_sqm = (width/1000)×(height/1000)×qty. "
                "cost = material.selling_price×area_sqm + finishing."
            )
        return "Set pricing_mode to SHEET or LARGE_FORMAT."


class Imposition(TimeStampedModel):
    """
    How many copies of a product fit on one sheet. E.g. 8 business cards per A4.
    Used for sheet cost calculation: sheets_needed = ceil(quantity / copies_per_sheet).
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="impositions",
        verbose_name=_("product"),
        help_text=_("Product this imposition applies to."),
    )
    sheet_size = models.CharField(
        max_length=20,
        choices=SheetSize.choices,
        default=SheetSize.A4,
        verbose_name=_("sheet size"),
        help_text=_("Paper sheet size (A4, A3, etc.)."),
    )
    copies_per_sheet = models.PositiveIntegerField(
        default=1,
        verbose_name=_("copies per sheet"),
        help_text=_("Number of product copies that fit on one sheet (e.g. 8 for business cards on A4)."),
    )
    is_default = models.BooleanField(
        default=False,
        verbose_name=_("is default"),
        help_text=_("Use this imposition by default for this product."),
    )

    class Meta:
        ordering = ["product", "sheet_size"]
        verbose_name = _("imposition")
        verbose_name_plural = _("impositions")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "sheet_size"],
                name="unique_product_sheet_imposition",
            )
        ]

    def __str__(self):
        return f"{self.product.name} on {self.sheet_size}: {self.copies_per_sheet} up"

    def save(self, *args, **kwargs):
        if self.product_id and self.sheet_size:
            self.copies_per_sheet = self.product.get_copies_per_sheet(self.sheet_size)
        super().save(*args, **kwargs)


class ProductFinishingOption(TimeStampedModel):
    """Links a product to a finishing rate with optional price adjustment."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="finishing_options",
        verbose_name=_("product"),
        help_text=_("Product this option applies to."),
    )
    finishing_rate = models.ForeignKey(
        FinishingRate,
        on_delete=models.CASCADE,
        related_name="product_options",
        verbose_name=_("finishing rate"),
        help_text=_("Finishing rate for this option."),
    )
    is_default = models.BooleanField(
        default=False,
        verbose_name=_("is default"),
        help_text=_("Whether this is the default finishing option."),
    )
    price_adjustment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("price adjustment"),
        help_text=_("Optional price adjustment for this finishing."),
    )
    apply_to_sides = models.CharField(
        max_length=10,
        choices=FinishingSides.choices,
        default=FinishingSides.BOTH,
        verbose_name=_("apply to sides"),
        help_text=_("Single-sided, double-sided, or both (follows print sides)."),
    )

    class Meta:
        ordering = ["product", "finishing_rate"]
        verbose_name = _("product finishing option")
        verbose_name_plural = _("product finishing options")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "finishing_rate"],
                name="unique_product_finishing",
            )
        ]

    def __str__(self):
        return f"{self.product.name} + {self.finishing_rate.name}"

    def clean(self):
        super().clean()
        if self.product_id and self.finishing_rate_id:
            if self.product.shop_id != self.finishing_rate.shop_id:
                raise ValidationError(
                    "Product and finishing rate must belong to the same shop."
                )


class ProductImage(TimeStampedModel):
    """Multiple images per product. Optional; products may have zero or more images."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name=_("product"),
        help_text=_("Product this image belongs to."),
    )
    image = models.ImageField(
        upload_to="products/",
        verbose_name=_("image"),
        help_text=_("Product image."),
    )
    is_primary = models.BooleanField(
        default=False,
        verbose_name=_("is primary"),
        help_text=_("Use as main/preview image for cards and listings."),
    )
    display_order = models.PositiveIntegerField(
        default=0,
        verbose_name=_("display order"),
        help_text=_("Order for display (lower = first)."),
    )

    class Meta:
        ordering = ["product", "display_order", "id"]
        verbose_name = _("product image")
        verbose_name_plural = _("product images")

    def __str__(self):
        return f"{self.product.name} – image #{self.id}"
