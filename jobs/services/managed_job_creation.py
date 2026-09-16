from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from jobs.audit_services import EVENT_MANAGED_JOB_CREATED, EVENT_PAYMENT_CONFIRMED, record_job_status_event
from jobs.choices import ManagedJobAssignmentStatus, ManagedJobPaymentStatus, ManagedJobStatus
from jobs.file_services import import_legacy_files_to_managed_job, sync_managed_job_artwork_requirement
from jobs.models import ManagedJob
from jobs.services.dispatch import ensure_job_assignment_for_paid_job
from notifications.models import Notification
from notifications.services import notify, notify_quote_event
from payments.models import Payment


logger = logging.getLogger(__name__)


def _client_for_quote_request(quote_request):
    if quote_request is None:
        return None
    return quote_request.on_behalf_of or quote_request.created_by


def _quote_request_title(quote_request) -> str:
    if quote_request is None:
        return "managed job"
    return (
        getattr(quote_request, "title", "")
        or getattr(quote_request, "project_title", "")
        or getattr(quote_request, "customer_name", "")
        or f"quote request #{quote_request.id}"
    )


def _notify_once(**kwargs) -> None:
    if Notification.objects.filter(
        user=kwargs["recipient"],
        notification_type=kwargs["notification_type"],
        object_type=kwargs.get("object_type", ""),
        object_id=kwargs.get("object_id"),
    ).exists():
        return
    notify(**kwargs)


def _notify_payment_confirmed(*, managed_job: ManagedJob, payment: Payment) -> None:
    recipients = [
        (
            managed_job.broker,
            f"Payment confirmed for {managed_job.managed_reference or 'managed job'}. It is ready for dispatch.",
        ),
        (
            managed_job.client,
            f"Your payment for {managed_job.managed_reference or 'managed job'} has been confirmed.",
        ),
    ]
    for recipient, message in recipients:
        if not recipient:
            continue
        if Notification.objects.filter(
            user=recipient,
            notification_type=Notification.JOB_STATUS_UPDATED,
            object_type="managed_job",
            object_id=managed_job.id,
            message=message,
        ).exists():
            continue
        notify_quote_event(
            recipient=recipient,
            notification_type=Notification.JOB_STATUS_UPDATED,
            message=message,
            object_type="managed_job",
            object_id=managed_job.id,
            actor=payment.payer,
        )


def _notify_managed_job_created(*, managed_job: ManagedJob, payment: Payment) -> None:
    try:
        quote = getattr(payment, "quote", None) or getattr(managed_job, "source_quote", None)
        quote_request = getattr(quote, "quote_request", None) or getattr(managed_job, "source_quote_request", None)
        title = _quote_request_title(quote_request)
        client_user = (
            getattr(managed_job, "client", None)
            or getattr(quote_request, "on_behalf_of", None)
            or getattr(quote_request, "created_by", None)
        )
        manager_user = (
            getattr(managed_job, "broker", None)
            or getattr(quote_request, "assigned_manager", None)
            or getattr(quote, "created_by", None)
        )

        if not manager_user:
            logger.warning("Skipping job-created manager notification; manager user could not be resolved managed_job_id=%s", managed_job.id)
        else:
            _notify_once(
                recipient=manager_user,
                notification_type=Notification.JOB_CREATED,
                message=f'Payment confirmed for "{title}". A new managed job has been created.',
                object_type="managed_job",
                object_id=managed_job.id,
                actor=payment.payer,
                send_email_notification=True,
                email_subject="Printy - New Job Created",
                email_message=f'Payment has been confirmed for "{title}". A managed job has been created and is ready for your attention.',
            )

        has_artwork = managed_job.job_files.exists() if hasattr(managed_job, "job_files") else False
        if not has_artwork:
            if not client_user:
                logger.warning("Skipping artwork-required client notification; client user could not be resolved managed_job_id=%s", managed_job.id)
            else:
                _notify_once(
                    recipient=client_user,
                    notification_type=Notification.ARTWORK_REQUIRED,
                    message=f'Please upload artwork for "{title}" so production can begin.',
                    object_type="managed_job",
                    object_id=managed_job.id,
                    actor=payment.payer,
                    send_email_notification=True,
                    email_subject="Printy - Artwork Needed",
                    email_message=f'Your payment is confirmed but we need your artwork files for "{title}" before production can start. Please upload them in your dashboard.',
                )
            if manager_user:
                _notify_once(
                    recipient=manager_user,
                    notification_type=Notification.ARTWORK_REQUIRED,
                    message=f'Artwork is missing for "{title}". The client has been asked to upload it.',
                    object_type="managed_job",
                    object_id=managed_job.id,
                    actor=payment.payer,
                )
            return

        if manager_user:
            _notify_once(
                recipient=manager_user,
                notification_type=Notification.JOB_READY_TO_START,
                message=f'"{title}" is paid and artwork is attached. You can start production.',
                object_type="managed_job",
                object_id=managed_job.id,
                actor=payment.payer,
                send_email_notification=True,
                email_subject="Printy - Job Ready to Start",
                email_message=f'Payment is confirmed and artwork is attached for "{title}". You can now start production.',
            )
    except Exception as exc:
        logger.warning("Managed job creation notification failed managed_job_id=%s payment_id=%s: %s", managed_job.id, payment.id, exc)


