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


class CalculatorDraftStatus(models.TextChoices):
    """CalculatorDraft status (client saved-calculation lifecycle)."""

    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    ARCHIVED = "archived", "Archived"


class CalculatorDraftContext(models.TextChoices):
    """Backend-controlled calculator surface where the draft originated."""

    PUBLIC_GUEST = "public_guest", "Public guest"
    CLIENT_DASHBOARD = "client_dashboard", "Client dashboard"
    MANAGER_DASHBOARD = "manager_dashboard", "Manager dashboard"
    BROKER_DASHBOARD = "broker_dashboard", "Broker dashboard"
    SHOP_DASHBOARD = "shop_dashboard", "Shop dashboard"
    ADMIN_DASHBOARD = "admin_dashboard", "Admin dashboard"


class CalculatorDraftIntent(models.TextChoices):
    """Backend-controlled calculator action intent."""

    PUBLIC_PREVIEW = "public_preview", "Public preview"
    SAVE_DRAFT = "save_draft", "Save draft"
    CLIENT_QUOTE_REQUEST = "client_quote_request", "Client quote request"
    SOURCE_PRODUCTION = "source_production", "Source production"
    INTERNAL_ESTIMATE = "internal_estimate", "Internal estimate"
    RESPOND_TO_REQUEST = "respond_to_request", "Respond to request"


class QuoteOfferStatus(models.TextChoices):
    """Quote status (shop offer lifecycle)."""

    PENDING = "pending", "Pending"
    MODIFIED = "modified", "Modified"
    SENT = "sent", "Sent"
    REVISED = "revised", "Revised"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    DECLINED = "declined", "Declined"
    EXPIRED = "expired", "Expired"
