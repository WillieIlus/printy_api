"""
JobShare models: JobRequest (overflow work to share), JobClaim (printer claiming), JobNotification.
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel

from .choices import JobClaimStatus, JobMachineType, JobRequestStatus


def _generate_public_token():
    """Generate un-guessable token (32 bytes = 43 chars base64url)."""
    return secrets.token_urlsafe(32)


class JobRequest(TimeStampedModel):
    """Overflow job a printer wants to share with others."""

    OPEN = JobRequestStatus.OPEN
    CLAIMED = JobRequestStatus.CLAIMED
    CLOSED = JobRequestStatus.CLOSED
    STATUS_CHOICES = JobRequestStatus.choices

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="job_requests",
        verbose_name=_("created by"),
    )
    title = models.CharField(
        max_length=255,
        verbose_name=_("title"),
        help_text=_("Short title for the job."),
    )
    specs = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("specs"),
        help_text=_("Job specifications (product, quantity, paper, etc.)."),
    )
    location = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("location"),
        help_text=_("Location or area for the job."),
    )
    deadline = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("deadline"),
        help_text=_("When the job needs to be done."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=OPEN,
        verbose_name=_("status"),
    )
    public_token = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        verbose_name=_("public token"),
        help_text=_("Un-guessable token for public share URL."),
    )
    machine_type = models.CharField(
        max_length=30,
        choices=JobMachineType.choices,
        default=JobMachineType.DIGITAL,
        verbose_name=_("machine type"),
        help_text=_("Required machine type (DIGITAL, LARGE_FORMAT, UV, etc)."),
    )
    finishing_capabilities = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("finishing capabilities needed"),
        help_text=_("List of finishing capabilities required (e.g. ['lamination', 'cutting'])."),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("job request")
        verbose_name_plural = _("job requests")

    def __str__(self):
        return f"{self.title} ({self.status})"

    def ensure_public_token(self):
        """Generate public_token if not set."""
        if not self.public_token:
            self.public_token = _generate_public_token()
            self.save(update_fields=["public_token", "updated_at"])
        return self.public_token


class JobClaim(TimeStampedModel):
    """A printer's claim on a job request."""

    PENDING = JobClaimStatus.PENDING
    ACCEPTED = JobClaimStatus.ACCEPTED
    REJECTED = JobClaimStatus.REJECTED
    STATUS_CHOICES = JobClaimStatus.choices

    job_request = models.ForeignKey(
        JobRequest,
        on_delete=models.CASCADE,
        related_name="claims",
        verbose_name=_("job request"),
    )
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="job_claims",
        verbose_name=_("claimed by"),
    )
    price_offered = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("price offered"),
        help_text=_("Optional price the claimant offers."),
    )
    message = models.TextField(
        blank=True,
        default="",
        verbose_name=_("message"),
        help_text=_("Message from the claimant."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
        verbose_name=_("status"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("job claim")
        verbose_name_plural = _("job claims")
        constraints = [
            models.UniqueConstraint(
                fields=["job_request", "claimed_by"],
                name="unique_job_claim",
            ),
        ]

    def __str__(self):
        return f"{self.job_request.title} — {self.claimed_by.email} ({self.status})"


class JobNotification(TimeStampedModel):
    """Notification for claimant when their claim is accepted."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="job_notifications",
        verbose_name=_("user"),
    )
    job_request = models.ForeignKey(
        JobRequest,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("job request"),
    )
    job_claim = models.ForeignKey(
        JobClaim,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
        verbose_name=_("job claim"),
    )
    message = models.TextField(
        default="",
        verbose_name=_("message"),
        help_text=_("Notification message."),
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("read at"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("job notification")
        verbose_name_plural = _("job notifications")

    def __str__(self):
        return f"JobNotification #{self.id} for {self.user.email}"
