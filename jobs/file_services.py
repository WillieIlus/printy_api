"""Managed job file ownership and visibility helpers."""

from __future__ import annotations

import logging
import os
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string

from api.visibility import CLIENT_ACTOR, OPS_ACTOR, PARTNER_ACTOR, SHOP_ACTOR
from jobs.audit_services import (
    EVENT_FILE_REPLACED,
    EVENT_FILE_UPLOADED,
    EVENT_PROOF_APPROVED,
    EVENT_PROOF_REJECTED,
    EVENT_REVISION_REQUESTED,
    record_job_status_event,
)
from jobs.choices import JobFileStatus, JobFileType, JobFileVisibility
from jobs.models import JobAssignment, JobFile, ManagedJob
from notifications.models import Notification
from notifications.services import notify
from quotes.models import QuoteRequest, QuoteRequestAttachment, Quote, QuoteAttachment


ALLOWED_ARTWORK_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".ai", ".eps"}
MAX_ARTWORK_FILE_BYTES = 50 * 1024 * 1024


logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _filename_for_field(file_field: Any) -> str:
    if not file_field:
        return ""
    name = getattr(file_field, "name", "") or str(file_field)
    return os.path.basename(name)


def _stored_name(file_field: Any) -> str | None:
    if not file_field:
        return None
    name = getattr(file_field, "name", "") or None
    return name or None


def validate_artwork_upload_file(*, file, original_filename: str = "") -> str:
    filename = os.path.basename(original_filename or getattr(file, "name", "") or "")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_ARTWORK_EXTENSIONS:
        raise ValueError("Unsupported artwork file type. Upload JPG, PNG, PDF, AI, or EPS.")
    size = int(getattr(file, "size", 0) or 0)
    if size <= 0:
        raise ValueError("Artwork file is empty. Choose another file.")
    if size > MAX_ARTWORK_FILE_BYTES:
        raise ValueError("Artwork files must be 50MB or smaller.")
    return filename


def managed_job_has_artwork(*, managed_job: ManagedJob) -> bool:
    return managed_job.job_files.filter(
        file_type__in=[JobFileType.ARTWORK, JobFileType.CUSTOMER_UPLOAD],
    ).exists()


def managed_job_artwork_state(*, managed_job: ManagedJob) -> dict[str, Any]:
    has_artwork = managed_job_has_artwork(managed_job=managed_job)
    payment_confirmed = str(getattr(managed_job, "payment_status", "") or "").lower() in {
        "confirmed",
        "release_ready",
        "released",
    }
    artwork_missing = not has_artwork
    artwork_required = bool(managed_job.artwork_required or artwork_missing)
    can_dispatch = payment_confirmed and has_artwork and managed_job.dispatched_at is None

    if has_artwork:
        status_label = "Artwork uploaded"
    elif payment_confirmed:
        status_label = "Artwork required before dispatch"
    elif artwork_required:
        status_label = "Artwork still needed"
    else:
        status_label = "Artwork optional for now"

    return {
        "artwork_uploaded": has_artwork,
        "artwork_required": artwork_required,
        "artwork_missing": artwork_missing,
        "can_dispatch": can_dispatch,
        "artwork_status_label": status_label,
        "artwork_reminder_sent": bool(getattr(managed_job, "artwork_reminder_sent", False)),
    }


def sync_managed_job_artwork_requirement(*, managed_job: ManagedJob) -> bool:
    has_artwork = managed_job_has_artwork(managed_job=managed_job)
    required = not has_artwork
    if managed_job.artwork_required != required:
        managed_job.artwork_required = required
        managed_job.save(update_fields=["artwork_required", "updated_at"])
    return has_artwork


def _job_dashboard_url(managed_job: ManagedJob, role: str) -> str:
    frontend_url = str(getattr(settings, "FRONTEND_URL", "https://printy.ke") or "https://printy.ke").rstrip("/")
    if role == "partner":
        return f"{frontend_url}/dashboard/partner/jobs/{managed_job.id}"
    return f"{frontend_url}/dashboard/client/jobs/{managed_job.id}"


