from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel
from inventory.choices import SheetSize
from inventory.models import Machine
from inventory.models import Paper as PaperStock
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


class ShopPricingSettings(TimeStampedModel):
    """
    DEPRECATED: transitional shop pricing setup flag.
    PlatformFeePolicy owns active fee rules.
    """

    shop = models.OneToOneField(
        Shop,
        on_delete=models.CASCADE,
        related_name="pricing_settings",
        verbose_name=_("shop"),
        help_text=_("Shop that owns these marketplace pricing settings."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether these pricing settings are active."),
    )

    class Meta:
        ordering = ["shop__name"]
        verbose_name = _("shop pricing settings")
        verbose_name_plural = _("shop pricing settings")

    def __str__(self):
        return f"Pricing settings for {self.shop.name}"


class PlatformFeePolicy(models.Model):
    """Central platform fee and markup cap policy."""

    name = models.CharField(max_length=120, default="Default Printy Fee Policy")
    is_active = models.BooleanField(default=True)
    printer_fee_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0000"))
    broker_margin_fee_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0000"))
    small_job_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("2000.00"))
    medium_job_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("10000.00"))
    small_job_max_multiple = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("4.00"))
    medium_job_max_multiple = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("3.00"))
    bulk_job_max_multiple = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("2.00"))
    add_platform_fee_on_top = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        verbose_name = _("platform fee policy")
        verbose_name_plural = _("platform fee policies")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        for field in ("printer_fee_rate", "broker_margin_fee_rate"):
            if getattr(self, field) < 0:
                errors[field] = _("Rate cannot be negative.")
        if self.small_job_limit <= 0:
            errors["small_job_limit"] = _("Small job limit must be greater than zero.")
        if self.medium_job_limit <= self.small_job_limit:
            errors["medium_job_limit"] = _("Medium job limit must be greater than the small job limit.")
        for field in ("small_job_max_multiple", "medium_job_max_multiple", "bulk_job_max_multiple"):
            if getattr(self, field) < 1:
                errors[field] = _("Maximum markup multiple must be at least 1.")
        if errors:
            raise ValidationError(errors)

    def get_max_markup_multiple(self, production_cost):
        production_cost = Decimal(str(production_cost))
        if production_cost < self.small_job_limit:
            return self.small_job_max_multiple
        if production_cost < self.medium_job_limit:
            return self.medium_job_max_multiple
        return self.bulk_job_max_multiple

    def get_max_client_price(self, production_cost):
        production_cost = Decimal(str(production_cost))
        return production_cost * self.get_max_markup_multiple(production_cost)


class WastePolicy(models.Model):
    """Configurable sheet spoilage and billable-sheet policy."""

    name = models.CharField(max_length=120, default="Default Waste Policy")
    is_active = models.BooleanField(default=True)
    fixed_waste_sheets = models.PositiveIntegerField(default=2)
    variable_waste_rate = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.1000"))
    minimum_billable_sheets = models.PositiveIntegerField(default=3)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        verbose_name = _("waste policy")
        verbose_name_plural = _("waste policies")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        if self.variable_waste_rate < 0:
            errors["variable_waste_rate"] = _("Variable waste rate cannot be negative.")
        if self.minimum_billable_sheets <= 0:
            errors["minimum_billable_sheets"] = _("Minimum billable sheets must be greater than zero.")
        if errors:
            raise ValidationError(errors)


