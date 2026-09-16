"""Canonical workflow helpers for managed operational orchestration."""

from __future__ import annotations

from typing import Any

from api.visibility import CLIENT_ACTOR, OPS_ACTOR, PARTNER_ACTOR, SHOP_ACTOR
from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobAssignmentStatus,
    ManagedJobExceptionStatus,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
)
from quotes.choices import QuoteStatus, QuoteOfferStatus


CANONICAL_WORKFLOW_SEQUENCE = [
    ManagedJobStatus.DRAFT,
    ManagedJobStatus.QUOTED,
    ManagedJobStatus.AWAITING_PAYMENT,
    ManagedJobStatus.PAYMENT_CONFIRMED,
    ManagedJobStatus.ASSIGNED,
    ManagedJobStatus.IN_PRODUCTION,
    ManagedJobStatus.FINISHING,
    ManagedJobStatus.READY,
    ManagedJobStatus.DELIVERED,
    ManagedJobStatus.COMPLETED,
    ManagedJobStatus.DISPUTED,
    ManagedJobStatus.CANCELLED,
]


TRANSITION_OWNER_RULES: dict[str, tuple[str, ...]] = {
    ManagedJobStatus.DRAFT: (CLIENT_ACTOR, PARTNER_ACTOR, OPS_ACTOR),
    ManagedJobStatus.QUOTED: (SHOP_ACTOR, OPS_ACTOR),
    ManagedJobStatus.AWAITING_PAYMENT: (CLIENT_ACTOR, PARTNER_ACTOR, OPS_ACTOR),
    ManagedJobStatus.PAYMENT_CONFIRMED: (OPS_ACTOR, PARTNER_ACTOR),
    ManagedJobStatus.ASSIGNED: (OPS_ACTOR, PARTNER_ACTOR),
    ManagedJobStatus.IN_PRODUCTION: (SHOP_ACTOR, OPS_ACTOR),
    ManagedJobStatus.FINISHING: (SHOP_ACTOR, OPS_ACTOR),
    ManagedJobStatus.READY: (SHOP_ACTOR, OPS_ACTOR),
    ManagedJobStatus.DELIVERED: (OPS_ACTOR, SHOP_ACTOR),
    ManagedJobStatus.COMPLETED: (OPS_ACTOR, CLIENT_ACTOR),
    ManagedJobStatus.DISPUTED: (CLIENT_ACTOR, PARTNER_ACTOR, SHOP_ACTOR, OPS_ACTOR),
    ManagedJobStatus.CANCELLED: (OPS_ACTOR, CLIENT_ACTOR, PARTNER_ACTOR),
}


CLIENT_VISIBLE_STATUSES = {
    ManagedJobStatus.DRAFT: "Draft",
    ManagedJobStatus.QUOTED: "Quoted",
    ManagedJobStatus.AWAITING_PAYMENT: "Awaiting payment",
    ManagedJobStatus.PAYMENT_CONFIRMED: "Payment confirmed",
    ManagedJobStatus.ASSIGNED: "Production scheduled",
    ManagedJobStatus.IN_PRODUCTION: "In production",
    ManagedJobStatus.FINISHING: "Finishing",
    ManagedJobStatus.READY: "Ready",
    ManagedJobStatus.DELIVERED: "Delivered",
    ManagedJobStatus.COMPLETED: "Completed",
    ManagedJobStatus.DISPUTED: "Under review",
    ManagedJobStatus.CANCELLED: "Cancelled",
}


PARTNER_VISIBLE_STATUSES = {
    ManagedJobStatus.DRAFT: "Draft",
    ManagedJobStatus.QUOTED: "Quoted",
    ManagedJobStatus.AWAITING_PAYMENT: "Awaiting payment",
    ManagedJobStatus.PAYMENT_CONFIRMED: "Payment confirmed",
    ManagedJobStatus.ASSIGNED: "Assigned",
    ManagedJobStatus.IN_PRODUCTION: "In production",
    ManagedJobStatus.FINISHING: "Finishing",
    ManagedJobStatus.READY: "Ready",
    ManagedJobStatus.DELIVERED: "Delivered",
    ManagedJobStatus.COMPLETED: "Completed",
    ManagedJobStatus.DISPUTED: "Disputed",
    ManagedJobStatus.CANCELLED: "Cancelled",
}


