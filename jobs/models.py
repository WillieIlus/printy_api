"""
Canonical managed job models.
"""
import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
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
    # --- timeliness (law Part 4) -----------------------------------------
    due_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("due date"),
        help_text=_("Due date computed from the SLA config at quote acceptance (law 6.2)."),
    )
    due_date_acknowledged_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("due date acknowledged at"),
        help_text=_("When the buyer last acknowledged the current due date (law 6.5)."),
    )
    due_date_revision_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_("due date revision count"),
        help_text=_("How many times a manager change moved this job's due date (law 6.5)."),
    )
    is_late = models.BooleanField(
        default=False,
        verbose_name=_("is late"),
        help_text=_("Flagged when the job passed its latest acknowledged due date (law 6.6)."),
    )
    lateness_minutes = models.PositiveIntegerField(
        default=0,
        verbose_name=_("lateness minutes"),
        help_text=_("Minutes past the acknowledged due date, recorded not penalised (law 6.6)."),
    )
    current_stage = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name=_("current stage"),
        help_text=_("Canonical timeliness stage key (law 6.2/6.4)."),
    )
    stage_owner = models.CharField(
        max_length=16,
        blank=True,
        default="",
        verbose_name=_("stage owner"),
        help_text=_("Who holds the ball at the current stage (law 6.4)."),
    )

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
    STATUS_PROCESSING = "processing"
    STATUS_RELEASED = "released"
    STATUS_FAILED = "failed"
    STATUS_ON_HOLD = "on_hold"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, _("Pending")),
        (STATUS_PROCESSING, _("Processing")),
        (STATUS_RELEASED, _("Released")),
        (STATUS_FAILED, _("Failed")),
        (STATUS_ON_HOLD, _("On hold")),
        (STATUS_CANCELLED, _("Cancelled")),
    ]

    DISBURSEMENT_MANUAL = "manual"
    DISBURSEMENT_AUTOMATIC = "automatic"
    DISBURSEMENT_MODE_CHOICES = [
        (DISBURSEMENT_MANUAL, _("Manual")),
        (DISBURSEMENT_AUTOMATIC, _("Automatic")),
    ]
    # Printy currently has no outbound money-movement provider, so every payout
    # is manual: an admin moves the money out of band and then records the
    # transfer reference here. "released" therefore attests that the money was
    # sent by hand, not that an API transferred it.
    AUTOMATIC_DISBURSEMENT_AVAILABLE = False

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
    disbursement_mode = models.CharField(
        max_length=16,
        choices=DISBURSEMENT_MODE_CHOICES,
        default=DISBURSEMENT_MANUAL,
        verbose_name=_("disbursement mode"),
        help_text=_("How the money reached the recipient. 'manual' means an admin transferred it out of band."),
    )
    transfer_reference = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("transfer reference"),
        help_text=_("Bank/M-Pesa confirmation reference for a manual transfer, or the provider reference if automatic."),
    )
    provider_response = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("provider response"),
        help_text=_("Raw response from the payout provider, when one is involved."),
    )
    failure_reason = models.TextField(
        blank=True,
        default="",
        verbose_name=_("failure reason"),
    )
    failed_at = models.DateTimeField(null=True, blank=True)
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


class JobEventQuerySet(models.QuerySet):
    """QuerySet that refuses mutation so the job event log stays append-only."""

    def update(self, *args, **kwargs):  # pragma: no cover - guard
        raise ValidationError("Job events are append-only; update is forbidden (law 6.3).")

    def delete(self, *args, **kwargs):  # pragma: no cover - guard
        raise ValidationError("Job events are append-only; delete is forbidden (law 6.3).")


class JobEvent(models.Model):
    """Append-only job event log (law 6.3).

    Every stage start, handoff, completion and delay is written here with job,
    stage, event, actor, role, UTC timestamp and source. Events are never edited
    or deleted: a correction is a new event that ``references`` the original.
    """

    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    HANDOFF = "handoff"
    DELAY = "delay"
    DATE_REVISION = "date_revision"
    CORRECTION = "correction"

    SOURCE_SYSTEM = "system"
    SOURCE_MANAGER = "manager"
    SOURCE_PRINTER = "printer"
    SOURCE_BUYER = "buyer"
    SOURCE_EXTERNAL = "external"

    objects = JobEventQuerySet.as_manager()

    managed_job = models.ForeignKey(
        ManagedJob,
        on_delete=models.CASCADE,
        related_name="job_events",
        verbose_name=_("managed job"),
    )
    stage = models.CharField(max_length=32, blank=True, default="", verbose_name=_("stage"))
    event = models.CharField(max_length=32, verbose_name=_("event"))
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_events",
        verbose_name=_("actor"),
    )
    actor_role = models.CharField(max_length=16, blank=True, default="", verbose_name=_("actor role"))
    occurred_at = models.DateTimeField(default=timezone.now, verbose_name=_("occurred at (UTC)"))
    source = models.CharField(max_length=32, blank=True, default=SOURCE_SYSTEM, verbose_name=_("source"))
    references = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corrections",
        verbose_name=_("references"),
        help_text=_("The original event this correction references (law 6.3)."),
    )
    metadata = models.JSONField(default=dict, blank=True, verbose_name=_("metadata"))

    class Meta:
        ordering = ["occurred_at", "id"]
        verbose_name = _("job event")
        verbose_name_plural = _("job events")
        indexes = [
            models.Index(fields=["managed_job", "occurred_at"], name="job_event_job_occurred_idx"),
            models.Index(fields=["managed_job", "stage"], name="job_event_job_stage_idx"),
            models.Index(fields=["managed_job", "event"], name="job_event_job_event_idx"),
        ]

    def __str__(self):
        return f"{self.event}@{self.stage or '-'} on {self.managed_job.managed_reference or self.managed_job_id}"

    def save(self, *args, **kwargs):  # noqa: D401
        if self.pk is not None and JobEvent.objects.filter(pk=self.pk).exists():
            raise ValidationError("Job events are append-only; create a correcting event instead (law 6.3).")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # noqa: D401
        raise ValidationError("Job events cannot be deleted (append-only, law 6.3).")


class ViewerItemReadState(models.Model):
    """Per-viewer read marker for a quote request or a job (law 7.11).

    Read state is per viewer: the same quote/job can be unread for one user and
    read for another. It is deliberately kept out of the item's own status,
    section and attention/muted flags -- marking an item read must never change
    those (law 7.11).
    """

    ITEM_QUOTE_REQUEST = "quote_request"
    ITEM_JOB = "job"
    ITEM_TYPE_CHOICES = [
        (ITEM_QUOTE_REQUEST, _("Quote request")),
        (ITEM_JOB, _("Job")),
    ]

    viewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="viewer_item_read_states",
        verbose_name=_("viewer"),
    )
    item_type = models.CharField(
        max_length=32,
        choices=ITEM_TYPE_CHOICES,
        verbose_name=_("item type"),
        help_text=_("quote_request or job."),
    )
    item_id = models.PositiveIntegerField(verbose_name=_("item id"))
    read_at = models.DateTimeField(null=True, blank=True, verbose_name=_("read at"))
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("created at"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("updated at"))

    class Meta:
        verbose_name = _("viewer item read state")
        verbose_name_plural = _("viewer item read states")
        constraints = [
            models.UniqueConstraint(
                fields=["viewer", "item_type", "item_id"],
                name="viewer_read_view_type_item_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["viewer", "item_type"], name="viewer_read_view_type_idx"),
        ]

    def __str__(self):
        return f"{self.item_type}:{self.item_id} read={'yes' if self.read_at else 'no'} by {self.viewer_id}"