def _job_artwork_email_context(*, managed_job: ManagedJob, source: str) -> dict[str, Any]:
    quote_request = getattr(managed_job, "source_quote_request", None)
    request_snapshot = _as_dict(getattr(quote_request, "request_snapshot", None))
    nested_snapshot = _as_dict(request_snapshot.get("request_snapshot"))
    snapshot = nested_snapshot or request_snapshot
    product = (
        snapshot.get("product_label")
        or snapshot.get("product_type")
        or getattr(managed_job, "title", "")
        or "Print job"
    )
    quantity = snapshot.get("quantity") or ""
    client_name = (
        getattr(getattr(managed_job, "client", None), "name", "")
        or getattr(getattr(managed_job, "client", None), "email", "")
        or "there"
    )
    partner_name = (
        getattr(getattr(managed_job, "broker", None), "name", "")
        or getattr(getattr(managed_job, "broker", None), "email", "")
        or "Printy"
    )
    intro = "Your payment was received. Please upload your artwork so printing can begin."
    if source == "quote_accepted":
        intro = "Your quote was accepted. Upload your artwork now so the job is ready when payment is confirmed."
    elif source == "dispatch_attempt":
        intro = "Printing is ready to start, but your artwork is still missing. Upload it to unblock dispatch."
    elif source == "manager_requested_artwork":
        intro = "Your print manager is waiting on your artwork upload before production can move forward."

    return {
        "subject": "Action needed: Upload your artwork - Printy",
        "preheader": "Upload your artwork to unblock production.",
        "headline": "Upload your artwork to start printing",
        "greeting": f"Hi {client_name},",
        "intro": intro,
        "summary_rows": [
            {"label": "Job", "value": product},
            {"label": "Quantity", "value": str(quantity or "To be confirmed")},
            {"label": "Accepted formats", "value": "PDF, JPG, PNG, AI, EPS"},
            {"label": "File size", "value": "Max 50MB"},
            {"label": "Print manager", "value": partner_name},
        ],
        "cta_label": "Upload artwork",
        "cta_url": _job_dashboard_url(managed_job, "client"),
        "support_copy": "Once the file is uploaded, Printy can continue the job without changing your payment amount.",
        "footer_note": "Generated by Printy",
    }


def _send_artwork_missing_email(*, managed_job: ManagedJob, recipient, source: str) -> None:
    if not getattr(recipient, "email", ""):
        return
    context = _job_artwork_email_context(managed_job=managed_job, source=source)
    text_body = render_to_string("emails/artwork_required_client.txt", context)
    html_body = render_to_string("emails/artwork_required_client.html", context)
    email = EmailMultiAlternatives(
        subject=context["subject"],
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[recipient.email],
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=True)


def notify_missing_artwork(*, managed_job: ManagedJob, actor=None, source: str = "payment_confirmed") -> None:
    if managed_job_has_artwork(managed_job=managed_job):
        return
    if managed_job.artwork_reminder_sent:
        return

    client_recipient = managed_job.client or managed_job.created_by
    partner_recipient = managed_job.broker
    client_message = "Your payment was received. Please upload your artwork to allow production to begin."
    partner_message = "Client has paid but artwork is missing. Job cannot be dispatched until artwork is uploaded."
    if source == "quote_accepted":
        client_message = "Your quote is accepted, but artwork is still missing. Upload it now to keep the job moving."
        partner_message = "Artwork is still missing on an accepted job. The client has been asked to upload it."
    elif source == "quote_request_submitted":
        client_message = "Your quote request was sent. Don't forget to upload your artwork so production can begin as soon as you accept a quote."
        partner_message = "Client has not uploaded artwork yet."
    elif source == "dispatch_attempt":
        client_message = "Production is waiting for your artwork upload before the job can be dispatched."
    elif source == "manager_requested_artwork":
        client_message = "Your print manager is waiting for your artwork upload before production can move forward."
        partner_message = "Artwork is still pending from the client."

    if client_recipient:
        notify(
            recipient=client_recipient,
            notification_type=Notification.JOB_STATUS_UPDATED,
            message=client_message,
            object_type="managed_job",
            object_id=managed_job.id,
            actor=actor,
        )
        try:
            _send_artwork_missing_email(
                managed_job=managed_job,
                recipient=client_recipient,
                source=source,
            )
        except Exception:
            pass

    if partner_recipient:
        notify(
            recipient=partner_recipient,
            notification_type=Notification.JOB_STATUS_UPDATED,
            message=partner_message,
            object_type="managed_job",
            object_id=managed_job.id,
            actor=actor,
        )
    managed_job.artwork_reminder_sent = True
    managed_job.artwork_required = True
    managed_job.save(update_fields=["artwork_reminder_sent", "artwork_required", "updated_at"])


