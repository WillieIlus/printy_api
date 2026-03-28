from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel
from inventory.choices import SheetSize
from inventory.models import Machine, ProductionPaperSize
from shops.models import Shop

from .choices import (
    ChargeUnit,
    ColorMode,
    FinishingBillingBasis,
    FinishingSideMode,
    ServiceCode,
    ServicePricingType,
    Sides,
)


class FinishingCategory(TimeStampedModel):
    """Category for finishing services (e.g. Lamination, Binding, Folding)."""

    name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_("name"),
        help_text=_("Category name, e.g. Lamination, Binding."),
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        verbose_name=_("slug"),
        help_text=_("URL-friendly identifier."),
    )
    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
        help_text=_("Optional description of this category."),
    )

    class Meta:
        ordering = ["name"]
        verbose_name = _("finishing category")
        verbose_name_plural = _("finishing categories")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class PrintingRate(TimeStampedModel):
    """
    Printing rate per machine, sheet size, color mode.
    Real print shop pricing: single_price (simplex) and double_price (duplex) per sheet.
    Shop implied via machine.
    """

    machine = models.ForeignKey(
        Machine,
        on_delete=models.CASCADE,
        related_name="printing_rates",
        verbose_name=_("machine"),
        help_text=_("Machine this rate applies to."),
    )
    sheet_size = models.CharField(
        max_length=20,
        choices=SheetSize.choices,
        default=SheetSize.A4,
        verbose_name=_("sheet size"),
        help_text=_("Sheet size this rate applies to."),
    )
    color_mode = models.CharField(
        max_length=10,
        choices=ColorMode.choices,
        default=ColorMode.BW,
        verbose_name=_("color mode"),
        help_text=_("Black & white or color printing."),
    )
    single_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("single price"),
        help_text=_("Charge per sheet for simplex (1-sided) printing."),
    )
    double_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("double price"),
        help_text=_("Charge per sheet for duplex (2-sided) printing."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether this rate is active."),
    )
    is_default = models.BooleanField(
        default=False,
        verbose_name=_("is default"),
        help_text=_(
            "Use this rate as the main rate for the price list when no specific rate is specified. "
            "One default per machine."
        ),
    )

    class Meta:
        ordering = ["machine", "sheet_size", "color_mode"]
        verbose_name = _("printing rate")
        verbose_name_plural = _("printing rates")
        constraints = [
            models.UniqueConstraint(
                fields=["machine", "sheet_size", "color_mode"],
                name="unique_machine_sheet_color",
            ),
            models.UniqueConstraint(
                fields=["machine"],
                condition=models.Q(is_default=True),
                name="unique_machine_default_printing_rate",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.is_default and self.machine_id:
            PrintingRate.objects.filter(machine_id=self.machine_id).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.machine.name} - {self.sheet_size} {self.get_color_mode_display()}"

    def get_price_for_sides(self, sides):
        """Return single_price or double_price based on sides (SIMPLEX/DUPLEX)."""
        if sides == Sides.DUPLEX:
            return self.double_price
        return self.single_price

    @classmethod
    def resolve(cls, machine, sheet_size, color_mode, sides):
        """
        Resolve PrintingRate and return price for given sides.
        Order: 1) exact match (machine, sheet_size, color_mode), 2) default rate when sheet_size matches.
        """
        rate = cls.objects.filter(
            machine=machine,
            sheet_size=sheet_size,
            color_mode=color_mode,
            is_active=True,
        ).first()
        if rate:
            return rate, rate.get_price_for_sides(sides)
        # Fallback: use machine's default rate when sheet_size matches
        default_rate = cls.objects.filter(
            machine=machine,
            is_default=True,
            is_active=True,
        ).first()
        if default_rate and default_rate.sheet_size == sheet_size:
            return default_rate, default_rate.get_price_for_sides(sides)
        return None, None


class FinishingRate(TimeStampedModel):
    """Finishing service rate for a shop."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="finishing_rates",
        verbose_name=_("shop"),
        help_text=_("Shop that owns this finishing rate."),
    )
    category = models.ForeignKey(
        FinishingCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finishing_rates",
        verbose_name=_("category"),
        help_text=_("Finishing category (e.g. Lamination, Binding)."),
    )
    name = models.CharField(
        max_length=255,
        default="",
        verbose_name=_("name"),
        help_text=_("Display name of the finishing service."),
    )
    slug = models.SlugField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("slug"),
        help_text=_("Stable frontend/backend key for this finishing rule."),
    )
    thickness_microns = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("thickness (µm)"),
        help_text=_("Lamination thickness in microns (e.g. 12, 25, 50)."),
    )
    is_single_sided_only = models.BooleanField(
        default=False,
        verbose_name=_("single-sided only"),
        help_text=_("If true, this finishing can only be applied to one side (e.g. single-sided lamination)."),
    )
    charge_unit = models.CharField(
        max_length=20,
        choices=ChargeUnit.choices,
        default=ChargeUnit.PER_PIECE,
        verbose_name=_("charge unit"),
        help_text=_("How this finishing is charged (per piece, per side, per sqm, flat)."),
    )
    billing_basis = models.CharField(
        max_length=30,
        choices=FinishingBillingBasis.choices,
        default=FinishingBillingBasis.PER_PIECE,
        verbose_name=_("billing basis"),
        help_text=_("Canonical billing basis used by the pricing engine."),
    )
    side_mode = models.CharField(
        max_length=30,
        choices=FinishingSideMode.choices,
        default=FinishingSideMode.IGNORE_SIDES,
        verbose_name=_("side mode"),
        help_text=_("Whether rate multiplies by selected finishing sides."),
    )
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("price"),
        help_text=_("Price per charge unit (single-sided for lamination, etc.)."),
    )
    double_side_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("double-side price"),
        help_text=_("Price when applied to both sides (e.g. double-sided lamination). Blank = 2× single."),
    )
    setup_fee = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("setup fee"),
        help_text=_("Optional one-time setup fee."),
    )
    min_qty = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("min quantity"),
        help_text=_("Minimum quantity for this rate."),
    )
    minimum_charge = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("minimum charge"),
        help_text=_("Optional minimum charge after rule calculation."),
    )
    applies_to_product_types = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("applies to product types"),
        help_text=_("Optional list of product/category/pricing mode keys."),
    )
    display_unit_label = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("display unit label"),
        help_text=_("Optional UI label like 'per sheet per side'."),
    )
    help_text = models.TextField(
        blank=True,
        default="",
        verbose_name=_("help text"),
        help_text=_("Optional frontend-facing pricing explanation."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether this rate is active."),
    )

    class Meta:
        ordering = ["shop", "name"]
        verbose_name = _("finishing rate")
        verbose_name_plural = _("finishing rates")

    def __str__(self):
        return f"{self.name} ({self.shop.name})"

    def is_lamination_rule(self) -> bool:
        category_name = (getattr(self.category, "name", "") or "").strip().lower()
        name = (self.name or "").strip().lower()
        slug = (self.slug or "").strip().lower()
        return bool(
            self.thickness_microns
            or "lamination" in category_name
            or "lamination" in name
            or "lamination" in slug
        )

    def clean(self):
        super().clean()

        errors = {}
        per_side_sheet_unit = self.charge_unit == ChargeUnit.PER_SIDE_PER_SHEET
        flat_basis_values = {
            FinishingBillingBasis.FLAT_PER_JOB,
            FinishingBillingBasis.FLAT_PER_GROUP,
            FinishingBillingBasis.FLAT_PER_LINE,
        }

        if per_side_sheet_unit and self.billing_basis != FinishingBillingBasis.PER_SHEET:
            errors["billing_basis"] = _("Per-side-per-sheet finishings must use per_sheet billing.")

        if per_side_sheet_unit and self.side_mode != FinishingSideMode.PER_SELECTED_SIDE:
            errors["side_mode"] = _("Per-side-per-sheet finishings must bill per selected side.")

        if self.billing_basis == FinishingBillingBasis.PER_PIECE and self.side_mode != FinishingSideMode.IGNORE_SIDES:
            errors["side_mode"] = _("Per-piece finishings must ignore sides.")

        if self.billing_basis in flat_basis_values and self.side_mode != FinishingSideMode.IGNORE_SIDES:
            errors["side_mode"] = _("Flat finishings must ignore sides.")

        if self.billing_basis in flat_basis_values and self.charge_unit not in {ChargeUnit.FLAT, ChargeUnit.PER_SIDE}:
            errors["charge_unit"] = _("Flat finishings must use a flat-compatible charge unit.")

        if self.billing_basis == FinishingBillingBasis.PER_SHEET and not per_side_sheet_unit and self.side_mode == FinishingSideMode.PER_SELECTED_SIDE:
            errors["side_mode"] = _("Per selected side is only valid for per-sheet finishings that bill per side per sheet.")

        if self.double_side_price and self.side_mode != FinishingSideMode.PER_SELECTED_SIDE:
            errors["double_side_price"] = _("Double-side price is only valid when finishing bills per selected side.")

        if self.is_lamination_rule():
            if self.charge_unit != ChargeUnit.PER_SIDE_PER_SHEET:
                errors["charge_unit"] = _("Lamination must use PER_SIDE_PER_SHEET charge_unit.")
            if self.billing_basis != FinishingBillingBasis.PER_SHEET:
                errors["billing_basis"] = _("Lamination must use per_sheet billing.")
            if self.side_mode != FinishingSideMode.PER_SELECTED_SIDE:
                errors["side_mode"] = _("Lamination must use per_selected_side side_mode.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if not self.billing_basis:
            self.billing_basis = {
                ChargeUnit.PER_PIECE: FinishingBillingBasis.PER_PIECE,
                ChargeUnit.PER_SHEET: FinishingBillingBasis.PER_SHEET,
                ChargeUnit.PER_SIDE_PER_SHEET: FinishingBillingBasis.PER_SHEET,
                ChargeUnit.FLAT: FinishingBillingBasis.FLAT_PER_JOB,
            }.get(self.charge_unit, FinishingBillingBasis.PER_PIECE)
        if not self.side_mode:
            self.side_mode = (
                FinishingSideMode.PER_SELECTED_SIDE
                if self.charge_unit == ChargeUnit.PER_SIDE_PER_SHEET
                else FinishingSideMode.IGNORE_SIDES
            )
        if not self.display_unit_label:
            if self.billing_basis == FinishingBillingBasis.PER_SHEET and self.side_mode == FinishingSideMode.PER_SELECTED_SIDE:
                self.display_unit_label = "per sheet per side"
            elif self.billing_basis == FinishingBillingBasis.PER_SHEET:
                self.display_unit_label = "per sheet"
            elif self.billing_basis == FinishingBillingBasis.PER_PIECE:
                self.display_unit_label = "per piece"
        super().save(*args, **kwargs)


class Material(TimeStampedModel):
    """
    Material for LARGE_FORMAT printing (vinyl, banner, etc.) — sold by area (SQM).
    Not redundant with Paper: Material = large-format by sqm; Paper = sheet-fed pre-cut.
    """

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="materials",
        verbose_name=_("shop"),
        help_text=_("Shop that owns this material."),
    )
    production_size = models.ForeignKey(
        ProductionPaperSize,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="materials",
        verbose_name=_("production size"),
        help_text=_("Optional default parent sheet size for roll/sheet materials."),
    )
    material_type = models.CharField(
        max_length=255,
        default="",
        verbose_name=_("material type"),
        help_text=_("Type of material (e.g. vinyl, banner)."),
    )
    unit = models.CharField(
        max_length=20,
        default="SQM",
        verbose_name=_("unit"),
        help_text=_("Unit of measure (e.g. SQM for square meters)."),
    )
    buying_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("buying price"),
        help_text=_("Cost price per unit."),
    )
    selling_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("selling price"),
        help_text=_("Selling price per unit."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether this material is active."),
    )

    class Meta:
        ordering = ["shop", "material_type"]
        verbose_name = _("material")
        verbose_name_plural = _("materials")

    def __str__(self):
        return f"{self.material_type} ({self.shop.name})"


class ServiceRate(TimeStampedModel):
    """
    Extra charge the shop can apply: design, delivery, rush, setup.
    FIXED = flat price. TIERED_DISTANCE = price by distance tiers.
    """

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="service_rates",
        verbose_name=_("shop"),
        help_text=_("Shop that owns this service rate."),
    )
    code = models.CharField(
        max_length=30,
        choices=ServiceCode.choices,
        default=ServiceCode.DESIGN,
        verbose_name=_("code"),
        help_text=_("Service code (DESIGN, DELIVERY, RUSH, SETUP)."),
    )
    name = models.CharField(
        max_length=255,
        default="",
        verbose_name=_("name"),
        help_text=_("Display name (e.g. Design Charges, Delivery)."),
    )
    pricing_type = models.CharField(
        max_length=30,
        choices=ServicePricingType.choices,
        default=ServicePricingType.FIXED,
        verbose_name=_("pricing type"),
        help_text=_("FIXED or TIERED_DISTANCE."),
    )
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("price"),
        help_text=_("Fixed price when pricing_type=FIXED."),
    )
    is_optional = models.BooleanField(
        default=True,
        verbose_name=_("is optional"),
        help_text=_("Client can choose to add or skip."),
    )
    is_negotiable = models.BooleanField(
        default=False,
        verbose_name=_("is negotiable"),
        help_text=_("Seller can override price (e.g. design)."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether this service is available."),
    )

    class Meta:
        ordering = ["shop", "code"]
        verbose_name = _("service rate")
        verbose_name_plural = _("service rates")
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "code"],
                name="unique_shop_service_code",
            )
        ]

    def __str__(self):
        return f"{self.get_code_display()} ({self.shop.name})"

    def get_price_for_distance(self, distance_km):
        """Get price for TIERED_DISTANCE. Returns None if no matching tier."""
        if self.pricing_type != ServicePricingType.TIERED_DISTANCE:
            return self.price
        if distance_km is None:
            return None
        from decimal import Decimal
        d = Decimal(str(distance_km))
        tier = self.tiers.filter(min_km__lte=d).filter(
            models.Q(max_km__isnull=True) | models.Q(max_km__gte=d)
        ).order_by("-min_km").first()
        return tier.price if tier else None


class ServiceRateTier(TimeStampedModel):
    """Distance tier for TIERED_DISTANCE service (e.g. delivery)."""

    service_rate = models.ForeignKey(
        ServiceRate,
        on_delete=models.CASCADE,
        related_name="tiers",
        verbose_name=_("service rate"),
        help_text=_("Service rate this tier belongs to."),
    )
    min_km = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("min km"),
        help_text=_("Minimum distance in km."),
    )
    max_km = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("max km"),
        help_text=_("Maximum distance in km. Null = and above."),
    )
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("price"),
        help_text=_("Price for this tier."),
    )

    class Meta:
        ordering = ["service_rate", "min_km"]
        verbose_name = _("service rate tier")
        verbose_name_plural = _("service rate tiers")

    def __str__(self):
        r = f"{self.min_km}–{self.max_km}km" if self.max_km else f"{self.min_km}+km"
        return f"{self.service_rate}: {r} → {self.price}"


class VolumeDiscount(TimeStampedModel):
    """Bulk/volume discount for a shop (e.g. 10% off for 500+ items)."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="volume_discounts",
        verbose_name=_("shop"),
        help_text=_("Shop that owns this discount."),
    )
    name = models.CharField(
        max_length=255,
        verbose_name=_("name"),
        help_text=_("Display name (e.g. Bulk 500+, High Volume)."),
    )
    min_quantity = models.PositiveIntegerField(
        verbose_name=_("min quantity"),
        help_text=_("Minimum quantity to qualify for this discount."),
    )
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        verbose_name=_("discount percent"),
        help_text=_("Discount percentage (e.g. 10 for 10% off)."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether this discount is available."),
    )

    class Meta:
        ordering = ["shop", "min_quantity"]
        verbose_name = _("volume discount")
        verbose_name_plural = _("volume discounts")

    def __str__(self):
        return f"{self.name} ({self.shop.name}): {self.discount_percent}% off @ {self.min_quantity}+"
