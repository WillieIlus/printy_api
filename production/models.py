"""
Production tracking models for a printing business.
Jobs, JobProcesses, and supporting entities for analytics.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel
from shops.models import Shop


class Customer(TimeStampedModel):
    """Unified customer for production orders and quote requests. Replaces former Client."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="customers",
        verbose_name=_("shop"),
    )
    name = models.CharField(max_length=255, verbose_name=_("name"))
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    address = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name = _("customer")
        verbose_name_plural = _("customers")
        unique_together = [["shop", "name"]]

    def __str__(self):
        return self.name


class ProductionProduct(TimeStampedModel):
    """Product type for production jobs. Optional link to catalog.Product."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="production_products",
        verbose_name=_("shop"),
    )
    name = models.CharField(max_length=255, verbose_name=_("name"))
    catalog_product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_products",
        verbose_name=_("catalog product"),
    )
    description = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name = _("production product")
        verbose_name_plural = _("production products")
        unique_together = [["shop", "name"]]

    def __str__(self):
        return self.name


class ProductionMaterial(TimeStampedModel):
    """Material used in production (paper, laminate, etc.). Optional link to pricing.Material."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="production_materials",
        verbose_name=_("shop"),
    )
    name = models.CharField(max_length=255, verbose_name=_("name"))
    pricing_material = models.ForeignKey(
        "pricing.Material",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_materials",
        verbose_name=_("pricing material"),
    )
    unit = models.CharField(max_length=20, default="SHEET", help_text=_("SHEET, SQM, ROLL, etc."))

    class Meta:
        ordering = ["name"]
        verbose_name = _("production material")
        verbose_name_plural = _("production materials")
        unique_together = [["shop", "name"]]

    def __str__(self):
        return self.name


class Process(TimeStampedModel):
    """Production process type: printing, lamination, cutting, binding."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="production_processes",
        verbose_name=_("shop"),
    )
    name = models.CharField(max_length=100, verbose_name=_("name"))
    slug = models.SlugField(max_length=50, help_text=_("e.g. printing, lamination, cutting, binding"))
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "name"]
        verbose_name = _("process")
        verbose_name_plural = _("processes")
        unique_together = [["shop", "slug"]]

    def __str__(self):
        return self.name


class Operator(TimeStampedModel):
    """Operator/worker who performs production processes."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="production_operators",
        verbose_name=_("shop"),
    )
    name = models.CharField(max_length=255, verbose_name=_("name"))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_operator_profiles",
        verbose_name=_("user"),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _("operator")
        verbose_name_plural = _("operators")

    def __str__(self):
        return self.name


class PricingMethod(TimeStampedModel):
    """How a process is priced: per sheet, per piece, per sqm, flat."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="production_pricing_methods",
        verbose_name=_("shop"),
    )
    name = models.CharField(max_length=100, verbose_name=_("name"))
    slug = models.SlugField(max_length=50, help_text=_("e.g. per_sheet, per_piece, per_sqm, flat"))
    unit_label = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name = _("pricing method")
        verbose_name_plural = _("pricing methods")
        unique_together = [["shop", "slug"]]

    def __str__(self):
        return self.name


class WastageStage(TimeStampedModel):
    """Stage where waste is tracked (for analytics)."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="production_wastage_stages",
        verbose_name=_("shop"),
    )
    name = models.CharField(max_length=100, verbose_name=_("name"))
    process = models.ForeignKey(
        Process,
        on_delete=models.CASCADE,
        related_name="wastage_stages",
        null=True,
        blank=True,
        verbose_name=_("process"),
    )

    class Meta:
        ordering = ["name"]
        verbose_name = _("wastage stage")
        verbose_name_plural = _("wastage stages")

    def __str__(self):
        return self.name


class PriceCard(TimeStampedModel):
    """Default rate for a process + pricing method combination."""

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="production_price_cards",
        verbose_name=_("shop"),
    )
    process = models.ForeignKey(
        Process,
        on_delete=models.CASCADE,
        related_name="price_cards",
        verbose_name=_("process"),
    )
    pricing_method = models.ForeignKey(
        PricingMethod,
        on_delete=models.CASCADE,
        related_name="price_cards",
        verbose_name=_("pricing method"),
    )
    default_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name=_("default rate"),
    )
    material = models.ForeignKey(
        ProductionMaterial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="price_cards",
        verbose_name=_("material"),
    )

    class Meta:
        ordering = ["process", "pricing_method"]
        verbose_name = _("price card")
        verbose_name_plural = _("price cards")
        unique_together = [["shop", "process", "pricing_method"]]

    def __str__(self):
        return f"{self.process} / {self.pricing_method}: {self.default_rate}"


