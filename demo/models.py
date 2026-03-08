"""
Demo calculator models — standalone pricing data for the landing page.
Editable via Django admin. No Shop FK — used for "no login" demo.
"""
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _


class DemoPaper(models.Model):
    """Paper stock for SHEET-mode demo products (sheet size, GSM, price per sheet)."""

    SHEET_SIZES = [
        ("A4", "A4"),
        ("A3", "A3"),
        ("SRA3", "SRA3"),
    ]
    PAPER_TYPES = [
        ("UNCOATED", "Uncoated"),
        ("COATED", "Coated"),
        ("GLOSS", "Gloss"),
    ]

    sheet_size = models.CharField(
        max_length=20,
        choices=SHEET_SIZES,
        verbose_name=_("sheet size"),
    )
    gsm = models.PositiveIntegerField(
        verbose_name=_("GSM"),
        help_text=_("Paper weight (grams per square metre)."),
    )
    paper_type = models.CharField(
        max_length=20,
        choices=PAPER_TYPES,
        default="GLOSS",
        verbose_name=_("paper type"),
    )
    selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("selling price per sheet"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
    )

    class Meta:
        ordering = ["sheet_size", "gsm"]
        verbose_name = _("demo paper")
        verbose_name_plural = _("demo papers")
        constraints = [
            models.UniqueConstraint(
                fields=["sheet_size", "gsm", "paper_type"],
                name="unique_demo_paper",
            )
        ]

    def __str__(self):
        return f"{self.sheet_size} {self.gsm}gsm {self.get_paper_type_display()}"


class DemoPrintingRate(models.Model):
    """Printing rate per sheet (simplex/duplex) for demo calculator."""

    SHEET_SIZES = [
        ("A4", "A4"),
        ("A3", "A3"),
        ("SRA3", "SRA3"),
    ]
    COLOR_MODES = [
        ("BW", "Black & White"),
        ("COLOR", "Color"),
    ]

    sheet_size = models.CharField(
        max_length=20,
        choices=SHEET_SIZES,
        verbose_name=_("sheet size"),
    )
    color_mode = models.CharField(
        max_length=10,
        choices=COLOR_MODES,
        default="COLOR",
        verbose_name=_("color mode"),
    )
    single_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("single-sided price per sheet"),
    )
    double_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("double-sided price per sheet"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
    )

    class Meta:
        ordering = ["sheet_size", "color_mode"]
        verbose_name = _("demo printing rate")
        verbose_name_plural = _("demo printing rates")
        constraints = [
            models.UniqueConstraint(
                fields=["sheet_size", "color_mode"],
                name="unique_demo_printing_rate",
            )
        ]

    def __str__(self):
        return f"{self.sheet_size} {self.get_color_mode_display()}"


class DemoFinishingRate(models.Model):
    """Finishing service rate for demo (lamination, binding, folding, etc.)."""

    CHARGE_UNITS = [
        ("PER_PIECE", "Per Piece"),
        ("PER_SHEET", "Per Sheet"),
        ("PER_SQM", "Per Square Meter"),
        ("FLAT", "Flat"),
    ]

    name = models.CharField(
        max_length=255,
        verbose_name=_("name"),
    )
    charge_unit = models.CharField(
        max_length=20,
        choices=CHARGE_UNITS,
        default="PER_PIECE",
        verbose_name=_("charge unit"),
    )
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("price"),
    )
    setup_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("setup fee"),
    )
    min_qty = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("min quantity"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
    )

    class Meta:
        ordering = ["name"]
        verbose_name = _("demo finishing rate")
        verbose_name_plural = _("demo finishing rates")

    def __str__(self):
        return self.name


class DemoMaterial(models.Model):
    """Large-format material (vinyl, banner) sold by SQM."""

    material_type = models.CharField(
        max_length=255,
        verbose_name=_("material type"),
    )
    unit = models.CharField(
        max_length=20,
        default="SQM",
        verbose_name=_("unit"),
    )
    selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("selling price per unit"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
    )

    class Meta:
        ordering = ["material_type"]
        verbose_name = _("demo material")
        verbose_name_plural = _("demo materials")

    def __str__(self):
        return self.material_type


