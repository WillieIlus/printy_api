"""One state machine for every ManagedJobPayout change (Prompt 7 PART 2).

Manual release (admin endpoint, custody release, Django admin action, dispute
"release"), failure recording and the automatic Daraja B2C runner all move a
payout through :func:`transition`. It enforces the allowed edges and appends
an entry to ``payout.audit_log`` for every change, so the trail is the same
whichever path moved the money. The (managed_job, recipient_role) unique
constraint still guarantees one row per party.

The edges that matter for money safety:

- ``processing`` (a B2C request is with Daraja) can only end through a B2C
  result/timeout or the timeout sweep -- never through a manual release, which
  would pay twice.
- ``needs_review`` leaves only through an admin approval (-> pending), a
  cancellation, or a verified B2C success callback for the current request.
- ``released`` and ``cancelled`` are final.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils import timezone

from jobs.models import ManagedJobPayout as P

SOURCE_MANUAL = "manual"
SOURCE_AUTO = "auto"
SOURCE_B2C_CALLBACK = "b2c_callback"
SOURCE_ADMIN_REVIEW = "admin_review"
SOURCE_SYSTEM = "system"

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    P.STATUS_PENDING: {P.STATUS_PENDING, P.STATUS_PROCESSING, P.STATUS_RELEASED, P.STATUS_FAILED,
                       P.STATUS_ON_HOLD, P.STATUS_CANCELLED, P.STATUS_NEEDS_REVIEW},
    P.STATUS_FAILED: {P.STATUS_FAILED, P.STATUS_PENDING, P.STATUS_PROCESSING, P.STATUS_RELEASED,
                      P.STATUS_NEEDS_REVIEW, P.STATUS_CANCELLED},
    P.STATUS_ON_HOLD: {P.STATUS_ON_HOLD, P.STATUS_PENDING, P.STATUS_RELEASED, P.STATUS_CANCELLED,
                       P.STATUS_NEEDS_REVIEW},
    P.STATUS_PROCESSING: {P.STATUS_RELEASED, P.STATUS_FAILED, P.STATUS_NEEDS_REVIEW},
    P.STATUS_NEEDS_REVIEW: {P.STATUS_NEEDS_REVIEW, P.STATUS_PENDING, P.STATUS_CANCELLED, P.STATUS_RELEASED},
    P.STATUS_RELEASED: set(),
    P.STATUS_CANCELLED: set(),
}

# Edges only a particular source may take.
_RESTRICTED_EDGES: dict[tuple[str, str], set[str]] = {
    (P.STATUS_PROCESSING, P.STATUS_RELEASED): {SOURCE_B2C_CALLBACK},
    # An in-flight transfer may only be failed or parked by the B2C machinery;
    # the manual path must never make it retryable while Daraja may still pay.
    (P.STATUS_PROCESSING, P.STATUS_FAILED): {SOURCE_AUTO, SOURCE_B2C_CALLBACK},
    (P.STATUS_PROCESSING, P.STATUS_NEEDS_REVIEW): {SOURCE_AUTO, SOURCE_B2C_CALLBACK, SOURCE_SYSTEM},
    (P.STATUS_NEEDS_REVIEW, P.STATUS_RELEASED): {SOURCE_B2C_CALLBACK},
    (P.STATUS_NEEDS_REVIEW, P.STATUS_PENDING): {SOURCE_ADMIN_REVIEW},
}


class PayoutTransitionError(ValidationError):
    pass


def can_transition(current: str, target: str, *, source: str) -> bool:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        return False
    allowed_sources = _RESTRICTED_EDGES.get((current, target))
    return allowed_sources is None or source in allowed_sources


def audit_entry(payout: P, *, event: str, actor=None, source: str, reason: str = "", **extra) -> dict:
    entry = {
        "at": timezone.now().isoformat(),
        "event": event,
        "status": payout.status,
        "source": source,
        "actor_id": getattr(actor, "pk", None),
        "reason": reason,
    }
    entry.update({key: value for key, value in extra.items() if value is not None})
    log = list(payout.audit_log or [])
    log.append(entry)
    payout.audit_log = log
    return entry


def transition(payout: P, target: str, *, source: str, actor=None, reason: str = "", **extra) -> P:
    """Move ``payout`` to ``target`` (in memory) and log it; the caller saves.

    Raises :class:`PayoutTransitionError` for an edge the state machine does
    not allow, so a caller can never silently overwrite a final or in-flight
    payout.
    """
    current = payout.status or P.STATUS_PENDING
    if not can_transition(current, target, source=source):
        raise PayoutTransitionError(
            f"Payout {payout.pk or '(new)'} cannot move from '{current}' to '{target}' via {source}."
        )
    payout.status = target
    audit_entry(payout, event=f"{current}->{target}", actor=actor, source=source, reason=reason,
                from_status=current, **extra)
    return payout


MANUAL_RELEASE_BLOCKED = {
    P.STATUS_PROCESSING: "A Daraja B2C transfer for this payout is in flight; wait for its result.",
    P.STATUS_NEEDS_REVIEW: "This payout needs admin review (POST /api/admin/payouts/<id>/approve/) before it can move.",
    P.STATUS_CANCELLED: "This payout was cancelled.",
}


def assert_manual_release_allowed(payout: P) -> None:
    message = MANUAL_RELEASE_BLOCKED.get(payout.status)
    if message:
        raise PayoutTransitionError(message)
