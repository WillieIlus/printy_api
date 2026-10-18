"""Provider-agnostic client messaging (law 10.5 / 10.6).

No vendor is hard-wired. A provider implements :class:`MessagingProvider`; the
default :class:`StubMessagingProvider` only **logs**, so nothing is sent in
development or tests. Every attempt — sent, failed or skipped for opt-out — is
written to :class:`~clients.models.MessageDelivery` with its status.
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


class MessagingProvider(ABC):
    """Interface every messaging provider must implement."""

    name = "base"

    @abstractmethod
    def send(self, *, recipient: str, body: str, channel: str) -> DeliveryResult:  # pragma: no cover - interface
        raise NotImplementedError


class StubMessagingProvider(MessagingProvider):
    """A provider that only logs. Chosen when no real provider is configured."""

    name = "stub"

    def send(self, *, recipient: str, body: str, channel: str) -> DeliveryResult:
        logger.info("stub_message channel=%s recipient=%s body=%s", channel, recipient, body)
        return DeliveryResult(ok=True, provider=self.name, message_id="stub")


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
) -> MessageDelivery:
    """Deliver (or skip) one client message and log the attempt.

    Opt-out is honoured before any provider call.
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
        body=build_client_message_body(message, manager_name=manager_name),
        attempted_at=timezone.now(),
    )

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
        result = provider.send(recipient=delivery.recipient, body=delivery.body, channel=channel)
    except Exception as exc:  # a provider failure is recorded, never raised to the caller
        delivery.status = MessageDelivery.Status.FAILED
        delivery.error = str(exc)[:255]
        delivery.save()
        return delivery

    if result.ok:
        delivery.status = MessageDelivery.Status.SENT
        delivery.provider_message_id = result.message_id
        delivery.sent_at = timezone.now()
    else:
        delivery.status = MessageDelivery.Status.FAILED
        delivery.error = str(result.error or "")[:255]
    delivery.save()
    return delivery


__all__ = [
    "DeliveryResult",
    "MessagingProvider",
    "StubMessagingProvider",
    "get_provider",
    "client_has_opted_out",
    "set_client_opt_out",
    "build_client_message_body",
    "send_client_message",
]