class DemoProduct(models.Model):
    """
    Demo product template — business cards, flyers, booklets, etc.
    Admin-editable. Used by the landing page calculator.
    """

    PRICING_MODES = [
        ("SHEET", "Sheet (copies per sheet)"),
        ("LARGE_FORMAT", "Large Format (by area)"),
    ]
    SIDES = [
        ("SIMPLEX", "Single-sided"),
        ("DUPLEX", "Double-sided"),
    ]
    CATEGORIES = [
        ("business_cards", "Business Cards"),
        ("flyers", "Flyers"),
        ("booklets", "Booklets"),
        ("magazines", "Magazines"),
        ("notebooks", "Notebooks"),
        ("billboards", "Billboards"),
        ("rollup_banners", "Roll-up Banners"),
    ]

    name = models.CharField(
        max_length=255,
        verbose_name=_("name"),
    )
    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
    )
    category = models.CharField(
        max_length=50,
        choices=CATEGORIES,
        default="flyers",
        verbose_name=_("category"),
    )
    pricing_mode = models.CharField(
        max_length=20,
        choices=PRICING_MODES,
        default="SHEET",
        verbose_name=_("pricing mode"),
    )
    default_finished_width_mm = models.PositiveIntegerField(
        default=0,
        verbose_name=_("finished width (mm)"),
    )
    default_finished_height_mm = models.PositiveIntegerField(
        default=0,
        verbose_name=_("finished height (mm)"),
    )
    default_sides = models.CharField(
        max_length=10,
        choices=SIDES,
        default="DUPLEX",
        verbose_name=_("default sides"),
    )
    min_quantity = models.PositiveIntegerField(
        default=100,
        verbose_name=_("min quantity"),
    )
    default_sheet_size = models.CharField(
        max_length=20,
        blank=True,
        default="SRA3",
        verbose_name=_("default sheet size"),
    )
    copies_per_sheet = models.PositiveIntegerField(
        default=1,
        verbose_name=_("copies per sheet"),
        help_text=_("How many finished pieces fit on one sheet (imposition)."),
    )
    min_gsm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("min GSM"),
        help_text=_("Minimum paper weight allowed."),
    )
    max_gsm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("max GSM"),
        help_text=_("Maximum paper weight allowed."),
    )
    badge = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("badge"),
        help_text=_("Optional badge (e.g. Popular)."),
    )
    display_order = models.PositiveIntegerField(
        default=0,
        verbose_name=_("display order"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
    )

    finishing_options = models.ManyToManyField(
        DemoFinishingRate,
        through="DemoProductFinishingOption",
        related_name="demo_products",
        blank=True,
        verbose_name=_("finishing options"),
    )

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = _("demo product")
        verbose_name_plural = _("demo products")

    def __str__(self):
        return self.name


class DemoProductFinishingOption(models.Model):
    """Links demo product to finishing rate with optional price override."""

    product = models.ForeignKey(
        DemoProduct,
        on_delete=models.CASCADE,
        related_name="product_finishing_options",
    )
    finishing_rate = models.ForeignKey(
        DemoFinishingRate,
        on_delete=models.CASCADE,
        related_name="product_options",
    )
    is_default = models.BooleanField(
        default=False,
        verbose_name=_("is default"),
    )
    price_adjustment = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("price adjustment"),
        help_text=_("Override finishing price. Blank = use rate price."),
    )

    class Meta:
        ordering = ["product", "finishing_rate"]
        verbose_name = _("demo product finishing option")
        verbose_name_plural = _("demo product finishing options")
        constraints = [
            models.UniqueConstraint(
                fields=["product", "finishing_rate"],
                name="unique_demo_product_finishing",
            )
        ]

    def __str__(self):
        return f"{self.product.name} + {self.finishing_rate.name}"
