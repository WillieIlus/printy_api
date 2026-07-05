"""Normalize legacy quote statuses to stable frontend-facing statuses."""

from quotes.choices import CalculatorDraftStatus, QuoteStatus, QuoteOfferStatus


def normalize_calculator_draft_status(raw_status: str | None, *, has_shop=False, has_request_details=False, has_pricing=False) -> str:
    if raw_status == CalculatorDraftStatus.SENT:
        return "sent"
    if raw_status == CalculatorDraftStatus.ARCHIVED:
        return "abandoned"
    if raw_status == CalculatorDraftStatus.DRAFT and (has_shop or has_request_details or has_pricing):
        return "ready_to_send"
    return "draft"


def calculator_draft_status_label(status: str) -> str:
    return {
        "draft": "Draft",
        "ready_to_send": "Ready to send",
        "sent": "Sent",
        "abandoned": "Abandoned",
    }.get(status, "Draft")


def normalize_quote_request_status(raw_status: str | None) -> str:
    return {
        QuoteStatus.DRAFT: "draft",
        QuoteStatus.SUBMITTED: "sent",
        QuoteStatus.AWAITING_SHOP_ACTION: "pending",
        QuoteStatus.ACCEPTED: "pending",
        QuoteStatus.AWAITING_CLIENT_REPLY: "needs_confirmation",
        QuoteStatus.VIEWED: "viewed",
        QuoteStatus.QUOTED: "responded",
        QuoteStatus.REJECTED: "rejected",
        QuoteStatus.EXPIRED: "expired",
        QuoteStatus.CLOSED: "accepted",
        QuoteStatus.CANCELLED: "cancelled",
    }.get(raw_status or "", "pending")


def quote_request_status_label(status: str) -> str:
    return {
        "draft": "Draft",
        "pending": "Pending",
        "sent": "Sent",
        "viewed": "Viewed",
        "needs_confirmation": "Needs confirmation",
        "responded": "Responded",
        "accepted": "Accepted",
        "rejected": "Rejected",
        "expired": "Expired",
        "cancelled": "Cancelled",
    }.get(status, "Pending")


def normalize_quote_response_status(raw_status: str | None) -> str:
    return {
        QuoteOfferStatus.PENDING: "draft",
        QuoteOfferStatus.SENT: "sent",
        QuoteOfferStatus.MODIFIED: "modified",
        "revised": "modified",
        QuoteOfferStatus.ACCEPTED: "accepted",
        QuoteOfferStatus.REJECTED: "rejected",
        "declined": "rejected",
        QuoteOfferStatus.EXPIRED: "expired",
    }.get(raw_status or "", "draft")


def quote_response_status_label(status: str) -> str:
    return {
        "draft": "Draft",
        "sent": "Sent",
        "modified": "Modified",
        "accepted": "Accepted",
        "rejected": "Rejected",
        "expired": "Expired",
    }.get(status, "Draft")


def denormalize_quote_response_status(status: str) -> str:
    return {
        "draft": QuoteOfferStatus.PENDING,
        "sent": QuoteOfferStatus.SENT,
        "modified": QuoteOfferStatus.MODIFIED,
        "accepted": QuoteOfferStatus.ACCEPTED,
        "rejected": QuoteOfferStatus.REJECTED,
        "expired": QuoteOfferStatus.EXPIRED,
    }.get(status, status)