@transaction.atomic
def create_job_file(
    *,
    managed_job: ManagedJob,
    assignment: JobAssignment | None = None,
    uploaded_by=None,
    file=None,
    original_filename: str = "",
    file_type: str = JobFileType.CUSTOMER_UPLOAD,
    visibility: str = JobFileVisibility.CLIENT,
    status: str = JobFileStatus.UPLOADED,
    version: int = 1,
    notes: str = "",
    replaces: JobFile | None = None,
    source_quote_request_attachment: QuoteRequestAttachment | None = None,
    source_quote_attachment: QuoteAttachment | None = None,
) -> JobFile:
    if source_quote_request_attachment:
        existing = JobFile.objects.filter(
            managed_job=managed_job,
            source_quote_request_attachment=source_quote_request_attachment,
        ).first()
        if existing:
            return existing
    if source_quote_attachment:
        existing = JobFile.objects.filter(
            managed_job=managed_job,
            source_quote_attachment=source_quote_attachment,
        ).first()
        if existing:
            return existing

    job_file = JobFile.objects.create(
        managed_job=managed_job,
        assignment=assignment,
        uploaded_by=uploaded_by,
        file=file,
        original_filename=original_filename or _filename_for_field(file),
        file_type=file_type,
        visibility=visibility,
        status=status,
        version=version,
        notes=notes,
        replaces=replaces,
        source_quote_request_attachment=source_quote_request_attachment,
        source_quote_attachment=source_quote_attachment,
    )
    record_job_status_event(
        managed_job=managed_job,
        assignment=assignment,
        job_file=job_file,
        actor=uploaded_by,
        event_type=EVENT_FILE_UPLOADED,
        summary=f"File uploaded: {job_file.original_filename or 'job file'}.",
        metadata={
            "file_type": job_file.file_type,
            "visibility": job_file.visibility,
            "status": job_file.status,
            "version": job_file.version,
        },
    )
    return job_file


def _import_quote_request_attachment(*, managed_job: ManagedJob, attachment: QuoteRequestAttachment) -> JobFile:
    return create_job_file(
        managed_job=managed_job,
        uploaded_by=getattr(attachment.quote_request, "created_by", None),
        file=_stored_name(attachment.file),
        original_filename=attachment.name or _filename_for_field(attachment.file),
        file_type=JobFileType.CUSTOMER_UPLOAD,
        visibility=JobFileVisibility.CLIENT,
        notes="Imported from legacy quote request attachment.",
        source_quote_request_attachment=attachment,
    )


def _import_quote_attachment(*, managed_job: ManagedJob, attachment: QuoteAttachment) -> JobFile:
    return create_job_file(
        managed_job=managed_job,
        uploaded_by=getattr(attachment.quote, "created_by", None),
        file=_stored_name(attachment.file),
        original_filename=attachment.name or _filename_for_field(attachment.file),
        file_type=JobFileType.PROOF,
        visibility=JobFileVisibility.SHOP,
        notes="Imported from legacy shop quote attachment.",
        source_quote_attachment=attachment,
    )


