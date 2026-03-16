"""Choice enums for quotes app."""

from django.db import models


class QuoteStatus(models.TextChoices):
    """QuoteRequest status (customer lifecycle)."""

    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    VIEWED = "viewed", "Viewed"
    QUOTED = "quoted", "Quoted"
    ACCEPTED = "accepted", "Accepted"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class ShopQuoteStatus(models.TextChoices):
    """ShopQuote status (shop offer lifecycle)."""

    SENT = "sent", "Sent"
    REVISED = "revised", "Revised"
    ACCEPTED = "accepted", "Accepted"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired"
