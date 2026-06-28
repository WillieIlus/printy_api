from __future__ import annotations

import mimetypes
import os
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.files.base import File
from django.utils import timezone

from quotes.models import PendingArtworkUpload, QuoteRequestAttachment

ALLOWED_PENDING_ARTWORK_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".ai", ".eps"}
IMAGE_PENDING_ARTWORK_EXTENSIONS = {".jpg", ".jpeg", ".png"}
MAX_PENDING_ARTWORK_BYTES = 50 * 1024 * 1024
PENDING_ARTWORK_EXPIRY_HOURS = 72


def build_pending_artwork_expiry():
    return timezone.now() + timedelta(hours=PENDING_ARTWORK_EXPIRY_HOURS)


def generate_pending_artwork_token() -> str:
    return secrets.token_urlsafe(24)


def validate_pending_artwork_file(uploaded_file) -> tuple[str, str]:
    filename = os.path.basename(getattr(uploaded_file, "name", "") or "")
    extension = os.path.splitext(filename)[1].lower()
    if extension not in ALLOWED_PENDING_ARTWORK_EXTENSIONS:
        raise ValueError("Unsupported artwork file type. Use pdf, jpg, png, ai, or eps.")
    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0:
        raise ValueError("Artwork file is empty.")
    if size > MAX_PENDING_ARTWORK_BYTES:
        raise ValueError("Artwork file cannot exceed 50MB.")
    return filename, extension


def create_pending_artwork_upload(*, uploaded_file, session_key: str) -> PendingArtworkUpload:
    filename, _ = validate_pending_artwork_file(uploaded_file)
    upload = PendingArtworkUpload(
        token=generate_pending_artwork_token(),
        session_key=session_key,
        original_filename=filename,
        file_size=int(getattr(uploaded_file, "size", 0) or 0),
        content_type=getattr(uploaded_file, "content_type", "") or mimetypes.guess_type(filename)[0] or "",
        expires_at=build_pending_artwork_expiry(),
    )
    upload.file.save(filename, uploaded_file, save=False)
    upload.save()
    return upload


def get_pending_artwork_for_token(*, token: str) -> PendingArtworkUpload | None:
    return PendingArtworkUpload.objects.filter(token=token).first()


def pending_artwork_is_expired(upload: PendingArtworkUpload) -> bool:
    return bool(upload.expires_at and timezone.now() >= upload.expires_at)


def purge_expired_pending_artwork(*, now=None) -> int:
    reference_time = now or timezone.now()
    uploads = list(PendingArtworkUpload.objects.filter(expires_at__lte=reference_time))
    deleted = 0
    for upload in uploads:
        delete_pending_artwork(upload)
        deleted += 1
    return deleted


def delete_pending_artwork(upload: PendingArtworkUpload):
    storage = upload.file.storage if upload.file else None
    file_name = upload.file.name if upload.file else ""
    upload.delete()
    if storage and file_name:
        try:
            storage.delete(file_name)
        except Exception:
            pass


def claim_pending_artwork_to_quote_request(*, token: str, quote_request, claimed_by=None) -> QuoteRequestAttachment:
    upload = PendingArtworkUpload.objects.filter(token=token).first()
    if upload is None:
        raise ValueError("Artwork upload was not found. Please upload it again.")
    if pending_artwork_is_expired(upload):
        delete_pending_artwork(upload)
        raise ValueError("Artwork upload expired. Please upload it again.")
    upload.claimed_at = timezone.now()
    upload.claimed_by = claimed_by
    upload.save(update_fields=["claimed_at", "claimed_by", "updated_at"])

    file_name = upload.original_filename or os.path.basename(upload.file.name)
    with upload.file.open("rb") as handle:
        attachment = QuoteRequestAttachment.objects.create(
            quote_request=quote_request,
            file=File(handle, name=file_name),
            name=file_name,
        )
    delete_pending_artwork(upload)
    return attachment


def pending_artwork_preview_url(*, request, upload: PendingArtworkUpload) -> str | None:
    extension = os.path.splitext(upload.original_filename or upload.file.name)[1].lower()
    if extension not in IMAGE_PENDING_ARTWORK_EXTENSIONS:
        return None
    return request.build_absolute_uri(f"/api/calculator/artwork-upload/{upload.token}/preview/")


def serialize_pending_artwork_upload(*, request, upload: PendingArtworkUpload) -> dict:
    return {
        "artwork_token": upload.token,
        "filename": upload.original_filename,
        "size": upload.file_size,
        "expires_at": upload.expires_at.isoformat() if upload.expires_at else None,
        "preview_url": pending_artwork_preview_url(request=request, upload=upload),
    }