SHOP_VISIBLE_STATUSES = {
    ManagedJobStatus.DRAFT: "Pending intake",
    ManagedJobStatus.QUOTED: "Quoted",
    ManagedJobStatus.AWAITING_PAYMENT: "Awaiting payment confirmation",
    ManagedJobStatus.PAYMENT_CONFIRMED: "Payment confirmed",
    ManagedJobStatus.ASSIGNED: "Assigned",
    ManagedJobStatus.IN_PRODUCTION: "In production",
    ManagedJobStatus.FINISHING: "Finishing",
    ManagedJobStatus.READY: "Ready for handoff",
    ManagedJobStatus.DELIVERED: "Delivered",
    ManagedJobStatus.COMPLETED: "Completed",
    ManagedJobStatus.DISPUTED: "Ops hold",
    ManagedJobStatus.CANCELLED: "Cancelled",
}


OPS_VISIBLE_STATUSES = {status: status.label for status in ManagedJobStatus}


def canonical_status_label(status: str | None, *, actor: str = OPS_ACTOR) -> str:
    if not status:
        return "Unknown"
    if actor == CLIENT_ACTOR:
        return CLIENT_VISIBLE_STATUSES.get(status, "Unknown")
    if actor == PARTNER_ACTOR:
        return PARTNER_VISIBLE_STATUSES.get(status, "Unknown")
    if actor == SHOP_ACTOR:
        return SHOP_VISIBLE_STATUSES.get(status, "Unknown")
    return OPS_VISIBLE_STATUSES.get(status, "Unknown")


def canonical_transition_owners(status: str | None) -> tuple[str, ...]:
    if not status:
        return tuple()
    return TRANSITION_OWNER_RULES.get(status, tuple())


def project_workflow_state(
    *,
    status: str | None,
    actor: str,
    payment_status: str | None = None,
    assignment_status: str | None = None,
    exception_status: str | None = None,
    urgency_type: str | None = None,
    operational_priority_level: int | None = None,
) -> dict[str, Any]:
    normalized_urgency = (urgency_type or "standard").strip().lower() or "standard"
    priority = operational_priority_level or 1
    tone = "neutral"
    detail = ""
    if normalized_urgency == "same_day":
        tone = "info"
        detail = "Same-day turnaround is active for this job."
    elif normalized_urgency == "express":
        tone = "warning"
        detail = "Priority production is active to protect the requested turnaround."
    elif normalized_urgency == "after_hours":
        tone = "warning"
        detail = "After-hours production is flagged for this job."
    elif normalized_urgency == "emergency":
        tone = "danger"
        detail = "Emergency production is active and needs fast operational handling."

    if exception_status in {ManagedJobExceptionStatus.PRODUCTION_ISSUE, ManagedJobExceptionStatus.DELIVERY_ISSUE, ManagedJobExceptionStatus.DISPUTE_OPEN, ManagedJobExceptionStatus.OPS_REVIEW}:
        tone = "danger" if exception_status != ManagedJobExceptionStatus.OPS_REVIEW else "warning"

    return {
        "status": status,
        "label": canonical_status_label(status, actor=actor),
        "code": normalized_urgency,
        "tone": tone,
        "detail": detail,
        "priority_level": priority,
        "payment_status": payment_status if actor in {PARTNER_ACTOR, SHOP_ACTOR, OPS_ACTOR} else None,
        "assignment_status": assignment_status if actor in {PARTNER_ACTOR, SHOP_ACTOR, OPS_ACTOR} else None,
        "exception_status": exception_status if actor in {SHOP_ACTOR, OPS_ACTOR} else None,
        "allowed_transition_actors": list(canonical_transition_owners(status)) if actor == OPS_ACTOR else [],
        "is_terminal": status in {ManagedJobStatus.COMPLETED, ManagedJobStatus.DISPUTED, ManagedJobStatus.CANCELLED},
    }


