"""Subscription and payment models."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from shops.models import Shop


class SubscriptionPlan(models.Model):
    """Subscription plan — name, price, billing period."""

    MONTHLY = "MONTHLY"
    BILLING_PERIOD_CHOICES = [(MONTHLY, "Monthly")]

    name = models.CharField(max_length=100, verbose_name=_("name"))
    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("price"),
        help_text=_("Price per billing period."),
    )
    billing_period = models.CharField(
        max_length=20,
        choices=BILLING_PERIOD_CHOICES,
        default=MONTHLY,
        verbose_name=_("billing period"),
    )

    class Meta:
        verbose_name = _("subscription plan")
        verbose_name_plural = _("subscription plans")
        ordering = ["price"]

    def __str__(self):
        return f"{self.name} — {self.get_billing_period_display()}"

    def days_in_period(self) -> int:
        """Days in one billing period."""
        if self.billing_period == self.MONTHLY:
            return 30
        return 30


class Subscription(models.Model):
    """Shop subscription — OneToOne with shop."""

    TRIAL = "TRIAL"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (TRIAL, "Trial"),
        (ACTIVE, "Active"),
        (PAST_DUE, "Past due"),
        (CANCELLED, "Cancelled"),
    ]

    shop = models.OneToOneField(
        Shop,
        on_delete=models.CASCADE,
        related_name="subscription",
        verbose_name=_("shop"),
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
        verbose_name=_("plan"),
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=TRIAL,
        verbose_name=_("status"),
    )
    period_start = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("period start"),
    )
    period_end = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("period end"),
    )
    next_billing_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("next billing date"),
    )
    last_payment_date = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("last payment date"),
    )

    class Meta:
        verbose_name = _("subscription")
        verbose_name_plural = _("subscriptions")

    def __str__(self):
        return f"{self.shop.name} — {self.get_status_display()}"


class MpesaStkRequest(models.Model):
    """M-Pesa STK push request — tracks init and callback."""

    INITIATED = "INITIATED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    STATUS_CHOICES = [
        (INITIATED, "Initiated"),
        (SUCCESS, "Success"),
        (FAILED, "Failed"),
    ]

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="mpesa_stk_requests",
        verbose_name=_("shop"),
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="mpesa_stk_requests",
        verbose_name=_("plan"),
    )
    phone = models.CharField(max_length=20, verbose_name=_("phone"))
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("amount"),
    )
    checkout_request_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name=_("checkout request id"),
        help_text=_("From Daraja STK push response."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=INITIATED,
        verbose_name=_("status"),
    )
    raw_callback_payload = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("raw callback payload"),
    )
    receipt_number = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("receipt number"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("M-Pesa STK request")
        verbose_name_plural = _("M-Pesa STK requests")

    def __str__(self):
        return f"{self.checkout_request_id} — {self.get_status_display()}"


class Payment(models.Model):
    """Payment record — links to subscription and M-Pesa receipt."""

    MPESA_C2B = "MPESA_C2B"
    METHOD_CHOICES = [(MPESA_C2B, "M-Pesa C2B")]

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (COMPLETED, "Completed"),
        (FAILED, "Failed"),
    ]

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name=_("subscription"),
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_("amount"),
    )
    method = models.CharField(
        max_length=20,
        choices=METHOD_CHOICES,
        default=MPESA_C2B,
        verbose_name=_("method"),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
        verbose_name=_("status"),
    )
    receipt_number = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("receipt number"),
    )
    phone = models.CharField(max_length=20, blank=True, default="", verbose_name=_("phone"))
    request_id = models.CharField(
        max_length=100,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("request id"),
        help_text=_("CheckoutRequestID or similar."),
    )
    period_start = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("period start"),
    )
    period_end = models.DateField(
        null=True,
        blank=True,
        verbose_name=_("period end"),
    )
    metadata = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("metadata"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))

    class Meta:
        verbose_name = _("payment")
        verbose_name_plural = _("payments")

    def __str__(self):
        return f"{self.subscription.shop.name} — {self.amount} ({self.get_status_display()})"