@transaction.atomic
def create_managed_job_from_payment(*, payment: Payment) -> ManagedJob:
    payment = Payment.objects.select_for_update(of=("self",)).select_related(
        "quote",
        "quote__quote_request",
        "quote__production_option",
        "quote__production_option__shop",
        "payer",
    ).get(pk=payment.pk)
    if payment.status != Payment.STATUS_PAID:
        raise ValidationError(f"Cannot create managed job for payment in status {payment.status}.")
    quote = payment.quote
    if quote is None:
        raise ValidationError(f"Payment {payment.id} has no quote.")
    split = getattr(quote, "financial_split", None)
    if split is None:
        raise ValidationError("Paid quote must have QuoteFinancialSplit.")

    managed_job = ManagedJob.objects.select_for_update().filter(source_quote=quote).first()
    if managed_job is None:
        quote_request = quote.quote_request
        production_option = quote.production_option
        assigned_shop = getattr(production_option, "shop", None) or getattr(quote, "shop", None)
        managed_job = ManagedJob.objects.create(
            title=quote.note[:255] if quote.note else f"Managed job from quote {quote.quote_reference or quote.id}",
            source_quote_request=quote_request,
            source_quote=quote,
            client=_client_for_quote_request(quote_request),
            broker=getattr(quote_request, "assigned_manager", None),
            assigned_shop=assigned_shop,
            created_by=payment.payer,
            status=ManagedJobStatus.PAYMENT_CONFIRMED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            assignment_status=ManagedJobAssignmentStatus.UNASSIGNED,
            client_total=split.client_total,
            operational_snapshot={
                "source": "payment_confirmed",
                "quote_id": quote.id,
                "quote_request_id": quote_request.id if quote_request else None,
                "production_option_id": production_option.id if production_option else None,
                "assigned_shop_id": assigned_shop.id if assigned_shop else None,
            },
            workflow_metadata={
                "created_from": "payment_confirmed",
                "payment_id": payment.id,
            },
            accepted_at=quote.accepted_at,
            payment_confirmed_at=payment.confirmed_at or timezone.now(),
        )
        import_legacy_files_to_managed_job(
            managed_job=managed_job,
            quote_request=quote_request,
            quote=quote,
        )
        sync_managed_job_artwork_requirement(managed_job=managed_job)
        record_job_status_event(
            managed_job=managed_job,
            actor=payment.payer,
            event_type=EVENT_MANAGED_JOB_CREATED,
            summary="Managed job created after canonical payment confirmation.",
            metadata={"quote_id": quote.id, "payment_id": payment.id},
        )
    else:
        update_fields = ["updated_at"]
        if managed_job.status in {ManagedJobStatus.DRAFT, ManagedJobStatus.QUOTED, ManagedJobStatus.AWAITING_PAYMENT}:
            managed_job.status = ManagedJobStatus.PAYMENT_CONFIRMED
            update_fields.append("status")
        if managed_job.payment_status != ManagedJobPaymentStatus.CONFIRMED:
            managed_job.payment_status = ManagedJobPaymentStatus.CONFIRMED
            update_fields.append("payment_status")
        if managed_job.payment_confirmed_at is None:
            managed_job.payment_confirmed_at = payment.confirmed_at or timezone.now()
            update_fields.append("payment_confirmed_at")
        managed_job.save(update_fields=update_fields)

    if payment.managed_job_id != managed_job.id:
        payment.managed_job = managed_job
        payment.save(update_fields=["managed_job", "updated_at"])

    if not managed_job.events.filter(event_type=EVENT_PAYMENT_CONFIRMED, metadata__payment_id=payment.id).exists():
        record_job_status_event(
            managed_job=managed_job,
            actor=payment.payer,
            event_type=EVENT_PAYMENT_CONFIRMED,
            summary="Canonical payment confirmed for managed job.",
            metadata={"payment_id": payment.id, "quote_id": quote.id},
        )
    _notify_payment_confirmed(managed_job=managed_job, payment=payment)
    _notify_managed_job_created(managed_job=managed_job, payment=payment)

    if managed_job.assigned_shop_id and managed_job.assignment_status == ManagedJobAssignmentStatus.UNASSIGNED:
        dispatched_by = (
            managed_job.broker
            or getattr(quote.quote_request, "assigned_manager", None)
            or getattr(quote, "created_by", None)
        )
        if dispatched_by is None:
            logger.warning(
                "Auto-dispatch actor could not be resolved for managed_job_id=%s; dispatching with no actor",
                managed_job.id,
            )

        def auto_dispatch_after_commit() -> None:
            try:
                assignment = ensure_job_assignment_for_paid_job(
                    managed_job=managed_job,
                    dispatched_by=dispatched_by,
                    notes="Auto-dispatched after payment confirmation",
                )
                if assignment is not None:
                    logger.info(
                        "Auto-dispatched managed_job_id=%s to shop_id=%s after payment confirmation",
                        managed_job.id,
                        assignment.assigned_shop_id,
                    )
            except Exception as exc:
                logger.exception(
                    "Auto-dispatch failed for managed_job_id=%s: %s",
                    managed_job.id,
                    exc,
                )

        transaction.on_commit(auto_dispatch_after_commit)
    return managed_job
