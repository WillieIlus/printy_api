"""
Canonical managed job models.
"""
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel

from .choices import (
    JobAssignmentStatus,
    JobFileStatus,
    JobFileType,
    JobFileVisibility,
    ManagedJobAssignmentStatus,
    ManagedJobExceptionStatus,
    ManagedJobFulfillmentMode,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
    ManagedJobUrgencyType,
    ManagedJobTopologyType,
)


def _generate_public_token():
    """Generate un-guessable token (32 bytes = 43 chars base64url)."""
    return secrets.token_urlsafe(32)


def _reference_date_and_sequence(source) -> tuple[str, int | None]:
    reference = str(getattr(source, "request_reference", "") or getattr(source, "quote_reference", "") or "")
    parts = reference.split("-")
    if len(parts) >= 3 and parts[1].isdigit():
        sequence = parts[-1]
        if sequence.isdigit():
            return parts[1], int(sequence)
    created_at = getattr(source, "created_at", None) or timezone.now()
    return f"{created_at:%Y%m%d}", getattr(source, "id", None)


def _managed_reference_from_source(instance) -> str:
    quote_request = getattr(instance, "source_quote_request", None)
    quote = getattr(instance, "source_quote", None)
    if quote_request is None and quote is not None:
        quote_request = getattr(quote, "quote_request", None)
    source = quote_request or quote
    if source is None:
        return ""
    reference_date, sequence = _reference_date_and_sequence(source)
    if not sequence:
        return ""
    return f"MJ-{reference_date}-{int(sequence):04d}"


class ManagedJob(TimeStampedModel):
    """Platform-owned workflow anchor for managed operational orchestration."""

    managed_reference = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        default="",
        verbose_name=_("managed reference"),
        help_text=_("Stable reference for the managed operational job."),
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("title"),
        help_text=_("Operational label for the managed job."),
    )
    tracking_token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name=_("tracking token"),
        help_text=_("Public tracking token for managed job status links."),
    )
    source_quote_request = models.ForeignKey(
        "quotes.QuoteRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_jobs",
        verbose_name=_("source quote request"),
    )
    source_quote = models.ForeignKey(
        "quotes.Quote",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_jobs",
        verbose_name=_("source shop quote"),
    )
    source_production_order = models.ForeignKey(
        "production.ProductionOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_jobs",
        verbose_name=_("source production order"),
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_jobs",
        verbose_name=_("client"),
    )
    broker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="broker_managed_jobs",
        verbose_name=_("broker"),
    )
    assigned_shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_jobs",
        verbose_name=_("assigned shop"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_jobs_created",
        verbose_name=_("created by"),
    )
    status = models.CharField(
        max_length=32,
        choices=ManagedJobStatus.choices,
        default=ManagedJobStatus.DRAFT,
        verbose_name=_("status"),
    )
    payment_status = models.CharField(
        max_length=32,
        choices=ManagedJobPaymentStatus.choices,
        default=ManagedJobPaymentStatus.PENDING,
        verbose_name=_("payment status"),
    )
    assignment_status = models.CharField(
        max_length=32,
        choices=ManagedJobAssignmentStatus.choices,
        default=ManagedJobAssignmentStatus.UNASSIGNED,
        verbose_name=_("assignment status"),
    )
    exception_status = models.CharField(
        max_length=32,
        choices=ManagedJobExceptionStatus.choices,
        default=ManagedJobExceptionStatus.CLEAR,
        verbose_name=_("exception status"),
    )
    fulfillment_mode = models.CharField(
        max_length=32,
        choices=ManagedJobFulfillmentMode.choices,
        default=ManagedJobFulfillmentMode.PICKUP,
        verbose_name=_("fulfillment mode"),
    )
    topology_type = models.CharField(
        max_length=32,
        choices=ManagedJobTopologyType.choices,
        default=ManagedJobTopologyType.CLIENT_PRINTY_SUPPORT,
        verbose_name=_("topology type"),
    )
    urgency_type = models.CharField(
        max_length=32,
        choices=ManagedJobUrgencyType.choices,
        default=ManagedJobUrgencyType.STANDARD,
        verbose_name=_("urgency type"),
    )
    urgency_multiplier = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    urgency_fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    after_hours_fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    requested_deadline = models.DateTimeField(null=True, blank=True)
    requested_delivery_time = models.DateTimeField(null=True, blank=True)
    operational_priority_level = models.PositiveSmallIntegerField(default=1)
    client_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    printy_fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    broker_payout = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payout_hold = models.BooleanField(default=False)
    artwork_required = models.BooleanField(default=False)
    artwork_reminder_sent = models.BooleanField(default=False)
    dispute_open = models.BooleanField(default=False)
    production_issue_flag = models.BooleanField(default=False)
    delivery_issue_flag = models.BooleanField(default=False)
    ops_review_required = models.BooleanField(default=False)
    operational_snapshot = models.JSONField(default=dict, blank=True)
    workflow_metadata = models.JSONField(default=dict, blank=True)
    relationship_snapshot = models.JSONField(default=dict, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    payment_confirmed_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    dispatched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_jobs_dispatched",
        verbose_name=_("dispatched by"),
    )
    assigned_at = models.DateTimeField(null=True, blank=True)
    production_started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    disputed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("managed job")
        verbose_name_plural = _("managed jobs")
        indexes = [
            models.Index(fields=["status", "payment_status"], name="managed_job_status_payment_idx"),
            models.Index(fields=["assigned_shop", "assignment_status"], name="managed_job_assignment_idx"),
            models.Index(fields=["operational_priority_level", "status"], name="managed_job_priority_idx"),
        ]

    def __str__(self):
        return self.managed_reference or self.title or f"ManagedJob #{self.id}"

    def save(self, *args, **kwargs):
        if not self.managed_reference:
            self.managed_reference = _managed_reference_from_source(self)
        if not self.managed_reference:
            self.managed_reference = f"MJ-{timezone.now():%Y%m%d}-new-{secrets.token_hex(3)}"
        super().save(*args, **kwargs)
        if "-new-" in self.managed_reference:
            self.managed_reference = f"MJ-{timezone.now():%Y%m%d}-{self.id:04d}"
            super().save(update_fields=["managed_reference", "updated_at"])


