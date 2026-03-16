"""
Notification model for quote marketplace events.
"""
from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Notification(models.Model):
    """
    In-app notification for quote-related events.
    Recipient = user. Actor = who triggered (optional).
    """

    QUOTE_REQUEST_SUBMITTED = "quote_request_submitted"
    SHOP_QUOTE_SENT = "shop_quote_sent"
    SHOP_QUOTE_REVISED = "shop_quote_revised"
    SHOP_QUOTE_ACCEPTED = "shop_quote_accepted"
    REQUEST_DECLINED = "request_declined"
    QUOTE_REQUEST_CANCELLED = "quote_request_cancelled"
    JOB_STATUS_UPDATED = "job_status_updated"
    TYPE_CHOICES = [
        (QUOTE_REQUEST_SUBMITTED, _("New quote request")),
        (SHOP_QUOTE_SENT, _("Quote sent")),
        (SHOP_QUOTE_REVISED, _("Quote revised")),
        (SHOP_QUOTE_ACCEPTED, _("Quote accepted")),
        (REQUEST_DECLINED, _("Request declined")),
        (QUOTE_REQUEST_CANCELLED, _("Request cancelled")),
        (JOB_STATUS_UPDATED, _("Job status updated")),
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
        help_text=_("e.g. quote_request, shop_quote, production_order"),
    )
    object_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("object id"),
    )
    message = models.TextField(
        default="",
        verbose_name=_("message"),
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

    def __str__(self):
        return f"{self.get_notification_type_display()} for {self.user}"

    @property
    def is_read(self):
        return self.read_at is not None