class SetupCostPolicy(models.Model):
    """Configurable fixed setup, labor, admin, and file-checking cost policy."""

    name = models.CharField(max_length=120, default="Default Setup Cost Policy")
    is_active = models.BooleanField(default=True)
    setup_minutes = models.PositiveIntegerField(default=10)
    labor_rate_per_hour = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("500.00"))
    machine_setup_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("75.00"))
    admin_handling_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("50.00"))
    file_check_fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("50.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        verbose_name = _("setup cost policy")
        verbose_name_plural = _("setup cost policies")

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        for field in ("labor_rate_per_hour", "machine_setup_fee", "admin_handling_fee", "file_check_fee"):
            if getattr(self, field) < 0:
                errors[field] = _("Cost cannot be negative.")
        if errors:
            raise ValidationError(errors)


class QuantityPricingTier(models.Model):
    """Configurable low-volume multiplier and order floor by billable sheets."""

    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    min_sheets = models.PositiveIntegerField()
    max_sheets = models.PositiveIntegerField(null=True, blank=True)
    multiplier = models.DecimalField(max_digits=6, decimal_places=2)
    minimum_order_floor = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["min_sheets", "max_sheets"]
        verbose_name = _("quantity pricing tier")
        verbose_name_plural = _("quantity pricing tiers")

    def __str__(self):
        upper = self.max_sheets if self.max_sheets is not None else "+"
        return f"{self.name} ({self.min_sheets}-{upper} sheets)"

    def clean(self):
        super().clean()
        errors = {}
        if self.max_sheets is not None and self.max_sheets < self.min_sheets:
            errors["max_sheets"] = _("Max sheets must be greater than or equal to min sheets.")
        if self.multiplier < 1:
            errors["multiplier"] = _("Multiplier must be at least 1.")
        if self.minimum_order_floor < 0:
            errors["minimum_order_floor"] = _("Minimum order floor cannot be negative.")
        if errors:
            raise ValidationError(errors)


class PrintingRate(TimeStampedModel):
    """
    Printing rate per machine, sheet size, color mode.
    Real print shop pricing: single_price is the print charge per side.
    double_price remains available as an optional duplex-per-sheet override.
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
        default=Decimal("15.00"),
        verbose_name=_("single price"),
        help_text=_("Base print charge per side. Simplex uses this once; duplex uses it twice."),
    )
    double_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("double price"),
        help_text=_("Optional duplex-per-sheet override. Leave blank to calculate from per-side price plus any duplex surcharge."),
    )
    duplex_surcharge = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("5.00"),
        verbose_name=_("duplex surcharge"),
        help_text=_("Optional flat surcharge added once per duplex sheet when the surcharge rule applies."),
    )
    duplex_surcharge_enabled = models.BooleanField(
        default=False,
        verbose_name=_("duplex surcharge enabled"),
        help_text=_("Turn on duplex surcharge logic for this printing rate."),
    )
    duplex_surcharge_min_gsm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("duplex surcharge min gsm"),
        help_text=_("Optional gsm threshold. The duplex surcharge only applies when the selected paper gsm meets or exceeds this value."),
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

    def clean(self):
        super().clean()
        errors = {}
        if self.duplex_surcharge < 0:
            errors["duplex_surcharge"] = _("Duplex surcharge cannot be negative.")
        if self.duplex_surcharge_min_gsm is not None and self.duplex_surcharge_min_gsm <= 0:
            errors["duplex_surcharge_min_gsm"] = _("Minimum gsm must be greater than zero.")
        if errors:
            raise ValidationError(errors)

    def should_apply_duplex_surcharge(self, *, paper=None, apply_duplex_surcharge=None) -> bool:
        if apply_duplex_surcharge is False:
            return False
        if self.duplex_surcharge <= 0:
            return False
        if apply_duplex_surcharge is True:
            return True
        if not self.duplex_surcharge_enabled:
            return False

        # Business rule: Art 130 may have no surcharge
        if paper:
            paper_name = (getattr(paper, "name", "") or "").strip().lower()
            paper_gsm = getattr(paper, "gsm", 0)
            if "art" in paper_name and paper_gsm == 130:
                return False

        threshold = self.duplex_surcharge_min_gsm
        if threshold is None:
            return True
        paper_gsm = getattr(paper, "gsm", None)
        if paper_gsm is None:
            return False
        return int(paper_gsm) >= int(threshold)

    def get_duplex_price_breakdown(self, *, paper=None, apply_duplex_surcharge=None) -> dict:
        front_side_price = Decimal(self.single_price or 0)
        back_side_price = Decimal(self.single_price or 0)
        surcharge_applied = self.should_apply_duplex_surcharge(
            paper=paper,
            apply_duplex_surcharge=apply_duplex_surcharge,
        )
        surcharge_amount = Decimal(self.duplex_surcharge or 0) if surcharge_applied else Decimal("0")
        override_used = self.double_price is not None
        # double_price is the base print cost for both sides; surcharge is always added on top when applicable.
        base_per_sheet = Decimal(self.double_price) if override_used else (front_side_price + back_side_price)
        total_per_sheet = base_per_sheet + surcharge_amount
        return {
            "front_side_price": front_side_price,
            "back_side_price": back_side_price,
            "duplex_surcharge": surcharge_amount,
            "duplex_surcharge_applied": surcharge_applied,
            "duplex_override_used": override_used,
            "duplex_override_price": Decimal(self.double_price) if self.double_price is not None else None,
            "total_per_sheet": total_per_sheet,
        }

    def get_price_for_sides(self, sides, *, paper=None, apply_duplex_surcharge=None):
        """Return the effective per-sheet printing price for simplex or duplex."""
        if sides == Sides.DUPLEX:
            return self.get_duplex_price_breakdown(
                paper=paper,
                apply_duplex_surcharge=apply_duplex_surcharge,
            )["total_per_sheet"]
        return Decimal(self.single_price or 0)

    @classmethod
    def resolve(cls, machine, sheet_size, color_mode, sides, *, paper=None, apply_duplex_surcharge=None):
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
            return rate, rate.get_price_for_sides(
                sides,
                paper=paper,
                apply_duplex_surcharge=apply_duplex_surcharge,
            )
        # Fallback: use machine's default rate when sheet_size matches
        default_rate = cls.objects.filter(
            machine=machine,
            is_default=True,
            is_active=True,
        ).first()
        if default_rate and default_rate.sheet_size == sheet_size:
            return default_rate, default_rate.get_price_for_sides(
                sides,
                paper=paper,
                apply_duplex_surcharge=apply_duplex_surcharge,
            )
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
        verbose_name=_("one-side only"),
        help_text=_("If true, this finishing can only be applied to one side."),
    )
    charge_unit = models.CharField(
        max_length=20,
        choices=ChargeUnit.choices,
        default=ChargeUnit.PER_PIECE,
        verbose_name=_("charge unit"),
        help_text=_("How this finishing is charged. For lamination, use per sheet."),
    )
    billing_basis = models.CharField(
        max_length=30,
        choices=FinishingBillingBasis.choices,
        default=FinishingBillingBasis.PER_PIECE,
        verbose_name=_("billing basis"),
        help_text=_("Billing basis used by pricing. For lamination, use per sheet."),
    )
    side_mode = models.CharField(
        max_length=30,
        choices=FinishingSideMode.choices,
        default=FinishingSideMode.IGNORE_SIDES,
        verbose_name=_("side mode"),
        help_text=_("Whether the rate changes based on one side or both sides."),
    )
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("price"),
        help_text=_("Base rate. For lamination, this is the one-side rate per sheet."),
    )
    double_side_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("both-side rate"),
        help_text=_("Optional rate when lamination is applied to both sides. Leave blank to use 2× the one-side rate."),
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
        help_text=_("Optional UI label like 'per sheet'."),
    )
    help_text = models.TextField(
        blank=True,
        default="",
        verbose_name=_("help text"),
        help_text=_("Optional customer-facing pricing explanation."),
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
        category_name = ""
        name = (self.name or "").strip().lower()
        slug = (self.slug or "").strip().lower()
        return bool(
            self.thickness_microns
            or "lamination" in category_name
            or "lamination" in name
            or "lamination" in slug
        )

    def uses_side_selected_sheet_pricing(self) -> bool:
        return (
            self.billing_basis == FinishingBillingBasis.PER_SHEET
            and self.side_mode == FinishingSideMode.PER_SELECTED_SIDE
        )

    def clean(self):
        super().clean()

        errors = {}
        per_side_sheet_unit = self.charge_unit == ChargeUnit.PER_SIDE_PER_SHEET
        side_selected_sheet_pricing = self.uses_side_selected_sheet_pricing()
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

        if self.billing_basis == FinishingBillingBasis.PER_SHEET and self.charge_unit == ChargeUnit.PER_PIECE:
            errors["charge_unit"] = _("Per-sheet finishings cannot use per-piece charge_unit.")

        if self.double_side_price and not side_selected_sheet_pricing:
            errors["double_side_price"] = _("Double-side price is only valid for per-sheet finishings that support side selection.")

        if self.is_lamination_rule():
            if self.billing_basis != FinishingBillingBasis.PER_SHEET:
                errors["billing_basis"] = _("Lamination must use per_sheet billing.")
            if self.side_mode != FinishingSideMode.PER_SELECTED_SIDE:
                errors["side_mode"] = _("Lamination must use per_selected_side side_mode.")
            if self.charge_unit not in {ChargeUnit.PER_SHEET, ChargeUnit.PER_SIDE_PER_SHEET}:
                errors["charge_unit"] = _("Lamination must use per_sheet charge_unit. Legacy PER_SIDE_PER_SHEET is still supported.")

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        if self.is_lamination_rule() and self.charge_unit == ChargeUnit.PER_SIDE_PER_SHEET:
            # TODO: remove legacy PER_SIDE_PER_SHEET entirely after existing rows are migrated.
            self.charge_unit = ChargeUnit.PER_SHEET
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
                if self.charge_unit == ChargeUnit.PER_SIDE_PER_SHEET or self.is_lamination_rule()
                else FinishingSideMode.IGNORE_SIDES
            )
        if self.is_lamination_rule() and not self.help_text:
            self.help_text = "Charged per sheet. Choose one side or both sides."
        if not self.display_unit_label or self.display_unit_label == "per sheet per side":
            if self.billing_basis == FinishingBillingBasis.PER_SHEET and self.side_mode == FinishingSideMode.PER_SELECTED_SIDE:
                self.display_unit_label = "per sheet"
            elif self.billing_basis == FinishingBillingBasis.PER_SHEET:
                self.display_unit_label = "per sheet"
            elif self.billing_basis == FinishingBillingBasis.PER_PIECE:
                self.display_unit_label = "per piece"
        super().save(*args, **kwargs)


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