class JobAssignment(TimeStampedModel):
    """Shop production responsibility layer beneath ManagedJob."""

    managed_job = models.ForeignKey(
        ManagedJob,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name=_("managed job"),
    )
    assigned_shop = models.ForeignKey(
        "shops.Shop",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_assignments",
        verbose_name=_("assigned shop"),
    )
    source_quote = models.ForeignKey(
        "quotes.Quote",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_assignments",
        verbose_name=_("source shop quote"),
    )
    production_order = models.ForeignKey(
        "production.ProductionOrder",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_assignments",
        verbose_name=_("production order"),
    )
    status = models.CharField(
        max_length=32,
        choices=JobAssignmentStatus.choices,
        default=JobAssignmentStatus.PENDING,
        verbose_name=_("status"),
    )
    shop_payout = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    urgency_type = models.CharField(
        max_length=32,
        choices=ManagedJobUrgencyType.choices,
        default=ManagedJobUrgencyType.STANDARD,
        verbose_name=_("urgency type"),
    )
    operational_priority_level = models.PositiveSmallIntegerField(default=1)
    due_at = models.DateTimeField(null=True, blank=True)
    requested_deadline = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    reassigned_from = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reassignments",
        verbose_name=_("reassigned from"),
    )
    assignment_notes = models.TextField(blank=True, default="")
    operational_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("job assignment")
        verbose_name_plural = _("job assignments")
        constraints = [
            models.UniqueConstraint(
                fields=["managed_job"],
                condition=models.Q(reassigned_from__isnull=True),
                name="unique_active_assignment_per_managed_job",
            ),
        ]
        indexes = [
            models.Index(fields=["assigned_shop", "status"], name="job_assignment_shop_status_idx"),
            models.Index(fields=["assigned_shop", "operational_priority_level"], name="job_assignment_priority_idx"),
        ]

    def __str__(self):
        return f"Assignment #{self.id} for {self.managed_job.managed_reference or self.managed_job_id}"


