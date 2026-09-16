"""M-Pesa payment records.

Money is always Kenyan Shillings. Amounts are Decimal — never floats.

State machine (docs/DARAJA_PRODUCTION_CHECKLIST.md):

    initiated
       |
       |-- STK push accepted by Daraja  -> pending
       |                                    |
       |-- STK push rejected by Daraja       |-- success callback, amount ok -> paid
       |   -> failed                         |
       |                                     |-- failure callback            -> failed
       |                                     |-- ResultCode 1032 / timeout   -> cancelled
       |                                     |-- amount mismatch             -> needs_review
       |                                     `-- duplicate callback          -> (ignored, logged)

`reconciliation_status` is independent of payment status because an amount
mismatch must be reviewable without destroying the fact that money moved.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class MpesaPaymentStatus(models.TextChoices):
    """Canonical payment states shared by frontend and backend."""

    INITIATED = "initiated", "Initiated"
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    NEEDS_REVIEW = "needs_review", "Needs review"


class MpesaReconciliationStatus(models.TextChoices):
    """Independent verification of money actually received."""

    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    AMOUNT_MISMATCH = "amount_mismatch", "Amount mismatch"
    DUPLICATE = "duplicate", "Duplicate"
    MANUAL_REVIEW = "manual_review", "Manual review"


class MpesaPayment(models.Model):
    """One STK Push attempt against a payable Printy object.

    Attachable to a ManagedJob, Quote or QuoteRequest via a generic foreign
    key, so the module does not need to know about the rest of the codebase.
    """

    # who is paying
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mpesa_payments",
        verbose_name=_("user"),
        help_text=_("Account that initiated the payment. Null if guest."),
    )
    phone_number = models.CharField(
        max_length=20,
        db_index=True,
        verbose_name=_("phone number"),
        help_text=_("Normalized MSISDN in 2547XXXXXXXX form."),
    )

    # what is being paid for
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    payable = GenericForeignKey("content_type", "object_id")

    # money
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("1.00"))],
        verbose_name=_("amount"),
        help_text=_("Expected amount in KES."),
    )
    currency = models.CharField(max_length=3, default="KES", editable=False)

    # state
    status = models.CharField(
        max_length=20,
        choices=MpesaPaymentStatus.choices,
        default=MpesaPaymentStatus.INITIATED,
        db_index=True,
        verbose_name=_("status"),
    )
    reconciliation_status = models.CharField(
        max_length=24,
        choices=MpesaReconciliationStatus.choices,
        default=MpesaReconciliationStatus.PENDING,
        db_index=True,
        verbose_name=_("reconciliation status"),
    )

    # what the client asked for
    account_reference = models.CharField(
        max_length=40,
        default="PRINTY",
        verbose_name=_("account reference"),
        help_text=_("Shown on the customer's M-Pesa statement."),
    )
    description = models.CharField(max_length=100, default="Printy payment")

    # Daraja identifiers — CheckoutRequestID is the idempotency key
    merchant_request_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    checkout_request_id = models.CharField(max_length=64, blank=True, default="", unique=True)
    customer_message = models.TextField(blank=True, default="")

    # confirmation
    mpesa_receipt_number = models.CharField(max_length=32, blank=True, default="", db_index=True)
    transaction_date = models.DateTimeField(null=True, blank=True)
    paid_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text=_("Amount reported by the callback. Compare against `amount`."),
    )
    result_code = models.CharField(max_length=12, blank=True, default="")
    result_desc = models.TextField(blank=True, default="")
    raw_callback = models.JSONField(null=True, blank=True)

    # lifecycle timestamps
    initiated_at = models.DateTimeField(auto_now_add=True)
    stk_pushed_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("M-Pesa payment")
        verbose_name_plural = _("M-Pesa payments")
        ordering = ["-initiated_at"]
        indexes = [
            models.Index(fields=["status", "reconciliation_status"]),
            models.Index(fields=["-initiated_at"]),
            models.Index(fields=["content_type", "object_id"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=Decimal("1.00")),
                name="mpesa_amount_minimum_one_shilling",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.account_reference} · {self.amount} KES · {self.status}"

    # ── transitions ──────────────────────────────────────────
    # Every transition is guarded. A payment that has already reached a
    # terminal state must never be moved by a late or replayed callback.

    TERMINAL_STATES = {
        MpesaPaymentStatus.PAID,
        MpesaPaymentStatus.FAILED,
        MpesaPaymentStatus.CANCELLED,
        MpesaPaymentStatus.NEEDS_REVIEW,
    }

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATES

    @property
    def is_paid(self) -> bool:
        return self.status == MpesaPaymentStatus.PAID

    @property
    def is_amount_matched(self) -> bool:
        if self.paid_amount is None:
            return False
        return self.paid_amount == self.amount

    def clean(self) -> None:
        super().clean()
        if self.paid_amount is not None and self.paid_amount < 0:
            raise ValidationError({"paid_amount": _("Paid amount cannot be negative.")})

    def mark_push_sent(self, *, merchant_request_id: str, checkout_request_id: str, customer_message: str = "") -> None:
        """Daraja accepted the STK push request. Money has NOT moved yet."""
        if self.is_terminal:
            raise ValidationError(_("Cannot re-push a terminal payment."))
        self.status = MpesaPaymentStatus.PENDING
        self.reconciliation_status = MpesaReconciliationStatus.PENDING
        self.merchant_request_id = merchant_request_id
        self.checkout_request_id = checkout_request_id
        self.customer_message = customer_message
        self.stk_pushed_at = timezone.now()
        self.save(
            update_fields=[
                "status", "reconciliation_status", "merchant_request_id",
                "checkout_request_id", "customer_message", "stk_pushed_at", "updated_at",
            ]
        )

    def mark_confirmed(
        self,
        *,
        receipt_number: str,
        paid_amount: Decimal,
        transaction_date=None,
        result_code: str = "0",
        result_desc: str = "",
        raw: dict | None = None,
    ) -> bool:
        """Success callback. Returns False if this is a duplicate."""
        if self.is_terminal:
            return False

        self.mpesa_receipt_number = receipt_number
        self.paid_amount = paid_amount
        self.result_code = result_code
        self.result_desc = result_desc
        self.raw_callback = raw
        if transaction_date is not None:
            self.transaction_date = transaction_date

        matched = Decimal(str(paid_amount)) == self.amount
        self.reconciliation_status = (
            MpesaReconciliationStatus.CONFIRMED if matched else MpesaReconciliationStatus.AMOUNT_MISMATCH
        )
        # An amount mismatch is still money received — flag it, do not call it paid.
        self.status = MpesaPaymentStatus.PAID if matched else MpesaPaymentStatus.NEEDS_REVIEW
        self.confirmed_at = timezone.now()
        self.save()
        return True

    def mark_failed(self, *, result_code: str, result_desc: str, raw: dict | None = None) -> bool:
        """Failure callback, or Daraja rejecting the push outright."""
        if self.is_terminal:
            return False
        self.status = MpesaPaymentStatus.FAILED
        self.result_code = result_code
        self.result_desc = result_desc
        self.raw_callback = raw
        self.save(update_fields=["status", "result_code", "result_desc", "raw_callback", "updated_at"])
        return True

    def mark_cancelled(self, *, result_code: str, result_desc: str, raw: dict | None = None) -> bool:
        """User cancelled (1032) or the session timed out (1037 / DS timeout)."""
        if self.is_terminal:
            return False
        self.status = MpesaPaymentStatus.CANCELLED
        self.result_code = result_code
        self.result_desc = result_desc
        self.raw_callback = raw
        self.save(update_fields=["status", "result_code", "result_desc", "raw_callback", "updated_at"])
        return True

    def mark_needs_review(self, reason: str) -> bool:
        """Manual flag for duplicates or anything an operator must inspect."""
        if self.status == MpesaPaymentStatus.PAID:
            return False
        self.status = MpesaPaymentStatus.NEEDS_REVIEW
        self.reconciliation_status = MpesaReconciliationStatus.MANUAL_REVIEW
        self.result_desc = reason
        self.save(update_fields=["status", "reconciliation_status", "result_desc", "updated_at"])
        return True


class MpesaAccessToken(models.Model):
    """Cached Daraja OAuth token.

    Daraja tokens last ~3599 seconds. Storing the expiry (not the fetched-at
    time) means a restart can never reuse a dead token.
    """

    access_token = models.TextField()
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("M-Pesa access token")
        verbose_name_plural = _("M-Pesa access tokens")
        ordering = ["-expires_at"]

    def __str__(self) -> str:
        return f"token expires {self.expires_at:%Y-%m-%d %H:%M}"

    @property
    def is_valid(self) -> bool:
        # refresh 60s early so a request never races the expiry
        return self.expires_at > timezone.now() + timezone.timedelta(seconds=60)


class MpesaCallbackLog(models.Model):
    """Every callback Safaricom sends us, including replays.

    Daraja retries on failure. This table is what makes duplicate handling
    auditable rather than inferred.
    """

    checkout_request_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    merchant_request_id = models.CharField(max_length=64, blank=True, default="")
    result_code = models.CharField(max_length=12, blank=True, default="")
    result_desc = models.TextField(blank=True, default="")
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    duplicate = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("M-Pesa callback log")
        verbose_name_plural = _("M-Pesa callback logs")
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"callback {self.checkout_request_id} rc={self.result_code}"
