"""Law 10.4-10.6 data model: client identity, OTP, single-job links, messaging.

These models back the phone-first client flow:

* :class:`ClientLink`  — a signed, single-job client link (law 10.6).
* :class:`ClientOtp`   — a hashed, expiring, attempt-limited one-time code (law 10.4).
* :class:`MessageDelivery` — a logged delivery attempt and its status (law 10.6).
* :class:`ClientContactPreference` — the honoured opt-out (law 10.5).

Client identity itself lives on ``accounts.UserProfile`` keyed by the normalised
phone; this app never stores a second copy of the phone as the identity.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel

CLIENT_LINK_SALT = "printy.client-link"


def link_expiry_days() -> int:
    return int(getattr(settings, "LINK_EXPIRY_DAYS", 30))


def default_link_expiry():
    return timezone.now() + timedelta(days=link_expiry_days())


def _new_jti() -> str:
    return secrets.token_urlsafe(24)


class ClientLink(TimeStampedModel):
    """A signed, single-job client link (law 10.6).

    The public token is the signed payload; the database stores only the opaque
    ``jti`` it carries, so the token cannot be forged and can be revoked by the
    manager. Creating a new link for the same request replaces the old one, and
    every use is counted and timestamped.
    """

    quote_request = models.ForeignKey(
        "quotes.QuoteRequest",
        on_delete=models.CASCADE,
        related_name="client_links",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_links_created",
    )
    jti = models.CharField(max_length=64, unique=True, default=secrets.token_urlsafe)
    expires_at = models.DateTimeField(default=default_link_expiry)
    revoked_at = models.DateTimeField(null=True, blank=True)
    replaced_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["quote_request", "created_at"]),
        ]

    def __str__(self):
        return f"Client link for request {self.quote_request_id} ({'active' if self.is_active else 'inactive'})"

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_replaced(self) -> bool:
        return self.replaced_at is not None

    @property
    def is_active(self) -> bool:
        return not (self.revoked_at or self.replaced_at or self.is_expired)

    @property
    def token(self) -> str:
        """The signed, shareable token string (law 10.6: signed token)."""
        return signing.dumps({"jti": self.jti}, salt=CLIENT_LINK_SALT, compress=True)

    @property
    def url(self) -> str:
        base = str(getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
        path = f"/client/{self.token}"
        return f"{base}{path}" if base else path

    @classmethod
    def resolve(cls, token: str, *, max_age_seconds: int | None = None):
        """Return the :class:`ClientLink` for a signed token, or ``None``.

        Tampered, malformed or (optionally) expired signatures return ``None``.
        """
        token = str(token or "").strip()
        if not token:
            return None
        if max_age_seconds is None:
            max_age_seconds = link_expiry_days() * 24 * 60 * 60
        try:
            data = signing.loads(token, salt=CLIENT_LINK_SALT, max_age=max_age_seconds)
        except signing.BadSignature:
            return None
        jti = str((data or {}).get("jti") or "").strip()
        if not jti:
            return None
        return cls.objects.filter(jti=jti).select_related("quote_request").first()

    @property
    def is_active_now(self) -> bool:
        return self.revoked_at is None and self.replaced_at is None and not self.is_expired


class ClientLinkUse(TimeStampedModel):
    """Every open of a client link is logged (law 10.6)."""

    link = models.ForeignKey(ClientLink, on_delete=models.CASCADE, related_name="uses")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True, default="")
    opened_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-opened_at", "-id"]

    def __str__(self):
        return f"use of link {self.link_id} at {self.opened_at:%Y-%m-%d %H:%M}"


class ClientOtp(TimeStampedModel):
    """A hashed, expiring one-time code for a client phone (law 10.4).

    The plaintext code is never stored; only a salted hash. Verification marks
    the challenge verified and issues a short ``verified_token`` that the
    link-scoped action endpoints require.
    """

    class Purpose(models.TextChoices):
        CLIENT_ACTIONS = "client_actions", "Client actions"

    quote_request = models.ForeignKey(
        "quotes.QuoteRequest",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="client_otps",
    )
    phone_normalized = models.CharField(max_length=16, db_index=True)
    purpose = models.CharField(max_length=32, choices=Purpose.choices, default=Purpose.CLIENT_ACTIONS)
    code_hash = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    attempt_count = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    resend_count = models.PositiveIntegerField(default=0)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_token = models.CharField(max_length=64, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["phone_normalized", "purpose", "created_at"]),
        ]

    def __str__(self):
        return f"OTP for {self.phone_normalized} ({self.purpose})"

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @property
    def is_locked(self) -> bool:
        return self.attempt_count >= self.max_attempts


class ClientContactPreference(TimeStampedModel):
    """Client opt-out from manager-initiated messages (law 10.5)."""

    phone_normalized = models.CharField(max_length=16, unique=True)
    opted_out = models.BooleanField(default=False)
    opted_out_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.phone_normalized} opted_out={self.opted_out}"


class MessageDelivery(TimeStampedModel):
    """A logged client-message delivery attempt and its status (law 10.6)."""

    class Channel(models.TextChoices):
        SMS = "sms", "SMS"
        WHATSAPP = "whatsapp", "WhatsApp"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SKIPPED_OPT_OUT = "skipped_opt_out", "Skipped (opt-out)"

    quote_request = models.ForeignKey(
        "quotes.QuoteRequest",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="message_deliveries",
    )
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="client_messages",
    )
    recipient = models.CharField(max_length=32)
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.SMS)
    provider = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    body = models.TextField(blank=True, default="")
    error = models.CharField(max_length=255, blank=True, default="")
    provider_message_id = models.CharField(max_length=128, blank=True, default="")
    attempted_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.channel}->{self.recipient} [{self.status}]"


def new_verified_token() -> str:
    return secrets.token_urlsafe(32)


def otp_ttl() -> timedelta:
    return timedelta(minutes=int(getattr(settings, "OTP_TTL_MINUTES", 10)))


__all__ = [
    "CLIENT_LINK_SALT",
    "ClientLink",
    "ClientLinkUse",
    "ClientOtp",
    "ClientContactPreference",
    "MessageDelivery",
    "new_verified_token",
    "otp_ttl",
    "link_expiry_days",
]
