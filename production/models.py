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


class ProductionOrder(TimeStampedModel):
    """Shop-side fulfillment record linked to managed job assignment workflow."""

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
    quote = models.ForeignKey(
        "quotes.Quote",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="production_orders",
        verbose_name=_("shop quote"),
        help_text=_("Accepted shop quote this order was created from."),
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