class ProductionOrder(TimeStampedModel):
    """Production order (job) after customer accepts a ShopQuote. Distinct from jobs.JobRequest (overflow sharing)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    READY = "ready"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (PENDING, _("Pending")),
        (IN_PROGRESS, _("In Progress")),
        (READY, _("Ready")),
        (COMPLETED, _("Completed")),
        (CANCELLED, _("Cancelled")),
    ]

    DELIVERY_PENDING = "pending"
    DELIVERY_READY_FOR_PICKUP = "ready_for_pickup"
    DELIVERY_SHIPPED = "shipped"
    DELIVERY_DELIVERED = "delivered"
    DELIVERY_NA = "n_a"
    DELIVERY_STATUS_CHOICES = [
        (DELIVERY_PENDING, _("Pending")),
        (DELIVERY_READY_FOR_PICKUP, _("Ready for Pickup")),
        (DELIVERY_SHIPPED, _("Shipped")),
        (DELIVERY_DELIVERED, _("Delivered")),
        (DELIVERY_NA, _("N/A (pickup)")),
    ]

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="production_orders",
        verbose_name=_("shop"),
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_orders",
        verbose_name=_("customer"),
    )
    shop_quote = models.ForeignKey(
        "quotes.ShopQuote",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_orders",
        verbose_name=_("shop quote"),
        help_text=_("Accepted shop quote this order was created from."),
    )
    product = models.ForeignKey(
        ProductionProduct,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_orders",
        verbose_name=_("product"),
    )
    order_number = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("order number"),
    )
    title = models.CharField(
        max_length=255,
        default="",
        verbose_name=_("title"),
        help_text=_("Job title or description."),
    )
    quantity = models.PositiveIntegerField(
        default=0,
        verbose_name=_("quantity"),
        help_text=_("Total quantity ordered."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
        verbose_name=_("status"),
    )
    due_date = models.DateField(null=True, blank=True, verbose_name=_("due date"))
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name=_("completed at"))
    delivery_status = models.CharField(
        max_length=20,
        choices=DELIVERY_STATUS_CHOICES,
        blank=True,
        default="",
        verbose_name=_("delivery status"),
        help_text=_("Delivery tracking: pending, ready_for_pickup, shipped, delivered, or n_a for pickup."),
    )
    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("delivered at"),
        help_text=_("When the order was delivered to the customer."),
    )
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_orders_created",
        verbose_name=_("created by"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("production order")
        verbose_name_plural = _("production orders")

    def __str__(self):
        return self.order_number or self.title or f"Order #{self.id}"

    @property
    def total_revenue(self):
        return sum((jp.line_total or Decimal("0")) for jp in self.processes.all())


class JobProcess(TimeStampedModel):
    """A production stage within a production order (printing, lamination, cutting, binding)."""

    production_order = models.ForeignKey(
        ProductionOrder,
        on_delete=models.CASCADE,
        related_name="processes",
        verbose_name=_("production order"),
    )
    process = models.ForeignKey(
        Process,
        on_delete=models.CASCADE,
        related_name="job_processes",
        verbose_name=_("process"),
    )
    operator = models.ForeignKey(
        Operator,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_processes",
        verbose_name=_("operator"),
    )
    material = models.ForeignKey(
        ProductionMaterial,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_processes",
        verbose_name=_("material"),
    )
    pricing_method = models.ForeignKey(
        PricingMethod,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_processes",
        verbose_name=_("pricing method"),
    )
    date = models.DateField(verbose_name=_("date"))
    qty_input = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name=_("qty input"),
    )
    waste = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name=_("waste"),
    )
    default_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name=_("default rate"),
    )
    applied_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name=_("applied rate"),
    )
    billable_units = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name=_("billable units"),
    )
    line_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        verbose_name=_("line total"),
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["date", "process__display_order"]
        verbose_name = _("job process")
        verbose_name_plural = _("job processes")

    def __str__(self):
        return f"{self.production_order} — {self.process} ({self.date})"

    @property
    def good_qty(self):
        return (self.qty_input or Decimal("0")) - (self.waste or Decimal("0"))

    def save(self, *args, **kwargs):
        if self.applied_rate is None:
            self.applied_rate = self.default_rate or Decimal("0")
        self.line_total = (self.billable_units or Decimal("0")) * (self.applied_rate or Decimal("0"))
        super().save(*args, **kwargs)
