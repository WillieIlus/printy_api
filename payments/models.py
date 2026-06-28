from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel


class Payment(TimeStampedModel):
    """Canonical business-level payment record."""

    METHOD_MPESA = "mpesa"
    METHOD_CASH = "cash"
    METHOD_MANUAL = "manual"
    METHOD_CHOICES = [
        (METHOD_MPESA, _("M-Pesa")),
        (METHOD_CASH, _("Cash")),
        (METHOD_MANUAL, _("Manual")),
    ]

    STATUS_PENDING = "pending"
    STATUS_PROCESSING = "processing"
    STATUS_PAID = "paid"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_PROCESSING, _("Processing")),
        (STATUS_PAID, _("Paid")),
        (STATUS_FAILED, _("Failed")),
        (STATUS_CANCELLED, _("Cancelled")),
        (STATUS_EXPIRED, _("Expired")),
    ]

    quote = models.ForeignKey(
        "quotes.Quote",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
        verbose_name=_("quote"),
    )
    managed_job = models.ForeignKey(
        "jobs.ManagedJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="canonical_payments",
        verbose_name=_("managed job"),
    )
    payer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
        verbose_name=_("payer"),
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    method = models.CharField(max_length=16, choices=METHOD_CHOICES, default=METHOD_MPESA)
    provider = models.CharField(max_length=32, blank=True, default="mpesa")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    account_reference = models.CharField(max_length=100, blank=True, default="", db_index=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    merchant_request_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    mpesa_receipt_number = models.CharField(max_length=50, blank=True, null=True, db_index=True)
    payer_phone = models.CharField(max_length=20, blank=True, null=True)
    expected_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    received_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["quote", "status"], name="payment_quote_status_idx"),
            models.Index(fields=["managed_job", "status"], name="payment_job_status_idx"),
            models.Index(fields=["account_reference"], name="payment_account_ref_idx"),
        ]

    def __str__(self):
        return f"Payment #{self.id or 'new'} {self.amount} {self.currency}"


class MpesaSTKRequest(TimeStampedModel):
    """Disabled canonical placeholder for future STK lifecycle tracking."""

    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_SENT, _("Sent")),
        (STATUS_SUCCESS, _("Success")),
        (STATUS_FAILED, _("Failed")),
        (STATUS_CANCELLED, _("Cancelled")),
        (STATUS_EXPIRED, _("Expired")),
    ]

    payment = models.ForeignKey(
        Payment,
        on_delete=models.CASCADE,
        related_name="mpesa_stk_requests",
        verbose_name=_("payment"),
    )
    phone_number = models.CharField(max_length=20)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    account_reference = models.CharField(max_length=100, blank=True, default="")
    merchant_request_id = models.CharField(max_length=100, blank=True, null=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    response_code = models.CharField(max_length=20, blank=True, null=True)
    response_description = models.TextField(blank=True, null=True)
    customer_message = models.TextField(blank=True, null=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    callback_received_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    raw_response = models.JSONField(null=True, blank=True)
    raw_callback = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at", "-created_at"]
        indexes = [
            models.Index(fields=["checkout_request_id"], name="mpesa_stk_checkout_idx"),
            models.Index(fields=["status"], name="mpesa_stk_status_idx"),
        ]

    def __str__(self):
        return self.checkout_request_id or f"STK request #{self.id or 'new'}"
