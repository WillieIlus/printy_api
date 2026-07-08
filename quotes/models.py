"""
Quote models: QuoteRequest (customer request), Quote (shop response), QuoteItem (line items).

Domain separation:
- QuoteRequest: customer's request for a quote (draft → submitted → quoted → accepted/closed)
- Quote: shop's priced offer (sent → accepted/declined/expired; supports revisions)
- QuoteItem: line items with specs; prices filled when linked to Quote
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel
from core.querysets import QuoteRequestQuerySet, QuoteItemQuerySet
from shops.models import Shop
from .choices import CalculatorDraftContext, CalculatorDraftIntent, CalculatorDraftStatus, QuoteStatus, QuoteOfferStatus


class QuoteRequest(TimeStampedModel):
    """
    Customer's request for a quote. Created by buyer; visible to buyer and shop.
    Lifecycle: draft → submitted → [viewed] → quoted → accepted | closed | cancelled.
    """

    DRAFT = QuoteStatus.DRAFT
    SUBMITTED = QuoteStatus.SUBMITTED
    VIEWED = QuoteStatus.VIEWED
    QUOTED = QuoteStatus.QUOTED
    ACCEPTED = QuoteStatus.ACCEPTED
    CLOSED = QuoteStatus.CLOSED
    CANCELLED = QuoteStatus.CANCELLED
    STATUS_CHOICES = QuoteStatus.choices
    MANAGER_SELECTION_CLIENT_SELECTED = "client_selected"
    MANAGER_SELECTION_PRINTY_AUTO = "printy_auto"

    objects = QuoteRequestQuerySet.as_manager()
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="quote_requests",
        null=True,
        blank=True,
        verbose_name=_("shop"),
        help_text=_("DEPRECATED internal routing field. Batch 7 will replace client request shop routing."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_requests",
        verbose_name=_("created by"),
        help_text=_("User who created this quote request."),
    )
    assigned_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_quote_requests",
        verbose_name=_("assigned manager"),
        help_text=_("Print Manager / Partner assigned to coordinate this request before production shop selection."),
    )
    on_behalf_of = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotes_requested_for_me",
        verbose_name=_("on behalf of"),
        help_text=_("End client this request was created for when a partner submits on their behalf."),
    )
    customer_name = models.CharField(
        max_length=255,
        default="",
        verbose_name=_("customer name"),
        help_text=_("Name of the customer."),
    )
    customer_email = models.EmailField(
        blank=True,
        verbose_name=_("customer email"),
        help_text=_("Email address of the customer."),
    )
    customer_phone = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("customer phone"),
        help_text=_("Phone number of the customer."),
    )
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=DRAFT,
        verbose_name=_("status"),
        help_text=_("Customer request lifecycle: draft → submitted → quoted → accepted/closed."),
    )
    notes = models.TextField(
        blank=True,
        default="",
        verbose_name=_("notes"),
        help_text=_("Additional notes for the quote."),
    )
    delivery_address = models.TextField(
        blank=True,
        default="",
        verbose_name=_("delivery address"),
        help_text=_("Full address for delivery (street, building, etc.)."),
    )
    PICKUP = "pickup"
    DELIVERY = "delivery"
    DELIVERY_PREFERENCE_CHOICES = [
        (PICKUP, _("Pickup")),
        (DELIVERY, _("Delivery")),
    ]
    delivery_preference = models.CharField(
        max_length=20,
        choices=DELIVERY_PREFERENCE_CHOICES,
        blank=True,
        default="",
        verbose_name=_("delivery preference"),
        help_text=_("Customer preference: pickup at shop or delivery."),
    )
    request_reference = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("request reference"),
        help_text=_("Stable reference for customer-facing quote requests."),
    )
    source_draft = models.ForeignKey(
        "CalculatorDraft",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_requests",
        verbose_name=_("source draft"),
        help_text=_("Draft that generated this request, if any."),
    )
    request_snapshot = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("request snapshot"),
        help_text=_("Frozen request payload used when the customer submitted the request."),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("quote request")
        verbose_name_plural = _("quote requests")
        indexes = [
            models.Index(fields=["shop", "status"], name="quotes_shop_status_idx"),
            models.Index(fields=["shop", "-created_at"], name="quotes_shop_created_idx"),
        ]

    def __str__(self):
        return f"Request #{self.id} - {self.customer_name} ({self.status})"

    @property
    def manager_selection_mode(self):
        snapshot = self.request_snapshot if isinstance(self.request_snapshot, dict) else {}
        assignment = snapshot.get("assignment") if isinstance(snapshot.get("assignment"), dict) else {}
        return (
            assignment.get("manager_selection_mode")
            or snapshot.get("manager_selection_mode")
            or (self.MANAGER_SELECTION_CLIENT_SELECTED if self.assigned_manager_id else "")
        )

    def get_latest_quote(self):
        """Return the most recent sent/accepted Quote, or None."""
        return self.quotes.filter(
            status__in=[Quote.SENT, Quote.REVISED, Quote.ACCEPTED]
        ).order_by("-sent_at", "-created_at").first()

    def get_latest_response(self):
        return self.quotes.exclude(status=Quote.PENDING).order_by("-created_at", "-id").first()


class Quote(TimeStampedModel):
    """
    Shop's priced offer in response to a QuoteRequest. Created by shop; visible to shop and customer.
    Supports revisions: one QuoteRequest can have multiple Quotes (latest is active).
    Lifecycle: sent → accepted | declined | expired | revised.
    """

    PENDING = "pending"
    MODIFIED = "modified"
    SENT = "sent"
    REVISED = "revised"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DECLINED = "declined"
    EXPIRED = "expired"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (MODIFIED, "Modified"),
        (SENT, "Sent"),
        (REVISED, "Revised"),
        (ACCEPTED, "Accepted"),
        (REJECTED, "Rejected"),
        (DECLINED, "Declined"),
        (EXPIRED, "Expired"),
    ]

    quote_request = models.ForeignKey(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name="quotes",
        verbose_name=_("quote request"),
        help_text=_("Quote request this offer responds to."),
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="quotes",
        verbose_name=_("shop"),
        help_text=_("Shop that sent this quote."),
    )
    production_option = models.ForeignKey(
        "quotes.ProductionOption",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="quotes",
        help_text=_("The sourced production option this quote was built from."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotes",
        verbose_name=_("created by"),
        help_text=_("Shop user who created/sent this quote."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=PENDING,
        verbose_name=_("status"),
        help_text=_("Shop offer lifecycle: sent → accepted/declined/expired/revised."),
    )
    total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("total"),
        help_text=_("Total price of the quote."),
    )
    sent_to_client_at = models.DateTimeField(null=True, blank=True)
    sent_to_client_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_facing_quotes",
    )
    client_quote_status = models.CharField(max_length=32, blank=True, default="")
    pricing_locked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("pricing locked at"),
        help_text=_("When pricing was locked."),
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("sent at"),
        help_text=_("When the quote was sent to the customer."),
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("expires at"),
        help_text=_("When the client-facing quote expires."),
    )
    whatsapp_message = models.TextField(
        blank=True,
        default="",
        verbose_name=_("whatsapp message"),
        help_text=_("Message sent via WhatsApp when quote was sent."),
    )
    note = models.TextField(
        blank=True,
        default="",
        verbose_name=_("note"),
        help_text=_("Shop's note to customer (e.g. conditions, clarifications)."),
    )
    turnaround_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("turnaround (days)"),
        help_text=_("Expected turnaround in days (e.g. ready in 3 days)."),
    )
    turnaround_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("turnaround (hours)"),
        help_text=_("Expected turnaround in working hours."),
    )
    estimated_ready_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("estimated ready at"),
        help_text=_("Projected ready datetime based on working hours."),
    )
    human_ready_text = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("human ready text"),
        help_text=_("Human-friendly ready promise shown to customers."),
    )
    turnaround_label = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("turnaround label"),
        help_text=_("Centralized turnaround label such as Same day or Standard."),
    )
    revision_number = models.PositiveIntegerField(
        default=1,
        verbose_name=_("revision number"),
        help_text=_("Revision count for this quote request (1 = first, 2 = first revision, etc.)."),
    )
    quote_reference = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("quote reference"),
        help_text=_("Stable shop response reference."),
    )
    response_snapshot = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("response snapshot"),
        help_text=_("Frozen response payload for PDF/share rendering."),
    )
    revised_pricing_snapshot = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("revised pricing snapshot"),
        help_text=_("Frozen revised pricing payload for this response."),
    )
    accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("accepted at"),
        help_text=_("When the client accepted this quote."),
    )
    rejected_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("rejected at"),
        help_text=_("When the client rejected or did not select this quote."),
    )
    rejection_reason = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("rejection reason"),
        help_text=_("Short client rejection reason or not-selected reason."),
    )
    rejection_message = models.TextField(
        blank=True,
        default="",
        verbose_name=_("rejection message"),
        help_text=_("Optional detailed rejection message from the client."),
    )

    class Meta:
        ordering = ["-sent_at", "-created_at"]
        verbose_name = _("shop quote")
        verbose_name_plural = _("shop quotes")
        indexes = [
            models.Index(fields=["shop", "status"], name="quote_shop_status_idx"),
            models.Index(fields=["quote_request", "-created_at"], name="quote_req_created_idx"),
        ]

    def __str__(self):
        return f"Quote #{self.id} for Request #{self.quote_request_id} ({self.status})"

    def is_terminal_status(self) -> bool:
        return self.status in {QuoteOfferStatus.ACCEPTED, QuoteOfferStatus.REJECTED}

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() > self.expires_at)

    @property
    def financials(self):
        return getattr(self, "financial_split", None)


class ProductionOption(TimeStampedModel):
    """Manager/broker/admin sourced shop production option before a quote is sent."""

    CANDIDATE = "candidate"
    SELECTED = "selected"
    REJECTED = "rejected"
    STATUS_CHOICES = [
        (CANDIDATE, "Candidate"),
        (SELECTED, "Selected"),
        (REJECTED, "Rejected"),
    ]

    quote_request = models.ForeignKey(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name="production_options",
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.PROTECT,
        related_name="production_options",
    )
    production_cost = models.DecimalField(max_digits=12, decimal_places=2)
    estimated_turnaround_hours = models.PositiveIntegerField(null=True, blank=True)
    capacity_status = models.CharField(max_length=40, blank=True, default="")
    score = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=CANDIDATE)
    pricing_snapshot = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_production_options",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("production option")
        verbose_name_plural = _("production options")

    def __str__(self):
        return f"Production option #{self.id} for request #{self.quote_request_id}"

    def clean(self):
        super().clean()
        if self.production_cost <= 0:
            raise ValidationError({"production_cost": _("Production cost must be greater than zero.")})


class QuoteFinancialSplit(models.Model):
    """Immutable financial snapshot for a quote."""

    quote = models.OneToOneField(
        Quote,
        on_delete=models.CASCADE,
        related_name="financial_split",
    )
    policy_used = models.ForeignKey(
        "pricing.PlatformFeePolicy",
        on_delete=models.PROTECT,
        related_name="quote_splits",
    )
    production_option = models.ForeignKey(
        ProductionOption,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="financial_splits",
    )
    production_cost = models.DecimalField(max_digits=12, decimal_places=2)
    manager_markup = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    production_fee_component = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    markup_fee_component = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    broker_client_price = models.DecimalField(max_digits=12, decimal_places=2)
    gross_margin = models.DecimalField(max_digits=12, decimal_places=2)
    printer_side_fee = models.DecimalField(max_digits=12, decimal_places=2)
    broker_margin_fee = models.DecimalField(max_digits=12, decimal_places=2)
    printy_fee = models.DecimalField(max_digits=12, decimal_places=2)
    shop_payout = models.DecimalField(max_digits=12, decimal_places=2)
    manager_payout = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    broker_payout = models.DecimalField(max_digits=12, decimal_places=2)
    client_total = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    pricing_tier = models.CharField(max_length=40, default="legacy")
    applied_policy_version = models.CharField(max_length=40, default="printy-fees-v1")
    max_allowed_client_price = models.DecimalField(max_digits=12, decimal_places=2)
    applied_markup_multiple = models.DecimalField(max_digits=8, decimal_places=4)
    calculated_at = models.DateTimeField(auto_now_add=True)
    locked = models.BooleanField(default=False)

    class Meta:
        ordering = ["-calculated_at"]
        verbose_name = _("quote financial split")
        verbose_name_plural = _("quote financial splits")

    def __str__(self):
        return f"Financial split for quote #{self.quote_id}"

    def clean(self):
        super().clean()
        errors = {}
        if self.production_cost < 0:
            errors["production_cost"] = _("Production cost cannot be negative.")
        if self.manager_markup < 0:
            errors["manager_markup"] = _("Manager markup cannot be negative.")
        if self.broker_client_price < self.production_cost:
            errors["broker_client_price"] = _("Broker client price cannot be below production cost.")
        if self.broker_client_price > self.max_allowed_client_price:
            errors["broker_client_price"] = _("Broker client price exceeds the policy cap.")
        if self.broker_payout < 0:
            errors["broker_payout"] = _("Broker payout cannot be negative.")
        if self.client_total < self.production_cost:
            errors["client_total"] = _("Client total cannot be below production cost.")
        if (
            self.production_option_id
            and self.quote_id
            and self.production_option.quote_request_id != self.quote.quote_request_id
        ):
            errors["production_option"] = _("Production option must belong to the quote request.")
        if errors:
            raise ValidationError(errors)


class QuoteRequestMessage(TimeStampedModel):
    """
    DEPRECATED: transitional model kept only to satisfy legacy code.
    Scheduled for removal in a later postponed batch (not MVP).
    Do NOT add new fields, FKs, or features to this model.
    """

    class SenderRole(models.TextChoices):
        CLIENT = "client", "Client"
        SHOP = "shop", "Shop"
        SYSTEM = "system", "System"

    class MessageKind(models.TextChoices):
        STATUS = "status", "Status update"
        QUESTION = "question", "Question"
        REPLY = "reply", "Reply"
        REJECTION = "rejection", "Rejection"
        QUOTE = "quote", "Quote"
        NOTE = "note", "Note"

    class RecipientRole(models.TextChoices):
        CLIENT = "client", "Client"
        SHOP_OWNER = "shop_owner", "Shop owner"
        ADMIN = "admin", "Admin"
        SYSTEM = "system", "System"

    class MessageType(models.TextChoices):
        QUOTE_REQUEST_CREATED = "quote_request_created", "Quote request created"
        QUOTE_RESPONSE_SENT = "quote_response_sent", "Quote response sent"
        QUOTE_QUESTION = "quote_question", "Quote question"
        QUOTE_ACCEPTED = "quote_accepted", "Quote accepted"
        QUOTE_REJECTED = "quote_rejected", "Quote rejected"
        QUOTE_CONVERSATION = "quote_conversation", "Quote conversation"
        SYSTEM_NOTICE = "system_notice", "System notice"
        EMAIL_DELIVERY_FAILED = "email_delivery_failed", "Email delivery failed"

    class ConversationType(models.TextChoices):
        CLIENT_QUESTION = "client_question", "Client question"
        CLIENT_COUNTER_OFFER = "client_counter_offer", "Client counter offer"
        CLIENT_CHANGE_REQUEST = "client_change_request", "Client change request"
        CLIENT_FILE_UPDATE = "client_file_update", "Client file update"
        SHOP_REPLY = "shop_reply", "Shop reply"
        SYSTEM_UPDATE = "system_update", "System update"

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    class EmailStatus(models.TextChoices):
        NOT_SENT = "not_sent", "Not sent"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        BOUNCED = "bounced", "Bounced"

    quote_request = models.ForeignKey(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name=_("quote request"),
        help_text=_("Quote request this thread message belongs to."),
    )
    quote = models.ForeignKey(
        "Quote",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
        verbose_name=_("shop quote"),
        help_text=_("Optional linked quote revision for this message."),
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_request_messages",
        verbose_name=_("sender"),
        help_text=_("User who sent this message."),
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="received_quote_request_messages",
        verbose_name=_("recipient"),
        help_text=_("User who should see this inbox message."),
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_request_messages",
        verbose_name=_("shop"),
        help_text=_("Shop this message belongs to."),
    )
    recipient_email = models.EmailField(
        blank=True,
        verbose_name=_("recipient email"),
        help_text=_("Email copy recipient, if applicable."),
    )
    sender_role = models.CharField(
        max_length=20,
        choices=SenderRole.choices,
        default=SenderRole.SYSTEM,
        verbose_name=_("sender role"),
        help_text=_("Whether this message came from the client, shop, or system."),
    )
    recipient_role = models.CharField(
        max_length=20,
        choices=RecipientRole.choices,
        default=RecipientRole.SYSTEM,
        verbose_name=_("recipient role"),
        help_text=_("Who this message is intended for."),
    )
    message_kind = models.CharField(
        max_length=20,
        choices=MessageKind.choices,
        default=MessageKind.NOTE,
        verbose_name=_("message kind"),
        help_text=_("Thread message classification for UI timelines."),
    )
    message_type = models.CharField(
        max_length=40,
        choices=MessageType.choices,
        default=MessageType.SYSTEM_NOTICE,
        verbose_name=_("message type"),
        help_text=_("Normalized message event type for inbox/outbox."),
    )
    direction = models.CharField(
        max_length=20,
        choices=Direction.choices,
        default=Direction.INBOUND,
        verbose_name=_("direction"),
        help_text=_("Inbox/outbox direction for the receiving user."),
    )
    subject = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("subject"),
        help_text=_("Short subject line for message lists."),
    )
    body = models.TextField(
        blank=True,
        default="",
        verbose_name=_("body"),
        help_text=_("Visible thread message body."),
    )
    conversation_type = models.CharField(
        max_length=40,
        choices=ConversationType.choices,
        blank=True,
        default="",
        verbose_name=_("conversation type"),
        help_text=_("Structured quote conversation type for negotiation and follow-up messages."),
    )
    proposed_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("proposed price"),
        help_text=_("Optional proposed price in a conversation message."),
    )
    proposed_turnaround = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("proposed turnaround"),
        help_text=_("Optional proposed turnaround text in a conversation message."),
    )
    proposed_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("proposed quantity"),
        help_text=_("Optional proposed quantity in a conversation message."),
    )
    proposed_material = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("proposed material"),
        help_text=_("Optional proposed material in a conversation message."),
    )
    proposed_gsm = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("proposed gsm"),
        help_text=_("Optional proposed gsm in a conversation message."),
    )
    proposed_size = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("proposed size"),
        help_text=_("Optional proposed size in a conversation message."),
    )
    proposed_finishing = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("proposed finishing"),
        help_text=_("Optional proposed finishing selections in a conversation message."),
    )
    read_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("read at"),
        help_text=_("When the recipient opened this message."),
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("sent at"),
        help_text=_("When the message event was emitted."),
    )
    email_sent = models.BooleanField(
        default=False,
        verbose_name=_("email sent"),
        help_text=_("Whether an email copy was sent successfully."),
    )
    email_status = models.CharField(
        max_length=20,
        choices=EmailStatus.choices,
        default=EmailStatus.NOT_SENT,
        verbose_name=_("email status"),
        help_text=_("Delivery state for the optional email copy."),
    )
    email_error = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("email error"),
        help_text=_("Safe delivery error summary."),
    )
    metadata = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("metadata"),
        help_text=_("Optional structured metadata for timeline rendering."),
    )

    class Meta:
        # Transitional: exits as marked in the class docstring.
        ordering = ["created_at", "id"]
        verbose_name = _("quote request message")
        verbose_name_plural = _("quote request messages")
        indexes = [
            models.Index(fields=["recipient", "read_at"], name="qmsg_recipient_read_idx"),
            models.Index(fields=["quote_request", "direction"], name="qmsg_request_direction_idx"),
            models.Index(fields=["shop", "recipient_role"], name="qmsg_shop_role_idx"),
        ]

    def __str__(self):
        return f"{self.get_sender_role_display()} message for request #{self.quote_request_id}"


class QuoteItem(TimeStampedModel):
    """Single item in a quote. Stores direct FK refs to chosen resources.
    Supports PRODUCT (catalog) and CUSTOM (client-specified) items."""

    ITEM_TYPE_CHOICES = [
        ("PRODUCT", "Product"),
        ("CUSTOM", "Custom"),
    ]
    PRICING_MODE_CHOICES = [
        ("SHEET", "Sheet"),
        ("LARGE_FORMAT", "Large Format"),
    ]
    SIDES_CHOICES = [
        ("SIMPLEX", "Simplex"),
        ("DUPLEX", "Duplex"),
    ]
    COLOR_MODE_CHOICES = [
        ("BW", "Black & White"),
        ("COLOR", "Color"),
    ]

    quote_request = models.ForeignKey(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name=_("quote request"),
        help_text=_("Quote request this item belongs to."),
    )
    quote = models.ForeignKey(
        "Quote",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="items",
        verbose_name=_("shop quote"),
        help_text=_("Shop quote this item is priced in (set when shop prices)."),
    )
    item_type = models.CharField(
        max_length=20,
        choices=ITEM_TYPE_CHOICES,
        default="PRODUCT",
        verbose_name=_("item type"),
        help_text=_("PRODUCT = catalog product; CUSTOM = client-specified."),
    )
    title = models.CharField(
        max_length=120,
        blank=True,
        default="",
        verbose_name=_("title"),
        help_text=_("Display title; required for CUSTOM items."),
    )
    spec_text = models.TextField(
        blank=True,
        default="",
        verbose_name=_("spec text"),
        help_text=_("Free-text spec from client (e.g. dimensions, qty)."),
    )
    has_artwork = models.BooleanField(
        default=False,
        verbose_name=_("has artwork"),
        help_text=_("If true, design service defaults off (client provides art)."),
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        related_name="quote_items",
        null=True,
        blank=True,
        verbose_name=_("product"),
        help_text=_("Product for this quote item (required for PRODUCT type)."),
    )
    quantity = models.PositiveIntegerField(
        default=1,
        verbose_name=_("quantity"),
        help_text=_("Quantity ordered."),
    )
    pricing_mode = models.CharField(
        max_length=20,
        choices=PRICING_MODE_CHOICES,
        blank=True,
        default="",
        verbose_name=_("pricing mode"),
        help_text=_("Sheet or large format pricing."),
    )

    # SHEET: paper required
    paper = models.ForeignKey(
        "inventory.Paper",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_items",
        verbose_name=_("paper"),
        help_text=_("Paper for sheet printing (required for SHEET mode)."),
    )
    # LARGE_FORMAT: material + dims required
    chosen_width_mm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("chosen width (mm)"),
        help_text=_("Chosen width in millimeters for large format."),
    )
    chosen_height_mm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("chosen height (mm)"),
        help_text=_("Chosen height in millimeters for large format."),
    )

    sides = models.CharField(
        max_length=10,
        choices=SIDES_CHOICES,
        blank=True,
        default="",
        verbose_name=_("sides"),
        help_text=_("Simplex (1-sided) or duplex (2-sided)."),
    )
    color_mode = models.CharField(
        max_length=10,
        choices=COLOR_MODE_CHOICES,
        blank=True,
        default="",
        verbose_name=_("color mode"),
        help_text=_("Black & white or color."),
    )
    machine = models.ForeignKey(
        "inventory.Machine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_items",
        verbose_name=_("machine"),
        help_text=_("Machine to use for printing."),
    )

    special_instructions = models.TextField(
        blank=True,
        default="",
        verbose_name=_("special instructions"),
        help_text=_("Special instructions for this item."),
    )
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("unit price"),
        help_text=_(
            "Calculated: line_total/quantity. SHEET: paper + PrintingRate; LARGE_FORMAT: material×area. "
            "If 0, fill QuoteItem.paper (+ machine+sides+color) or material+chosen_width+height."
        ),
    )
    line_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("line total"),
        help_text=_(
            "Calculated on save. SHEET: paper.selling_price×sheets + PrintingRate×sheets + finishing. "
            "LARGE_FORMAT: material.selling_price×area_sqm + finishing."
        ),
    )
    pricing_snapshot = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("pricing snapshot"),
        help_text=_(
            "Full pricing breakdown at computation time. Stores imposition_count, sheets_needed, "
            "area_m2, paper_cost, print_cost, finishing_total, services_total, and line items."
        ),
    )
    pricing_locked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("pricing locked at"),
        help_text=_("When this item was priced."),
    )
    item_spec_snapshot = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("item spec snapshot"),
        help_text=_(
            "Frozen snapshot of buyer choices at add-to-quote time. "
            "Never mutates the source product template."
        ),
    )
    needs_review = models.BooleanField(
        default=False,
        verbose_name=_("needs review"),
        help_text=_(
            "True when pricing cannot be calculated (missing setup). "
            "Item is saved; seller reviews and prices manually."
        ),
    )

    # ------------------------------------------------------------------
    # Booklet summary fields (null-safe; flat jobs leave these blank)
    # ------------------------------------------------------------------
    input_pages = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("input pages"),
        help_text=_("Raw page count as entered by the buyer (before normalisation)."),
    )
    normalized_pages = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("normalized pages"),
        help_text=_(
            "Page count rounded up to the nearest booklet_page_multiple. "
            "Stored so pricing components can reference it without re-computing."
        ),
    )
    binding_type = models.CharField(
        max_length=20,
        choices=[
            ("SADDLE_STITCH", "Saddle Stitch"),
            ("PERFECT_BIND", "Perfect Bind"),
        ],
        blank=True,
        default="",
        verbose_name=_("binding type"),
        help_text=_("Binding method chosen for this booklet item. Blank for flat jobs."),
    )

    class Meta:
        ordering = ["pk"]
        verbose_name = _("quote item")
        verbose_name_plural = _("quote items")

    @property
    def has_booklet_structure(self) -> bool:
        """True when this item carries booklet page data (cover + insert components expected)."""
        return self.normalized_pages is not None

    def __str__(self):
        if self.item_type == "PRODUCT" and self.product_id:
            name = self.product.name
        elif self.title:
            name = self.title
        else:
            name = "Item"
        return f"{name} x{self.quantity}"


class QuoteItemFinishing(TimeStampedModel):
    """
    DEPRECATED: transitional model kept only to satisfy legacy code.
    Scheduled for removal in Batch 4.
    Do NOT add new fields, FKs, or features to this model.
    """

    quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.CASCADE,
        related_name="finishings",
        verbose_name=_("quote item"),
        help_text=_("Quote item this finishing applies to."),
    )
    finishing_rate = models.ForeignKey(
        "pricing.FinishingRate",
        on_delete=models.CASCADE,
        related_name="quote_item_finishings",
        verbose_name=_("finishing rate"),
        help_text=_("Finishing rate for this item."),
    )
    coverage_qty = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        verbose_name=_("coverage quantity"),
        help_text=_("Quantity of finishing applied."),
    )
    price_override = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("price override"),
        help_text=_("Optional override for the finishing price."),
    )
    apply_to_sides = models.CharField(
        max_length=10,
        choices=[
            ("SINGLE", "Single-sided"),
            ("DOUBLE", "Double-sided"),
            ("BOTH", "Both (follows print)"),
        ],
        default="BOTH",
        blank=True,
        verbose_name=_("apply to sides"),
        help_text=_("Single-sided, double-sided, or both (uses print sides)."),
    )
    selected_side = models.CharField(
        max_length=10,
        choices=[
            ("front", "Front"),
            ("back", "Back"),
            ("both", "Both"),
        ],
        default="both",
        verbose_name=_("selected side"),
        help_text=_("Explicit selected finishing side for preview and pricing."),
    )

    class Meta:
        # Transitional: exits as marked in the class docstring.
        verbose_name = _("quote item finishing")
        verbose_name_plural = _("quote item finishings")
        constraints = [
            models.UniqueConstraint(
                fields=["quote_item", "finishing_rate"],
                name="unique_quote_item_finishing",
            )
        ]

    def __str__(self):
        return f"{self.quote_item} - {self.finishing_rate.name}"


class CalculatorDraft(TimeStampedModel):
    """Saved calculator draft owned by a client before shop requests are sent."""

    INTAKE_MODE_DIRECT_SHOP = "direct_shop"

    class Status(models.TextChoices):
        DRAFT = CalculatorDraftStatus.DRAFT, "Draft"
        SENT = CalculatorDraftStatus.SENT, "Sent"
        ARCHIVED = CalculatorDraftStatus.ARCHIVED, "Archived"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="calculator_drafts_v2",
    )
    guest_session_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    selected_product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="saved_calculator_drafts",
    )
    source_job = models.ForeignKey(
        "jobs.ManagedJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reorder_calculator_drafts",
    )
    direct_intake_shop = models.ForeignKey(
        Shop,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="direct_intake_calculator_drafts",
    )
    intake_mode = models.CharField(max_length=32, blank=True, default="", db_index=True)
    title = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    calculator_context = models.CharField(
        max_length=32,
        choices=CalculatorDraftContext.choices,
        default=CalculatorDraftContext.PUBLIC_GUEST,
    )
    intent = models.CharField(
        max_length=32,
        choices=CalculatorDraftIntent.choices,
        default=CalculatorDraftIntent.PUBLIC_PREVIEW,
    )
    draft_reference = models.CharField(max_length=50, blank=True, default="")
    custom_product_snapshot = models.JSONField(null=True, blank=True)
    calculator_inputs_snapshot = models.JSONField()
    request_details_snapshot = models.JSONField(null=True, blank=True)
    artwork_token = models.CharField(max_length=64, blank=True, default="")
    artwork_filename = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        verbose_name = _("quote draft")
        verbose_name_plural = _("quote drafts")

    def __str__(self):
        return self.title or self.draft_reference or f"Draft #{self.pk}"

    def can_send(self) -> bool:
        return self.status == CalculatorDraftStatus.DRAFT


class QuoteItemComponent(TimeStampedModel):
    """
    DEPRECATED: transitional model kept only to satisfy legacy code.
    Scheduled for removal in Batch 4.
    Do NOT add new fields, FKs, or features to this model.
    """

    COMPONENT_TYPE_CHOICES = [
        ("BODY", _("Body")),
        ("COVER", _("Cover")),
        ("INSERT", _("Insert")),
    ]

    quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.CASCADE,
        related_name="components",
        verbose_name=_("quote item"),
        help_text=_("Quote item this component belongs to."),
    )
    component_type = models.CharField(
        max_length=20,
        choices=COMPONENT_TYPE_CHOICES,
        default="BODY",
        verbose_name=_("component type"),
        help_text=_("BODY = single-part; COVER/INSERT = booklet parts."),
    )
    display_order = models.PositiveIntegerField(
        default=0,
        verbose_name=_("display order"),
        help_text=_("Order for display (lower = first)."),
    )
    paper = models.ForeignKey(
        "inventory.Paper",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_item_components",
        verbose_name=_("paper"),
        help_text=_("Paper for SHEET mode."),
    )
    chosen_width_mm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("chosen width (mm)"),
    )
    chosen_height_mm = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("chosen height (mm)"),
    )
    sides = models.CharField(
        max_length=10,
        choices=QuoteItem.SIDES_CHOICES,
        blank=True,
        default="",
        verbose_name=_("sides"),
    )
    color_mode = models.CharField(
        max_length=10,
        choices=QuoteItem.COLOR_MODE_CHOICES,
        blank=True,
        default="",
        verbose_name=_("color mode"),
    )
    pages = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("pages"),
        help_text=_(
            "Page count for this component. "
            "COVER = typically 4; INSERT = normalized_pages − 4. "
            "Null for flat (BODY) components."
        ),
    )

    class Meta:
        # Transitional: exits as marked in the class docstring.
        ordering = ["quote_item", "display_order", "pk"]
        verbose_name = _("quote item component")
        verbose_name_plural = _("quote item components")

    def __str__(self):
        return f"{self.quote_item} – {self.get_component_type_display()}"


class QuoteRequestService(TimeStampedModel):
    """
    DEPRECATED: transitional model kept only to satisfy legacy code.
    Scheduled for removal in Batch 4.
    Do NOT add new fields, FKs, or features to this model.
    """

    quote_request = models.ForeignKey(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name="services",
        verbose_name=_("quote request"),
        help_text=_("Quote request this service applies to."),
    )
    is_selected = models.BooleanField(
        default=False,
        verbose_name=_("is selected"),
        help_text=_("Pickup=false, Delivery=true."),
    )
    distance_km = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("distance (km)"),
        help_text=_("Distance for TIERED_DISTANCE. Seller can set later."),
    )
    price_override = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("price override"),
        help_text=_("Seller override (e.g. negotiated delivery)."),
    )

    class Meta:
        # Transitional: exits as marked in the class docstring.
        verbose_name = _("quote request service")
        verbose_name_plural = _("quote request services")

    def __str__(self):
        return f"Service for {self.quote_request}"


class QuoteShareLink(TimeStampedModel):
    """Shareable link for a shop quote. Token is unguessable; optional expiry."""

    quote = models.ForeignKey(
        Quote,
        on_delete=models.CASCADE,
        related_name="share_links",
        verbose_name=_("shop quote"),
        help_text=_("Shop quote this share link points to."),
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        verbose_name=_("token"),
        help_text=_("URL-safe random token for public access."),
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("expires at"),
        help_text=_("Optional expiry. Null = no expiry."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_share_links",
        verbose_name=_("created by"),
        help_text=_("User who created this share link."),
    )

    class Meta:
        verbose_name = _("quote share link")
        verbose_name_plural = _("quote share links")

    def __str__(self):
        return f"Share #{self.id} → Quote #{self.quote_id}"


class QuoteRequestAttachment(TimeStampedModel):
    """
    DEPRECATED: transitional model kept only to satisfy legacy code.
    Scheduled for removal in Batch 4.
    Do NOT add new fields, FKs, or features to this model.
    """

    quote_request = models.ForeignKey(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name=_("quote request"),
    )
    file = models.FileField(
        upload_to="quote_requests/%Y/%m/",
        verbose_name=_("file"),
        help_text=_("Uploaded file (artwork, spec, etc.)."),
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("name"),
        help_text=_("Optional display name for the file."),
    )

    class Meta:
        # Transitional: exits as marked in the class docstring.
        verbose_name = _("quote request attachment")
        verbose_name_plural = _("quote request attachments")

    def __str__(self):
        return self.name or self.file.name


def pending_artwork_upload_to(instance, filename):
    session_key = getattr(instance, "session_key", "") or "guest"
    return f"pending_artwork/{session_key}/{filename}"


class PendingArtworkUpload(TimeStampedModel):
    """Temporary guest artwork upload linked by unguessable token."""

    token = models.CharField(max_length=64, unique=True, db_index=True)
    session_key = models.CharField(max_length=64, db_index=True)
    file = models.FileField(upload_to=pending_artwork_upload_to, verbose_name=_("file"))
    original_filename = models.CharField(max_length=255, blank=True, default="")
    file_size = models.BigIntegerField(default=0)
    content_type = models.CharField(max_length=100, blank=True, default="")
    expires_at = models.DateTimeField()
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claimed_pending_artwork",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("pending artwork upload")
        verbose_name_plural = _("pending artwork uploads")
        indexes = [
            models.Index(fields=["session_key", "expires_at"], name="pending_art_exp_idx"),
        ]

    def __str__(self):
        return self.original_filename or self.token


class QuoteAttachment(TimeStampedModel):
    """
    DEPRECATED: transitional model kept only to satisfy legacy code.
    Scheduled for removal in Batch 4.
    Do NOT add new fields, FKs, or features to this model.
    """

    quote = models.ForeignKey(
        Quote,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name=_("shop quote"),
    )
    file = models.FileField(
        upload_to="quotes/%Y/%m/",
        verbose_name=_("file"),
        help_text=_("Uploaded file (proof, revised spec, etc.)."),
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("name"),
        help_text=_("Optional display name for the file."),
    )

    class Meta:
        # Transitional: exits as marked in the class docstring.
        verbose_name = _("shop quote attachment")
        verbose_name_plural = _("shop quote attachments")

    def __str__(self):
        return self.name or self.file.name


class QuoteItemAttachment(TimeStampedModel):
    """
    DEPRECATED: transitional model kept only to satisfy legacy code.
    Scheduled for removal in Batch 4.
    Do NOT add new fields, FKs, or features to this model.
    """

    quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name=_("quote item"),
    )
    file = models.ImageField(
        upload_to="quote_item_attachments/%Y/%m/",
        verbose_name=_("file"),
        help_text=_("Uploaded reference image for this quote item."),
    )
    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("name"),
        help_text=_("Optional display name for the image."),
    )

    class Meta:
        # Transitional: exits as marked in the class docstring.
        verbose_name = _("quote item attachment")
        verbose_name_plural = _("quote item attachments")

    def __str__(self):
        return self.name or self.file.name


class QuoteItemService(TimeStampedModel):
    """
    DEPRECATED: transitional model kept only to satisfy legacy code.
    Scheduled for removal in Batch 4.
    Do NOT add new fields, FKs, or features to this model.
    """

    quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.CASCADE,
        related_name="services",
        verbose_name=_("quote item"),
        help_text=_("Quote item this service applies to."),
    )
    is_selected = models.BooleanField(
        default=False,
        verbose_name=_("is selected"),
        help_text=_("I need design help."),
    )
    price_override = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name=_("price override"),
        help_text=_("Seller override (negotiable)."),
    )
    note = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("note"),
        help_text=_("Optional note."),
    )

    class Meta:
        # Transitional: exits as marked in the class docstring.
        verbose_name = _("quote item service")
        verbose_name_plural = _("quote item services")

    def __str__(self):
        return f"Service for {self.quote_item}"
