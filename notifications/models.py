"""
Notification model for quote marketplace events.
"""
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _


class Notification(models.Model):
    """
    In-app notification for quote-related events.

    Notifications are structured records, not pre-rendered sentences: the row
    stores a ``template_key`` plus ``params`` and the human-readable title/body
    are rendered per recipient-role and per language at read time. This keeps
    the privacy rule intact (a buyer never sees shop identity, a printer never
    sees what the client paid) because one event renders differently for each
    audience, and lets wording (and Swahili) change without migrating old rows.

    Recipient = user. Actor = who triggered (optional).
    """

    PRIORITY_ACTION_REQUIRED = "action_required"
    PRIORITY_INFORMATIONAL = "informational"
    PRIORITY_CHOICES = [
        (PRIORITY_ACTION_REQUIRED, _("Action required")),
        (PRIORITY_INFORMATIONAL, _("Informational")),
    ]

    QUOTE_REQUEST_SUBMITTED = "quote_request_submitted"
    QUOTE_REQUEST_SENT = "quote_request_sent"
    SHOP_QUOTE_SENT = "quote_sent"
    SHOP_QUOTE_REVISED = "quote_revised"
    SHOP_QUOTE_ACCEPTED = "quote_accepted"
    SHOP_QUESTION_ASKED = "shop_question_asked"
    BUYER_CLARIFICATION_SENT = "buyer_clarification_sent"
    REQUEST_DECLINED = "request_declined"
    QUOTE_REQUEST_CANCELLED = "quote_request_cancelled"
    JOB_STATUS_UPDATED = "job_status_updated"
    PAYMENT_CONFIRMED = "payment_confirmed"
    ARTWORK_REQUIRED = "artwork_required"
    JOB_READY_TO_START = "job_ready_to_start"
    JOB_CREATED = "job_created"
    TYPE_CHOICES = [
        (QUOTE_REQUEST_SUBMITTED, _("New quote request")),
        (QUOTE_REQUEST_SENT, _("Request sent")),
        (SHOP_QUOTE_SENT, _("Quote sent")),
        (SHOP_QUOTE_REVISED, _("Quote revised")),
        (SHOP_QUOTE_ACCEPTED, _("Quote accepted")),
        (SHOP_QUESTION_ASKED, _("Shop question")),
        (BUYER_CLARIFICATION_SENT, _("Buyer clarification")),
        (REQUEST_DECLINED, _("Request declined")),
        (QUOTE_REQUEST_CANCELLED, _("Request cancelled")),
        (JOB_STATUS_UPDATED, _("Job status updated")),
        (PAYMENT_CONFIRMED, _("Payment confirmed")),
        (ARTWORK_REQUIRED, _("Artwork required")),
        (JOB_READY_TO_START, _("Job ready to start")),
        (JOB_CREATED, _("Job created")),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("recipient"),
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications_triggered",
        verbose_name=_("actor"),
        help_text=_("User who triggered this event."),
    )
    notification_type = models.CharField(
        max_length=50,
        choices=TYPE_CHOICES,
        verbose_name=_("type"),
    )
    object_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("object type"),
        help_text=_("e.g. quote_request, quote, production_order"),
    )
    object_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("object id"),
    )
    message = models.TextField(
        default="",
        verbose_name=_("message"),
        help_text=_("Legacy fallback body, used when no template can be rendered."),
    )
    template_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name=_("template key"),
        help_text=_("Registry key used to render title/body per role and language."),
    )
    params = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_("params"),
        help_text=_("Structured values interpolated into the template at read time."),
    )
    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default=PRIORITY_INFORMATIONAL,
        verbose_name=_("priority"),
        help_text=_("``action_required`` items are pinned and accented in the UI."),
    )
    idempotency_key = models.CharField(
        max_length=200,
        blank=True,
        default="",
        db_index=True,
        verbose_name=_("idempotency key"),
        help_text=_("Dedupe key such as (event_type, entity_id, recipient)."),
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("read at"),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        indexes = [
            models.Index(fields=["user", "-created_at"], name="notif_user_created_idx"),
            models.Index(fields=["user", "read_at"], name="notif_user_read_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=~Q(idempotency_key=""),
                name="notif_user_idempotency_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.get_notification_type_display()} for {self.user}"

    @property
    def is_read(self):
        return self.read_at is not None

    @property
    def is_action_required(self):
        return self.priority == self.PRIORITY_ACTION_REQUIRED