def import_legacy_files_to_managed_job(
    *,
    managed_job: ManagedJob,
    quote_request: QuoteRequest | None = None,
    quote: Quote | None = None,
) -> list[JobFile]:
    imported: list[JobFile] = []
    resolved_quote_request = quote_request or managed_job.source_quote_request
    resolved_quote = quote or managed_job.source_quote

    if resolved_quote_request:
        for attachment in resolved_quote_request.attachments.all():
            imported.append(_import_quote_request_attachment(managed_job=managed_job, attachment=attachment))

    if resolved_quote:
        active_assignment = managed_job.assignments.filter(reassigned_from__isnull=True).first()
        for attachment in resolved_quote.attachments.all():
            job_file = _import_quote_attachment(managed_job=managed_job, attachment=attachment)
            if active_assignment and job_file.assignment_id != active_assignment.id:
                job_file.assignment = active_assignment
                job_file.save(update_fields=["assignment", "updated_at"])
            imported.append(job_file)

    return imported


def get_visible_job_files_for_actor(
    *,
    managed_job: ManagedJob,
    actor: str,
    assignment: JobAssignment | None = None,
):
    queryset = JobFile.objects.filter(managed_job=managed_job).select_related(
        "assignment",
        "uploaded_by",
        "source_quote_request_attachment",
        "source_quote_attachment",
    )
    if assignment:
        queryset = queryset.filter(assignment=assignment) | queryset.filter(assignment__isnull=True)

    if actor == OPS_ACTOR:
        return queryset.distinct().order_by("created_at", "id")
    if actor == SHOP_ACTOR:
        return queryset.filter(
            visibility__in=[
                JobFileVisibility.CLIENT,
                JobFileVisibility.PARTNER,
                JobFileVisibility.SHOP,
            ]
        ).distinct().order_by("created_at", "id")
    if actor == PARTNER_ACTOR:
        return queryset.filter(
            visibility__in=[
                JobFileVisibility.CLIENT,
                JobFileVisibility.PARTNER,
                JobFileVisibility.SHOP,
            ]
        ).exclude(file_type=JobFileType.DELIVERY_EVIDENCE).distinct().order_by("created_at", "id")
    if actor == CLIENT_ACTOR:
        client_queryset = queryset.filter(
            visibility__in=[
                JobFileVisibility.CLIENT,
                JobFileVisibility.PARTNER,
            ],
            file_type__in=[
                JobFileType.ARTWORK,
                JobFileType.CUSTOMER_UPLOAD,
                JobFileType.PROOF,
                JobFileType.DELIVERY_EVIDENCE,
            ],
        ).exclude(
            file_type=JobFileType.PROOF,
            status__in=[
                JobFileStatus.MANAGER_REVIEW,
                JobFileStatus.MANAGER_REJECTED,
                JobFileStatus.PROOF_UPLOADED,
                JobFileStatus.PROOF_REJECTED,
                JobFileStatus.REPLACED,
            ],
        )
        return client_queryset.distinct().order_by("created_at", "id")
    return queryset.none()


@transaction.atomic
def mark_job_file_replaced(*, job_file: JobFile, replacement: JobFile | None = None) -> JobFile:
    job_file.status = JobFileStatus.REPLACED
    job_file.save(update_fields=["status", "updated_at"])
    record_job_status_event(
        managed_job=job_file.managed_job,
        assignment=job_file.assignment,
        job_file=job_file,
        actor=getattr(replacement, "uploaded_by", None),
        event_type=EVENT_FILE_REPLACED,
        summary=f"File replaced: {job_file.original_filename or 'job file'}.",
        metadata={"replacement_id": getattr(replacement, "id", None)},
    )
    if replacement and replacement.replaces_id != job_file.id:
        replacement.replaces = job_file
        replacement.version = max(job_file.version + 1, replacement.version)
        replacement.save(update_fields=["replaces", "version", "updated_at"])
    return job_file