class ManagedJobPayout(TimeStampedModel):
    """Manual payout dispatch record for a managed job recipient."""

    RECIPIENT_ROLE_MANAGER = "manager"
    RECIPIENT_ROLE_SHOP = "shop"
    RECIPIENT_ROLE_CHOICES = [
        (RECIPIENT_ROLE_MANAGER, _("Manager")),
        (RECIPIENT_ROLE_SHOP, _("Shop")),
    ]

    STATUS_PENDING = "pending"
    STATUS_RELEASED = "released"
    STATUS_ON_HOLD = "on_hold"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_RELEASED, _("Released")),
        (STATUS_ON_HOLD, _("On hold")),
        (STATUS_CANCELLED, _("Cancelled")),
    ]

    managed_job = models.ForeignKey(
        ManagedJob,
        on_delete=models.CASCADE,
        related_name="payouts",
        verbose_name=_("managed job"),
    )
    assignment = models.ForeignKey(
        JobAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts",
        verbose_name=_("assignment"),
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_job_payouts",
        verbose_name=_("recipient"),
    )
    recipient_role = models.CharField(max_length=16, choices=RECIPIENT_ROLE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    release_reference = models.CharField(max_length=100, blank=True, default="")
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_job_payouts_released",
        verbose_name=_("released by"),
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("managed job payout")
        verbose_name_plural = _("managed job payouts")
        constraints = [
            models.UniqueConstraint(
                fields=["managed_job", "recipient_role"],
                name="unique_payout_recipient_role_per_job",
            ),
        ]
        indexes = [
            models.Index(fields=["managed_job", "status"], name="job_payout_job_status_idx"),
            models.Index(fields=["recipient", "status"], name="job_payout_recipient_idx"),
        ]

    def __str__(self):
        return f"{self.recipient_role} payout for {self.managed_job.managed_reference or self.managed_job_id}"


class JobFile(TimeStampedModel):
    """Canonical managed-job file ownership record."""

    managed_job = models.ForeignKey(
        ManagedJob,
        on_delete=models.CASCADE,
        related_name="job_files",
        verbose_name=_("managed job"),
    )
    assignment = models.ForeignKey(
        JobAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_files",
        verbose_name=_("assignment"),
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_job_files",
        verbose_name=_("uploaded by"),
    )
    file = models.FileField(
        upload_to="managed_jobs/%Y/%m/",
        null=True,
        blank=True,
        verbose_name=_("file"),
    )
    original_filename = models.CharField(max_length=255, blank=True, default="")
    file_type = models.CharField(
        max_length=32,
        choices=JobFileType.choices,
        default=JobFileType.CUSTOMER_UPLOAD,
        verbose_name=_("file type"),
    )
    visibility = models.CharField(
        max_length=16,
        choices=JobFileVisibility.choices,
        default=JobFileVisibility.CLIENT,
        verbose_name=_("visibility"),
    )
    status = models.CharField(
        max_length=32,
        choices=JobFileStatus.choices,
        default=JobFileStatus.UPLOADED,
        verbose_name=_("status"),
    )
    version = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True, default="")
    replaces = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="revisions",
        verbose_name=_("replaces"),
    )
    source_quote_request_attachment = models.ForeignKey(
        "quotes.QuoteRequestAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_files",
        verbose_name=_("source quote request attachment"),
    )
    source_quote_attachment = models.ForeignKey(
        "quotes.QuoteAttachment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_files",
        verbose_name=_("source shop quote attachment"),
    )

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = _("job file")
        verbose_name_plural = _("job files")
        constraints = [
            models.UniqueConstraint(
                fields=["managed_job", "source_quote_request_attachment"],
                condition=models.Q(source_quote_request_attachment__isnull=False),
                name="unique_job_file_source_quote_attachment",
            ),
            models.UniqueConstraint(
                fields=["managed_job", "source_quote_attachment"],
                condition=models.Q(source_quote_attachment__isnull=False),
                name="unique_job_file_source_shop_attachment",
            ),
        ]
        indexes = [
            models.Index(fields=["managed_job", "file_type"], name="job_file_type_idx"),
            models.Index(fields=["managed_job", "visibility"], name="job_file_visibility_idx"),
        ]

    def __str__(self):
        return self.original_filename or f"JobFile #{self.id}"


class JobStatusEvent(TimeStampedModel):
    """Lightweight audit trail for managed-job workflow activity."""

    managed_job = models.ForeignKey(
        ManagedJob,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name=_("managed job"),
    )
    assignment = models.ForeignKey(
        JobAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("assignment"),
    )
    job_file = models.ForeignKey(
        JobFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name=_("job file"),
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_status_events",
        verbose_name=_("actor"),
    )
    event_type = models.CharField(max_length=64, verbose_name=_("event type"))
    summary = models.CharField(max_length=255, blank=True, default="", verbose_name=_("summary"))
    metadata = models.JSONField(default=dict, blank=True, verbose_name=_("metadata"))

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _("managed job event")
        verbose_name_plural = _("managed job events")
        indexes = [
            models.Index(fields=["managed_job", "event_type"], name="job_status_event_type_idx"),
            models.Index(fields=["managed_job", "-created_at"], name="job_status_event_created_idx"),
        ]

    def __str__(self):
        return f"{self.event_type} on {self.managed_job.managed_reference or self.managed_job_id}"
