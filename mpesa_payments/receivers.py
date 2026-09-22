"""Signal receivers that settle the payable side of an M-Pesa payment.

The `mpesa_payments` module is deliberately cashier-only: it records money
movement and emits signals so the rest of the codebase can react without the
module importing application models. This file is that reaction — it advances
the ManagedJob payment state and notifies the client, mirroring what the
legacy `payments.services.mark_payment_paid` flow did.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone

from notifications.models import Notification
from notifications.services import notify
from .models import MpesaPayment
from .signals import mpesa_payment_confirmed, mpesa_payment_failed

logger = logging.getLogger("payments")


def _notify_once(**kwargs) -> None:
    if Notification.objects.filter(
        user=kwargs["recipient"],
        notification_type=kwargs["notification_type"],
        object_type=kwargs.get("object_type", ""),
        object_id=kwargs.get("object_id"),
    ).exists():
        return
    notify(**kwargs)


def _managed_job_client_user(managed_job):
    """Resolve the buyer for a managed job, falling back to the quote request."""
    if managed_job is None:
        return None
    if managed_job.client_id:
        return managed_job.client
    quote_request = getattr(managed_job, "source_quote_request", None)
    if quote_request is None:
        quote = getattr(managed_job, "source_quote", None)
        quote_request = getattr(quote, "quote_request", None) if quote else None
    if quote_request is None:
        return None
    return (
        getattr(quote_request, "on_behalf_of", None)
        or getattr(quote_request, "created_by", None)
    )


def _managed_job_title(managed_job) -> str:
    if managed_job is None:
        return "your print job"
    return getattr(managed_job, "title", "") or f"job #{managed_job.id}"


def _settle_managed_job(payment: MpesaPayment) -> None:
    """Advance a paid ManagedJob to payment_confirmed and notify the buyer."""
    from jobs.audit_services import EVENT_PAYMENT_CONFIRMED, record_job_status_event
    from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
    from jobs.models import ManagedJob

    managed_job = (
        ManagedJob.objects.filter(pk=payment.object_id)
        .select_related("client", "source_quote_request", "source_quote")
        .first()
    )
    if managed_job is None:
        logger.warning(
            "No ManagedJob found for confirmed M-Pesa payment_id=%s object_id=%s",
            payment.id,
            payment.object_id,
        )
        return

    title = _managed_job_title(managed_job)
    if managed_job.payment_status != ManagedJobPaymentStatus.CONFIRMED:
        managed_job.payment_status = ManagedJobPaymentStatus.CONFIRMED
        managed_job.payment_confirmed_at = payment.confirmed_at or timezone.now()
        update_fields = ["payment_status", "payment_confirmed_at", "updated_at"]
        if managed_job.status in {
            ManagedJobStatus.AWAITING_PAYMENT,
            ManagedJobStatus.DRAFT,
            ManagedJobStatus.QUOTED,
        }:
            managed_job.status = ManagedJobStatus.PAYMENT_CONFIRMED
            update_fields.append("status")
        managed_job.save(update_fields=update_fields)
        logger.info(
            "Confirmed M-Pesa payment settled managed_job_id=%s payment_id=%s receipt=%s",
            managed_job.id,
            payment.id,
            payment.mpesa_receipt_number,
        )

    client_user = _managed_job_client_user(managed_job)
    if client_user:
        _notify_once(
            recipient=client_user,
            notification_type=Notification.PAYMENT_CONFIRMED,
            message=f'Your payment of KES {payment.amount} for "{title}" has been confirmed. Your job is being prepared.',
            object_type="managed_job",
            object_id=managed_job.id,
            actor=payment.user,
            send_email_notification=True,
            email_subject="Printy - Payment Confirmed",
            email_message=f'Your payment of KES {payment.amount} for "{title}" has been confirmed. Your print job is now being prepared.',
        )

    broker = getattr(managed_job, "broker", None)
    if broker and broker.id != getattr(client_user, "id", None):
        _notify_once(
            recipient=broker,
            notification_type=Notification.JOB_READY_TO_START,
            message=f'"{title}" is paid and artwork is attached. You can start production.',
            object_type="managed_job",
            object_id=managed_job.id,
            actor=payment.user,
        )

    if not managed_job.events.filter(
        event_type=EVENT_PAYMENT_CONFIRMED, metadata__payment_id=payment.id
    ).exists():
        try:
            record_job_status_event(
                managed_job=managed_job,
                event_type=EVENT_PAYMENT_CONFIRMED,
                actor=payment.user,
                summary=f"M-Pesa payment confirmed ({payment.mpesa_receipt_number or 'receipt pending'}).",
                metadata={"payment_id": payment.id, "receipt": payment.mpesa_receipt_number},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record payment-confirmed event managed_job_id=%s: %s", managed_job.id, exc)


@receiver(mpesa_payment_confirmed, sender=MpesaPayment)
@transaction.atomic
def on_mpesa_payment_confirmed(sender, payment, **kwargs) -> None:
    """Mark the paid object's state and notify the buyer.

    Runs inside the callback's transaction (via transaction.on_commit) so a
    settlement can never be half-applied.
    """
    try:
        content_type = getattr(payment, "content_type", None)
        if content_type is None:
            return
        app_label = getattr(content_type, "app_label", "").lower()
        model_name = getattr(content_type, "model", "").lower()

        if app_label == "jobs" and model_name == "managedjob":
            _settle_managed_job(payment)
            return

        logger.info(
            "Confirmed M-Pesa payment with unhandled payable app=%s model=%s payment_id=%s",
            app_label,
            model_name,
            payment.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("M-Pesa confirmed receiver failed payment_id=%s: %s", payment.id, exc)


@receiver(mpesa_payment_failed, sender=MpesaPayment)
def on_mpesa_payment_failed(sender, payment, **kwargs) -> None:
    """The payment did not complete — nothing to settle, just log.

    A cancelled/failed callback only moves the MpesaPayment row; there is no
    state to advance and the legacy flow did not emit failure notifications.
    """
    logger.info(
        "M-Pesa payment did not complete payment_id=%s status=%s result_code=%s",
        payment.id,
        payment.status,
        payment.result_code or "",
    )