def create_print_ready_file(
    *,
    managed_job: ManagedJob,
    assignment: JobAssignment | None = None,
    uploaded_by=None,
    file=None,
    original_filename: str = "",
    notes: str = "Print-ready production file.",
    replaces: JobFile | None = None,
    ) -> JobFile:
    version = (replaces.version + 1) if replaces else 1
    return create_job_file(
        managed_job=managed_job,
        assignment=assignment,
        uploaded_by=uploaded_by,
        file=file,
        original_filename=original_filename,
        file_type=JobFileType.PRINT_READY,
        visibility=JobFileVisibility.SHOP,
        status=JobFileStatus.APPROVED,
        version=version,
        notes=notes,
        replaces=replaces,
    )


@transaction.atomic
def upload_artwork_for_managed_job(
    *,
    managed_job: ManagedJob,
    assignment: JobAssignment | None = None,
    uploaded_by=None,
    file=None,
    original_filename: str = "",
    notes: str = "Artwork uploaded for production.",
) -> JobFile:
    validated_filename = validate_artwork_upload_file(file=file, original_filename=original_filename)
    job_file = create_job_file(
        managed_job=managed_job,
        assignment=assignment,
        uploaded_by=uploaded_by,
        file=file,
        original_filename=validated_filename,
        file_type=JobFileType.ARTWORK,
        visibility=JobFileVisibility.CLIENT,
        status=JobFileStatus.UPLOADED,
        notes=notes,
    )
    sync_managed_job_artwork_requirement(managed_job=managed_job)
    logger.info(
        "Artwork uploaded successfully managed_job_id=%s job_file_id=%s",
        managed_job.id,
        job_file.id,
    )
    return job_file


@transaction.atomic
def upload_proof_for_managed_job(
    *,
    managed_job: ManagedJob,
    assignment: JobAssignment | None = None,
    uploaded_by=None,
    file=None,
    original_filename: str = "",
    notes: str = "Proof uploaded for approval.",
) -> JobFile:
    return create_job_file(
        managed_job=managed_job,
        assignment=assignment,
        uploaded_by=uploaded_by,
        file=file,
        original_filename=original_filename,
        file_type=JobFileType.PROOF,
        visibility=JobFileVisibility.PARTNER,
        status=JobFileStatus.MANAGER_REVIEW,
        notes=notes,
    )


@transaction.atomic
def manager_approve_job_proof(*, job_file: JobFile, actor=None, notes: str = "") -> JobFile:
    if job_file.file_type != JobFileType.PROOF:
        raise ValueError("Only proof files can be released for client approval.")
    if job_file.status not in {
        JobFileStatus.MANAGER_REVIEW,
        JobFileStatus.PROOF_UPLOADED,
        JobFileStatus.MANAGER_REJECTED,
        JobFileStatus.REVISION_REQUESTED,
    }:
        raise ValueError("This proof is not waiting for manager approval.")
    job_file.status = JobFileStatus.MANAGER_APPROVED
    job_file.visibility = JobFileVisibility.CLIENT
    update_fields = ["status", "visibility", "updated_at"]
    if notes:
        job_file.notes = notes
        update_fields.append("notes")
    job_file.save(update_fields=update_fields)
    record_job_status_event(
        managed_job=job_file.managed_job,
        assignment=job_file.assignment,
        job_file=job_file,
        actor=actor,
        event_type=EVENT_PROOF_APPROVED,
        summary=f"Proof released to client: {job_file.original_filename or 'proof file'}.",
        metadata={"status": job_file.status, "visibility": job_file.visibility},
    )
    return job_file


@transaction.atomic
def manager_reject_job_proof(*, job_file: JobFile, actor=None, notes: str = "") -> JobFile:
    if job_file.file_type != JobFileType.PROOF:
        raise ValueError("Only proof files can be rejected.")
    if job_file.status not in {JobFileStatus.MANAGER_REVIEW, JobFileStatus.PROOF_UPLOADED}:
        raise ValueError("This proof is not waiting for manager approval.")
    job_file.status = JobFileStatus.MANAGER_REJECTED
    job_file.visibility = JobFileVisibility.PARTNER
    update_fields = ["status", "visibility", "updated_at"]
    if notes:
        job_file.notes = notes
        update_fields.append("notes")
    job_file.save(update_fields=update_fields)
    record_job_status_event(
        managed_job=job_file.managed_job,
        assignment=job_file.assignment,
        job_file=job_file,
        actor=actor,
        event_type=EVENT_PROOF_REJECTED,
        summary=f"Proof rejected by manager: {job_file.original_filename or 'proof file'}.",
        metadata={"status": job_file.status, "visibility": job_file.visibility},
    )
    return job_file


