"""Manager approval of quote-request artwork before payment.

The prototype treats artwork as a review step: the buyer uploads a version, the
manager approves that exact version, and only then can the buyer pay.  The
existing production model stores request artwork as ``QuoteRequestAttachment``
rows, so this module records the review against the latest attachment id in
``QuoteRequest.request_snapshot``.  It does not add a model or migration.

A replacement attachment resets the review, so an old approval can never unlock
payment for a newer file.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.utils import timezone

from quotes.models import QuoteRequest, QuoteRequestAttachment


REVIEW_KEY = "artwork_review"

REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_CHANGES_REQUESTED = "changes_requested"

PAYMENT_BLOCK_UPLOAD = "Upload your artwork to pay"
PAYMENT_BLOCK_AWAITING_MANAGER = "Waiting for your manager to approve the artwork"
PAYMENT_BLOCK_CHANGES = "Your manager asked for changes to the artwork"


def _snapshot(quote_request: QuoteRequest) -> dict[str, Any]:
    raw = getattr(quote_request, "request_snapshot", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _save(quote_request: QuoteRequest, snapshot: dict[str, Any]) -> QuoteRequest:
    quote_request.request_snapshot = snapshot
    quote_request.save(update_fields=["request_snapshot", "updated_at"])
    return quote_request


def latest_attachment(quote_request: QuoteRequest) -> QuoteRequestAttachment | None:
    return quote_request.attachments.order_by("-id", "-created_at").first()


def get_review(quote_request: QuoteRequest) -> dict[str, Any]:
    snapshot = _snapshot(quote_request)
    review = snapshot.get(REVIEW_KEY)
    if not isinstance(review, dict):
        review = {}
    state = str(review.get("state") or REVIEW_PENDING)
    if state not in {REVIEW_PENDING, REVIEW_APPROVED, REVIEW_CHANGES_REQUESTED}:
        state = REVIEW_PENDING
    return {
        "state": state,
        "file_id": review.get("file_id"),
        "file_name": review.get("file_name", ""),
        "note": review.get("note", ""),
        "reviewed_at": review.get("reviewed_at"),
        "reviewed_by": review.get("reviewed_by"),
    }


def _claim(quote_request: QuoteRequest) -> dict[str, Any]:
    claim = _snapshot(quote_request).get("artwork_claim")
    return claim if isinstance(claim, dict) else {}


def mark_pending(quote_request: QuoteRequest, attachment: QuoteRequestAttachment | None = None) -> QuoteRequest:
    """Record a new current artwork version awaiting manager review."""
    snapshot = _snapshot(quote_request)
    snapshot[REVIEW_KEY] = {
        "state": REVIEW_PENDING,
        "file_id": getattr(attachment, "id", None),
        "file_name": getattr(attachment, "name", "") or "",
        "note": "",
        "reviewed_at": None,
        "reviewed_by": None,
    }
    return _save(quote_request, snapshot)


def clear_review(quote_request: QuoteRequest) -> QuoteRequest:
    snapshot = _snapshot(quote_request)
    snapshot.pop(REVIEW_KEY, None)
    return _save(quote_request, snapshot)


def approve_artwork(
    quote_request: QuoteRequest,
    *,
    actor=None,
    attachment_id: int | None = None,
    note: str = "",
) -> QuoteRequest:
    attachment = latest_attachment(quote_request)
    if attachment is not None:
        if attachment_id is not None and int(attachment_id) != int(attachment.id):
            raise ValidationError("That artwork version is no longer current. Refresh and review the latest file.")
        snapshot = _snapshot(quote_request)
        snapshot[REVIEW_KEY] = {
            "state": REVIEW_APPROVED,
            "file_id": attachment.id,
            "file_name": attachment.name or "",
            "note": note or "",
            "reviewed_at": timezone.now().isoformat(),
            "reviewed_by": getattr(actor, "id", None),
        }
        return _save(quote_request, snapshot)

    claim = _claim(quote_request)
    if claim.get("state") != "with_manager":
        raise ValidationError("There is no artwork to approve yet.")
    snapshot = _snapshot(quote_request)
    snapshot["artwork_claim"] = {
        **claim,
        "state": "accepted",
        "accepted_at": timezone.now().isoformat(),
        "accepted_by": getattr(actor, "id", None),
        "message": claim.get("message", ""),
    }
    snapshot[REVIEW_KEY] = {
        "state": REVIEW_APPROVED,
        "file_id": None,
        "file_name": "With manager",
        "note": note or "",
        "reviewed_at": timezone.now().isoformat(),
        "reviewed_by": getattr(actor, "id", None),
    }
    return _save(quote_request, snapshot)


def mark_manager_holds(
    quote_request: QuoteRequest,
    *,
    actor=None,
    note: str = "",
) -> QuoteRequest:
    """Approve a request whose manager already holds the physical artwork.

    Used for manager-created phone clients: there is no buyer upload, so the
    manager's confirmation is the reviewed version. It is recorded like any
    other approval against the current (empty) file set.
    """
    snapshot = _snapshot(quote_request)
    now = timezone.now().isoformat()
    snapshot["artwork_claim"] = {
        "state": "accepted",
        "message": note or "",
        "declared_at": now,
        "accepted_at": now,
        "accepted_by": getattr(actor, "id", None),
    }
    snapshot[REVIEW_KEY] = {
        "state": REVIEW_APPROVED,
        "file_id": None,
        "file_name": "With manager",
        "note": note or "",
        "reviewed_at": now,
        "reviewed_by": getattr(actor, "id", None),
    }
    return _save(quote_request, snapshot)


def request_changes(
    quote_request: QuoteRequest,
    *,
    actor=None,
    note: str = "",
) -> QuoteRequest:
    attachment = latest_attachment(quote_request)
    snapshot = _snapshot(quote_request)
    claim = _claim(quote_request)
    if attachment is None and claim.get("state") != "with_manager":
        raise ValidationError("There is no artwork to review yet.")
    if claim.get("state") == "with_manager" and attachment is None:
        snapshot["artwork_claim"] = {
            **claim,
            "state": "declined",
            "declined_at": timezone.now().isoformat(),
            "declined_by": getattr(actor, "id", None),
            "message": note or claim.get("message", ""),
        }
    snapshot[REVIEW_KEY] = {
        "state": REVIEW_CHANGES_REQUESTED,
        "file_id": getattr(attachment, "id", None),
        "file_name": getattr(attachment, "name", "") or "",
        "note": note or "",
        "reviewed_at": timezone.now().isoformat(),
        "reviewed_by": getattr(actor, "id", None),
    }
    return _save(quote_request, snapshot)


def is_approved(quote_request: QuoteRequest) -> bool:
    review = get_review(quote_request)
    if review["state"] != REVIEW_APPROVED:
        return False
    attachment = latest_attachment(quote_request)
    if attachment is None:
        return _claim(quote_request).get("state") == "accepted"
    return int(review.get("file_id") or 0) == int(attachment.id)


def payment_block_reason(quote_request: QuoteRequest) -> str | None:
    """The reason a quote payment is refused, or None when the current artwork is approved."""
    attachment = latest_attachment(quote_request)
    review = get_review(quote_request)
    claim = _claim(quote_request)

    if attachment is not None:
        if review["state"] == REVIEW_CHANGES_REQUESTED:
            return PAYMENT_BLOCK_CHANGES
        if review["state"] == REVIEW_APPROVED and int(review.get("file_id") or 0) == int(attachment.id):
            return None
        return PAYMENT_BLOCK_AWAITING_MANAGER

    if claim.get("state") in {"with_manager", "accepted"}:
        if review["state"] == REVIEW_CHANGES_REQUESTED:
            return PAYMENT_BLOCK_CHANGES
        return None if is_approved(quote_request) else PAYMENT_BLOCK_AWAITING_MANAGER

    return PAYMENT_BLOCK_UPLOAD


__all__ = [
    "REVIEW_KEY",
    "REVIEW_PENDING",
    "REVIEW_APPROVED",
    "REVIEW_CHANGES_REQUESTED",
    "PAYMENT_BLOCK_UPLOAD",
    "PAYMENT_BLOCK_AWAITING_MANAGER",
    "PAYMENT_BLOCK_CHANGES",
    "latest_attachment",
    "get_review",
    "mark_pending",
    "clear_review",
    "approve_artwork",
    "mark_manager_holds",
    "request_changes",
    "is_approved",
    "payment_block_reason",
]
