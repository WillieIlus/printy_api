"""Choice enums for quotes app."""

from django.db import models


class QuoteStatus(models.TextChoices):
    """QuoteRequest status (customer lifecycle)."""

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    AWAITING_SHOP_ACTION = "awaiting_shop_action", "Awaiting shop action"
    ACCEPTED = "accepted", "Accepted by shop"
    AWAITING_CLIENT_REPLY = "awaiting_client_reply", "Awaiting client reply"
    VIEWED = "viewed", "Viewed"
    QUOTED = "quoted", "Quoted"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class QuoteDraftStatus(models.TextChoices):
    """QuoteDraft status (client saved-calculation lifecycle)."""

    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    ARCHIVED = "archived", "Archived"


class ShopQuoteStatus(models.TextChoices):
    """ShopQuote status (shop offer lifecycle)."""

    PENDING = "pending", "Pending"
    MODIFIED = "modified", "Modified"
    SENT = "sent", "Sent"
    REVISED = "revised", "Revised"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired"