@transaction.atomic
def approve_job_proof(*, job_file: JobFile, actor=None, notes: str = "") -> JobFile:
    if job_file.file_type != JobFileType.PROOF:
        raise ValueError("Only proof files can be approved.")
    if job_file.status != JobFileStatus.MANAGER_APPROVED:
        raise ValueError("Proof must be approved by the manager before the client can approve it.")
    job_file.status = JobFileStatus.PROOF_APPROVED
    if notes:
        job_file.notes = notes
        job_file.save(update_fields=["status", "notes", "updated_at"])
    else:
        job_file.save(update_fields=["status", "updated_at"])
    record_job_status_event(
        managed_job=job_file.managed_job,
        assignment=job_file.assignment,
        job_file=job_file,
        actor=actor,
        event_type=EVENT_PROOF_APPROVED,
        summary=f"Proof approved: {job_file.original_filename or 'proof file'}.",
        metadata={"status": job_file.status},
    )
    return job_file


@transaction.atomic
def reject_job_proof(*, job_file: JobFile, actor=None, notes: str = "") -> JobFile:
    if job_file.file_type == JobFileType.PROOF and job_file.status != JobFileStatus.MANAGER_APPROVED:
        raise ValueError("Proof must be approved by the manager before the client can reject it.")
    job_file.status = JobFileStatus.PROOF_REJECTED
    if notes:
        job_file.notes = notes
        job_file.save(update_fields=["status", "notes", "updated_at"])
    else:
        job_file.save(update_fields=["status", "updated_at"])
    record_job_status_event(
        managed_job=job_file.managed_job,
        assignment=job_file.assignment,
        job_file=job_file,
        actor=actor,
        event_type=EVENT_PROOF_REJECTED,
        summary=f"Proof rejected: {job_file.original_filename or 'proof file'}.",
        metadata={"status": job_file.status},
    )
    return job_file


@transaction.atomic
def request_revision(*, job_file: JobFile, actor=None, notes: str = "") -> JobFile:
    if job_file.file_type == JobFileType.PROOF and job_file.status != JobFileStatus.MANAGER_APPROVED:
        raise ValueError("Proof must be approved by the manager before the client can request a revision.")
    job_file.status = JobFileStatus.REVISION_REQUESTED
    if notes:
        job_file.notes = notes
        job_file.save(update_fields=["status", "notes", "updated_at"])
    else:
        job_file.save(update_fields=["status", "updated_at"])
    record_job_status_event(
        managed_job=job_file.managed_job,
        assignment=job_file.assignment,
        job_file=job_file,
        actor=actor,
        event_type=EVENT_REVISION_REQUESTED,
        summary=f"Revision requested for {job_file.original_filename or 'proof file'}.",
        metadata={"status": job_file.status},
    )
    return job_file


@transaction.atomic
def mark_file_print_ready(*, job_file: JobFile, actor=None, notes: str = "") -> JobFile:
    job_file.file_type = JobFileType.PRINT_READY
    job_file.visibility = JobFileVisibility.SHOP
    job_file.status = JobFileStatus.PRINT_READY
    update_fields = ["file_type", "visibility", "status", "updated_at"]
    if notes:
        job_file.notes = notes
        update_fields.append("notes")
    job_file.save(update_fields=update_fields)
    record_job_status_event(
        managed_job=job_file.managed_job,
        assignment=job_file.assignment,
        job_file=job_file,
        actor=actor,
        event_type=EVENT_FILE_UPLOADED,
        summary=f"File marked print ready: {job_file.original_filename or 'job file'}.",
        metadata={"status": job_file.status, "file_type": job_file.file_type},
    )
    return job_file
