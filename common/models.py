from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """Abstract base model with created_at and updated_at timestamps."""

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
        help_text=_("Timestamp when the record was created."),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("updated at"),
        help_text=_("Timestamp when the record was last updated."),
    )

    class Meta:
        abstract = True


class AnalyticsEvent(models.Model):
    """Immutable analytics event for product, quote, and admin reporting."""

    class EventType(models.TextChoices):
        PAGE_VIEW = "page_view", _("Page view")
        SEARCH = "search", _("Search")
        PRODUCT_VIEW = "product_view", _("Product view")
        SHOP_VIEW = "shop_view", _("Shop view")
        QUOTE_START = "quote_start", _("Quote start")
        QUOTE_SUBMIT = "quote_submit", _("Quote submit")
        LOGIN = "login", _("Login")
        SIGNUP = "signup", _("Signup")
        API_ERROR = "api_error", _("API error")
        FRONTEND_ERROR = "frontend_error", _("Frontend error")

    event_type = models.CharField(
        max_length=32,
        choices=EventType.choices,
        verbose_name=_("event type"),
        help_text=_("Normalized analytics event type."),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analytics_events",
        verbose_name=_("user"),
        help_text=_("Authenticated user tied to this event, if any."),
    )
    session_key = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name=_("session key"),
        help_text=_("Opaque session identifier when available."),
    )
    visitor_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        verbose_name=_("visitor id"),
        help_text=_("Anonymous visitor identifier from client headers or cookies."),
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        unpack_ipv4=True,
        verbose_name=_("IP address"),
        help_text=_("Client IP address as observed by the backend."),
    )
    user_agent = models.CharField(
        max_length=512,
        blank=True,
        default="",
        verbose_name=_("user agent"),
        help_text=_("Client user agent string."),
    )
    referer = models.CharField(
        max_length=1024,
        blank=True,
        default="",
        verbose_name=_("referer"),
        help_text=_("HTTP referer or frontend route context."),
    )
    path = models.CharField(
        max_length=1024,
        blank=True,
        default="",
        verbose_name=_("path"),
        help_text=_("Request path or frontend route."),
    )
    method = models.CharField(
        max_length=16,
        blank=True,
        default="",
        verbose_name=_("method"),
        help_text=_("HTTP method when event came from a request."),
    )
    query_params = models.JSONField(
        blank=True,
        default=dict,
        verbose_name=_("query params"),
        help_text=_("Normalized request query parameters."),
    )
    metadata = models.JSONField(
        blank=True,
        default=dict,
        verbose_name=_("metadata"),
        help_text=_("Non-PII event metadata for analytics slices."),
    )
    country = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("country"),
        help_text=_("Resolved country for coarse analytics."),
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("city"),
        help_text=_("Resolved city for coarse analytics."),
    )
    region = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("region"),
        help_text=_("Resolved region or state for coarse analytics."),
    )
    status_code = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name=_("status code"),
        help_text=_("HTTP status code when the event maps to a response."),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
        help_text=_("Timestamp when the event was recorded."),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("analytics event")
        verbose_name_plural = _("analytics events")
        indexes = [
            models.Index(fields=["event_type", "-created_at"], name="analytics_type_created_idx"),
            models.Index(fields=["user", "-created_at"], name="analytics_user_created_idx"),
            models.Index(fields=["session_key", "-created_at"], name="analytics_session_created_idx"),
            models.Index(fields=["visitor_id", "-created_at"], name="analytics_visitor_created_idx"),
            models.Index(fields=["path", "-created_at"], name="analytics_path_created_idx"),
            models.Index(fields=["event_type", "path", "-created_at"], name="analytics_type_path_ct_idx"),
            models.Index(fields=["ip_address", "-created_at"], name="analytics_ip_created_idx"),
            models.Index(fields=["event_type", "country", "city"], name="analytics_type_geo_idx"),
            models.Index(fields=["status_code", "-created_at"], name="analytics_status_created_idx"),
            models.Index(fields=["country", "region", "city"], name="analytics_geo_idx"),
            models.Index(fields=["-created_at"], name="analytics_created_idx"),
        ]

    def __str__(self):
        subject = self.user.email if self.user_id and getattr(self.user, "email", "") else self.visitor_id or "anonymous"
        return f"{self.event_type} @ {self.path or '-'} ({subject})"