def canonical_workflow_definition() -> dict[str, Any]:
    return {
        "sequence": list(CANONICAL_WORKFLOW_SEQUENCE),
        "payment_defaults": {
            "initial": ManagedJobPaymentStatus.PENDING,
            "awaiting_confirmation": ManagedJobPaymentStatus.CONFIRMATION_PENDING,
            "confirmed": ManagedJobPaymentStatus.CONFIRMED,
            "release_ready": ManagedJobPaymentStatus.RELEASE_READY,
            "hold": ManagedJobPaymentStatus.PAYOUT_ON_HOLD,
        },
        "assignment_defaults": {
            "initial": ManagedJobAssignmentStatus.UNASSIGNED,
            "pending": ManagedJobAssignmentStatus.ASSIGNMENT_PENDING,
            "assigned": ManagedJobAssignmentStatus.ASSIGNED,
        },
        "exception_defaults": {
            "clear": ManagedJobExceptionStatus.CLEAR,
            "production_issue": ManagedJobExceptionStatus.PRODUCTION_ISSUE,
            "delivery_issue": ManagedJobExceptionStatus.DELIVERY_ISSUE,
            "dispute_open": ManagedJobExceptionStatus.DISPUTE_OPEN,
            "ops_review": ManagedJobExceptionStatus.OPS_REVIEW,
        },
        "transition_owners": {status: list(owners) for status, owners in TRANSITION_OWNER_RULES.items()},
    }


def managed_status_from_quote_request_status(status: str | None) -> str:
    mapping = {
        QuoteStatus.DRAFT: ManagedJobStatus.DRAFT,
        QuoteStatus.SUBMITTED: ManagedJobStatus.DRAFT,
        QuoteStatus.AWAITING_SHOP_ACTION: ManagedJobStatus.DRAFT,
        QuoteStatus.VIEWED: ManagedJobStatus.DRAFT,
        QuoteStatus.AWAITING_CLIENT_REPLY: ManagedJobStatus.QUOTED,
        QuoteStatus.QUOTED: ManagedJobStatus.QUOTED,
        QuoteStatus.ACCEPTED: ManagedJobStatus.AWAITING_PAYMENT,
        QuoteStatus.CLOSED: ManagedJobStatus.AWAITING_PAYMENT,
        QuoteStatus.REJECTED: ManagedJobStatus.CANCELLED,
        QuoteStatus.EXPIRED: ManagedJobStatus.CANCELLED,
        QuoteStatus.CANCELLED: ManagedJobStatus.CANCELLED,
    }
    return mapping.get(status, ManagedJobStatus.DRAFT)


def managed_status_from_quote_status(status: str | None) -> str:
    mapping = {
        QuoteOfferStatus.PENDING: ManagedJobStatus.DRAFT,
        QuoteOfferStatus.MODIFIED: ManagedJobStatus.QUOTED,
        QuoteOfferStatus.SENT: ManagedJobStatus.QUOTED,
        QuoteOfferStatus.REVISED: ManagedJobStatus.QUOTED,
        QuoteOfferStatus.ACCEPTED: ManagedJobStatus.AWAITING_PAYMENT,
        QuoteOfferStatus.REJECTED: ManagedJobStatus.CANCELLED,
        QuoteOfferStatus.DECLINED: ManagedJobStatus.CANCELLED,
        QuoteOfferStatus.EXPIRED: ManagedJobStatus.CANCELLED,
    }
    return mapping.get(status, ManagedJobStatus.DRAFT)


def managed_status_from_production_order(*, status: str | None, delivery_status: str | None = None) -> str:
    if status == "cancelled":
        return ManagedJobStatus.CANCELLED
    if status == "completed":
        if delivery_status == "delivered":
            return ManagedJobStatus.DELIVERED
        return ManagedJobStatus.COMPLETED
    if delivery_status == "delivered":
        return ManagedJobStatus.DELIVERED
    if status == "ready":
        return ManagedJobStatus.READY
    if status == "in_progress":
        if delivery_status in {"ready_for_pickup", "shipped"}:
            return ManagedJobStatus.FINISHING
        return ManagedJobStatus.IN_PRODUCTION
    return ManagedJobStatus.ASSIGNED


def assignment_status_from_production_order(*, status: str | None, delivery_status: str | None = None) -> str:
    if status == "cancelled":
        return JobAssignmentStatus.CANCELLED
    if status == "completed":
        if delivery_status == "delivered":
            return JobAssignmentStatus.COMPLETED
        return JobAssignmentStatus.COMPLETED
    if status == "ready":
        return JobAssignmentStatus.READY
    if status == "in_progress":
        return JobAssignmentStatus.IN_PRODUCTION
    return JobAssignmentStatus.ACCEPTED
