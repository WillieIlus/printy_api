"""Provider-agnostic client messaging (law 10.5 / 10.6).

No vendor is hard-wired. A provider implements :class:`MessagingProvider`; the
default :class:`StubMessagingProvider` sends nothing. Every attempt — sent,
queued, failed or skipped for opt-out — is written to
:class:`~clients.models.MessageDelivery` with its status.

Audit #7:

* The stub used to log the full message body (including plaintext OTP codes)
  and record the delivery as ``SENT``. It now logs only the channel, a masked
  recipient and the body length, and records the delivery as ``QUEUED`` (not
  sent) in dev/test. Outside dev/test there is no stub fallback: the delivery
  is recorded as ``FAILED`` and an error is logged, so callers (the OTP
  request view) can tell the client the code was not sent.
* Callers pass ``redact=`` for secrets in the body (the OTP code). The real
  body goes to the provider; the stored ``MessageDelivery.body`` has them
  masked.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from clients.models import ClientContactPreference, MessageDelivery
from clients.services.phone import normalize_client_phone

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    ok: bool
    provider: str
    message_id: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)
    # True when the provider accepted the message without delivering it (the
    # dev/test stub). The delivery is recorded as QUEUED, never SENT.
    queued: bool = False


STUB_NOT_ALLOWED_ERROR = "No SMS/WhatsApp provider is configured; the message was not sent."


def stub_messaging_allowed() -> bool:
    """The logging stub may stand in for a provider only in dev and tests."""
    explicit = getattr(settings, "MESSAGE_STUB_ALLOWED", None)
    if explicit is not None:
        return bool(explicit)
    app_env = str(getattr(settings, "APP_ENV", "") or "").strip().lower()
    return bool(getattr(settings, "DEBUG", False)) or app_env in {"local", "dev", "development", "test"}


def mask_recipient(recipient: str) -> str:
    value = str(recipient or "")
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def redact_text(text: str, secrets_to_hide=()) -> str:
    result = str(text or "")
    for secret in secrets_to_hide or ():
        secret = str(secret or "")
        if secret:
            result = result.replace(secret, "*" * len(secret))
    return result


class MessagingProvider(ABC):
    """Interface every messaging provider must implement."""

    name = "base"

    @abstractmethod
    def send(self, *, recipient: str, body: str, channel: str) -> DeliveryResult:  # pragma: no cover - interface
        raise NotImplementedError


class StubMessagingProvider(MessagingProvider):
    """Stands in when no real provider is configured. It never sends.

    In dev/test the message is reported as queued (not sent). Anywhere else it
    fails, so an unsent OTP or link is never recorded as delivered. The body is
    never logged: it can hold a one-time code.
    """

    name = "stub"

    def send(self, *, recipient: str, body: str, channel: str) -> DeliveryResult:
        if not stub_messaging_allowed():
            logger.error(
                "stub_message_refused channel=%s recipient=%s: %s",
                channel,
                mask_recipient(recipient),
                STUB_NOT_ALLOWED_ERROR,
            )
            return DeliveryResult(ok=False, provider=self.name, error=STUB_NOT_ALLOWED_ERROR)
        logger.info(
            "stub_message_not_sent channel=%s recipient=%s body_length=%s",
            channel,
            mask_recipient(recipient),
            len(body or ""),
        )
        return DeliveryResult(ok=True, provider=self.name, queued=True)


def get_provider() -> MessagingProvider:
    """Return the configured provider. Only the stub exists today."""
    configured = str(getattr(settings, "MESSAGE_PROVIDER", "stub") or "stub").strip().lower()
    # Only the stub is implemented; any other name still resolves to the stub so
    # the absence of a real provider is explicit at call time, never silent.
    if configured == "stub":
        return StubMessagingProvider()
    logger.warning("Unknown MESSAGE_PROVIDER %r; falling back to the logging stub.", configured)
    return StubMessagingProvider()


def client_has_opted_out(phone_normalized: str) -> bool:
    phone = str(phone_normalized or "").strip()
    if not phone:
        return False
    preference = ClientContactPreference.objects.filter(phone_normalized=phone).first()
    return bool(preference and preference.opted_out)


def set_client_opt_out(phone_normalized: str, opted_out: bool = True) -> ClientContactPreference:
    phone = normalize_client_phone(phone_normalized)
    preference, _ = ClientContactPreference.objects.get_or_create(phone_normalized=phone)
    preference.opted_out = bool(opted_out)
    preference.opted_out_at = timezone.now() if opted_out else None
    preference.save(update_fields=["opted_out", "opted_out_at", "updated_at"])
    return preference


def build_client_message_body(message: str, *, manager_name: str = "") -> str:
    """Wrap a message so it is clear it comes from Printy on behalf of a manager."""
    manager_name = str(manager_name or "").strip()
    attribution = (
        f"This message is from Printy on behalf of {manager_name}."
        if manager_name
        else "This message is from Printy on behalf of your print manager."
    )
    return f"{attribution} {str(message or '').strip()}".strip()


def send_client_message(
    *,
    recipient: str,
    message: str,
    channel: str = MessageDelivery.Channel.SMS,
    manager_name: str = "",
    quote_request=None,
    client=None,
    redact=(),
) -> MessageDelivery:
    """Deliver (or skip) one client message and log the attempt.

    Opt-out is honoured before any provider call. Strings in ``redact`` (an
    OTP code) are masked in the stored body and in any error text.
    """
    try:
        normalized = normalize_client_phone(recipient)
    except Exception:
        normalized = str(recipient or "").strip()

    delivery = MessageDelivery(
        quote_request=quote_request,
        client=client,
        recipient=normalized or str(recipient or "").strip(),
        channel=channel,
        body=redact_text(build_client_message_body(message, manager_name=manager_name), redact),
        attempted_at=timezone.now(),
    )
    provider_body = build_client_message_body(message, manager_name=manager_name)

    if normalized and ClientContactPreference.objects.filter(
        phone_normalized=normalized, opted_out=True
    ).exists():
        delivery.status = MessageDelivery.Status.SKIPPED_OPT_OUT
        delivery.provider = ""
        delivery.save()
        return delivery

    provider = get_provider()
    delivery.provider = provider.name
    try:
        result = provider.send(recipient=delivery.recipient, body=provider_body, channel=channel)
    except Exception as exc:  # a provider failure is recorded, never raised to the caller
        delivery.status = MessageDelivery.Status.FAILED
        delivery.error = redact_text(str(exc), redact)[:255]
        delivery.save()
        return delivery

    if result.ok and result.queued:
        # Accepted but not delivered (dev/test stub): never claim SENT.
        delivery.status = MessageDelivery.Status.QUEUED
        delivery.provider_message_id = result.message_id
    elif result.ok:
        delivery.status = MessageDelivery.Status.SENT
        delivery.provider_message_id = result.message_id
        delivery.sent_at = timezone.now()
    else:
        delivery.status = MessageDelivery.Status.FAILED
        delivery.error = redact_text(str(result.error or ""), redact)[:255]
    delivery.save()
    return delivery


__all__ = [
    "DeliveryResult",
    "MessagingProvider",
    "StubMessagingProvider",
    "get_provider",
    "mask_recipient",
    "redact_text",
    "stub_messaging_allowed",
    "client_has_opted_out",
    "set_client_opt_out",
    "build_client_message_body",
    "send_client_message",
]
