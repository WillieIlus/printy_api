"""
Quote models: QuoteRequest (customer request), ShopQuote (shop response), QuoteItem (line items).

Domain separation:
- QuoteRequest: customer's request for a quote (draft → submitted → quoted → accepted/closed)
- ShopQuote: shop's priced offer (sent → accepted/declined/expired; supports revisions)
- QuoteItem: line items with specs; prices filled when linked to ShopQuote
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel
from core.querysets import QuoteRequestQuerySet, QuoteItemQuerySet
from shops.models import Shop
from .choices import QuoteDraftStatus, QuoteStatus, ShopQuoteStatus


class CustomerInquiry(TimeStampedModel):
    """Optional customer inquiry (name, phone, email) for tracking before quote creation."""

    name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("name"),
        help_text=_("Customer name."),
    )
    phone = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("phone"),
        help_text=_("Customer phone number."),
    )
    email = models.EmailField(
        blank=True,
        verbose_name=_("email"),
        help_text=_("Customer email address."),
    )

    class Meta:
        verbose_name = _("customer inquiry")
        verbose_name_plural = _("customer inquiries")

    def __str__(self):
        return self.name or self.email or self.phone or f"Inquiry #{self.id}"


class QuoteDraftFile(TimeStampedModel):
    """Company/customer-level grouping for one or more shop-specific quote drafts."""

    OPEN = "open"
    CLOSED = "closed"
    STATUS_CHOICES = [
        (OPEN, "Open"),
        (CLOSED, "Closed"),
    ]

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quote_draft_files",
        verbose_name=_("created by"),
        help_text=_("User who owns this quote draft file."),
    )
    company_name = models.CharField(
        max_length=255,
        default="Untitled Company",
        verbose_name=_("company name"),
        help_text=_("Top-level customer or company name used to group quote drafts."),
    )
    contact_name = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("contact name"),
        help_text=_("Optional contact person for this quote draft file."),
    )
    contact_email = models.EmailField(
        blank=True,
        verbose_name=_("contact email"),
        help_text=_("Optional contact email for this quote draft file."),
    )
    contact_phone = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("contact phone"),
        help_text=_("Optional contact phone for this quote draft file."),
    )
    notes = models.TextField(
        blank=True,
        default="",
        verbose_name=_("notes"),
        help_text=_("Shared notes for the grouped quote draft file."),
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=OPEN,
        verbose_name=_("status"),
        help_text=_("Open files can receive drafts from multiple shops. Closed files are read-only groupings."),
    )

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        verbose_name = _("quote draft file")
        verbose_name_plural = _("quote draft files")
        indexes = [
            models.Index(fields=["created_by", "status"], name="draft_file_user_status_idx"),
        ]

    def __str__(self):
        return self.company_name or f"Draft file #{self.id}"


class QuoteRequest(TimeStampedModel):
    """
    Customer's request for a quote. Created by buyer; visible to buyer and shop.
    Lifecycle: draft → submitted → [viewed] → quoted → accepted | closed | cancelled.
    """

    DRAFT = "draft"
    SUBMITTED = "submitted"
    VIEWED = "viewed"
    QUOTED = "quoted"
    ACCEPTED = "accepted"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (VIEWED, "Viewed"),
        (QUOTED, "Quoted"),
        (ACCEPTED, "Accepted"),
        (CLOSED, "Closed"),
        (CANCELLED, "Cancelled"),
    ]

    objects = QuoteRequestQuerySet.as_manager()
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="quote_requests",
        verbose_name=_("shop"),
        help_text=_("Shop this quote request is for."),
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
        max_length=20,
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
    customer_inquiry = models.ForeignKey(
        CustomerInquiry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_requests",
        verbose_name=_("customer inquiry"),
        help_text=_("Optional linked customer inquiry."),
    )
    customer = models.ForeignKey(
        "production.Customer",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_requests",
        verbose_name=_("customer"),
        help_text=_("Optional link to unified customer record (for repeat customers)."),
    )
    delivery_address = models.TextField(
        blank=True,
        default="",
        verbose_name=_("delivery address"),
        help_text=_("Full address for delivery (street, building, etc.)."),
    )
    delivery_location = models.ForeignKey(
        "locations.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_requests",
        verbose_name=_("delivery location"),
        help_text=_("Area/neighborhood for delivery (e.g. Westlands, Kilimani)."),
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
    quote_draft_file = models.ForeignKey(
        QuoteDraftFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="drafts",
        verbose_name=_("quote draft file"),
        help_text=_("Optional company-level grouping for active quote drafts."),
    )
    request_reference = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name=_("request reference"),
        help_text=_("Stable reference for customer-facing quote requests."),
    )
    source_draft = models.ForeignKey(
        "QuoteDraft",
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

    def get_latest_shop_quote(self):
        """Return the most recent sent/accepted ShopQuote, or None."""
        return self.shop_quotes.filter(
            status__in=[ShopQuote.SENT, ShopQuote.REVISED, ShopQuote.ACCEPTED]
        ).order_by("-sent_at", "-created_at").first()

    def get_latest_response(self):
        return self.shop_quotes.order_by("-created_at", "-id").first()


class ShopQuote(TimeStampedModel):
    """
    Shop's priced offer in response to a QuoteRequest. Created by shop; visible to shop and customer.
    Supports revisions: one QuoteRequest can have multiple ShopQuotes (latest is active).
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
        related_name="shop_quotes",
        verbose_name=_("quote request"),
        help_text=_("Quote request this offer responds to."),
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="shop_quotes",
        verbose_name=_("shop"),
        help_text=_("Shop that sent this quote."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shop_quotes",
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

    class Meta:
        ordering = ["-sent_at", "-created_at"]
        verbose_name = _("shop quote")
        verbose_name_plural = _("shop quotes")
        indexes = [
            models.Index(fields=["shop", "status"], name="shopquote_shop_status_idx"),
            models.Index(fields=["quote_request", "-created_at"], name="shopquote_req_created_idx"),
        ]

    def __str__(self):
        return f"Quote #{self.id} for Request #{self.quote_request_id} ({self.status})"

    def is_terminal_status(self) -> bool:
        return self.status in {ShopQuoteStatus.ACCEPTED, ShopQuoteStatus.REJECTED}


class QuoteRequestMessage(TimeStampedModel):
    """Lightweight request thread message between client and shop."""

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

    quote_request = models.ForeignKey(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name=_("quote request"),
        help_text=_("Quote request this thread message belongs to."),
    )
    shop_quote = models.ForeignKey(
        "ShopQuote",
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
    sender_role = models.CharField(
        max_length=20,
        choices=SenderRole.choices,
        default=SenderRole.SYSTEM,
        verbose_name=_("sender role"),
        help_text=_("Whether this message came from the client, shop, or system."),
    )
    message_kind = models.CharField(
        max_length=20,
        choices=MessageKind.choices,
        default=MessageKind.NOTE,
        verbose_name=_("message kind"),
        help_text=_("Thread message classification for UI timelines."),
    )
    body = models.TextField(
        blank=True,
        default="",
        verbose_name=_("body"),
        help_text=_("Visible thread message body."),
    )
    metadata = models.JSONField(
        null=True,
        blank=True,
        verbose_name=_("metadata"),
        help_text=_("Optional structured metadata for timeline rendering."),
    )

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = _("quote request message")
        verbose_name_plural = _("quote request messages")

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
    shop_quote = models.ForeignKey(
        "ShopQuote",
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
    material = models.ForeignKey(
        "pricing.Material",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_items",
        verbose_name=_("material"),
        help_text=_("Material for large format (required for LARGE_FORMAT mode)."),
    )
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

    class Meta:
        ordering = ["pk"]
        verbose_name = _("quote item")
        verbose_name_plural = _("quote items")

    def __str__(self):
        if self.item_type == "PRODUCT" and self.product_id:
            name = self.product.name
        elif self.title:
            name = self.title
        else:
            name = "Item"
        return f"{name} x{self.quantity}"


class QuoteItemFinishing(TimeStampedModel):
    """Finishing applied to a quote item."""

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


class QuoteDraft(TimeStampedModel):
    """Saved calculator draft owned by a client before shop requests are sent."""

    class Status(models.TextChoices):
        DRAFT = QuoteDraftStatus.DRAFT, "Draft"
        SENT = QuoteDraftStatus.SENT, "Sent"
        ARCHIVED = QuoteDraftStatus.ARCHIVED, "Archived"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quote_drafts_v2",
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_drafts_v2",
    )
    selected_product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="saved_quote_drafts",
    )
    title = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    draft_reference = models.CharField(max_length=50, blank=True, default="")
    custom_product_snapshot = models.JSONField(null=True, blank=True)
    calculator_inputs_snapshot = models.JSONField()
    pricing_snapshot = models.JSONField(null=True, blank=True)
    request_details_snapshot = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        verbose_name = _("quote draft")
        verbose_name_plural = _("quote drafts")

    def __str__(self):
        return self.title or self.draft_reference or f"Draft #{self.pk}"

    def can_send(self) -> bool:
        return self.status == QuoteDraftStatus.DRAFT


class QuoteItemComponent(TimeStampedModel):
    """
    Multi-part job component: cover, insert, body.
    For simple items: one BODY component. For booklets: COVER + INSERT.
    Each component has its own paper/material, sides, color_mode.
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
    material = models.ForeignKey(
        "pricing.Material",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_item_components",
        verbose_name=_("material"),
        help_text=_("Material for LARGE_FORMAT mode."),
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

    class Meta:
        ordering = ["quote_item", "display_order", "pk"]
        verbose_name = _("quote item component")
        verbose_name_plural = _("quote item components")

    def __str__(self):
        return f"{self.quote_item} – {self.get_component_type_display()}"


class QuoteRequestService(TimeStampedModel):
    """Service applied once per quote (e.g. delivery)."""

    quote_request = models.ForeignKey(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name="services",
        verbose_name=_("quote request"),
        help_text=_("Quote request this service applies to."),
    )
    service_rate = models.ForeignKey(
        "pricing.ServiceRate",
        on_delete=models.CASCADE,
        related_name="quote_request_services",
        verbose_name=_("service rate"),
        help_text=_("Service rate (e.g. Delivery)."),
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
        verbose_name = _("quote request service")
        verbose_name_plural = _("quote request services")
        constraints = [
            models.UniqueConstraint(
                fields=["quote_request", "service_rate"],
                name="unique_quote_request_service",
            )
        ]

    def __str__(self):
        return f"{self.quote_request} - {self.service_rate.get_code_display()}"


class QuoteShareLink(TimeStampedModel):
    """Shareable link for a shop quote. Token is unguessable; optional expiry."""

    shop_quote = models.ForeignKey(
        ShopQuote,
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
        return f"Share #{self.id} → Quote #{self.shop_quote_id}"


class QuoteRequestAttachment(TimeStampedModel):
    """File attachment on a quote request (e.g. artwork, spec document)."""

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
        verbose_name = _("quote request attachment")
        verbose_name_plural = _("quote request attachments")

    def __str__(self):
        return self.name or self.file.name


class ShopQuoteAttachment(TimeStampedModel):
    """File attachment on a shop quote (e.g. revised spec, proof)."""

    shop_quote = models.ForeignKey(
        ShopQuote,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name=_("shop quote"),
    )
    file = models.FileField(
        upload_to="shop_quotes/%Y/%m/",
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
        verbose_name = _("shop quote attachment")
        verbose_name_plural = _("shop quote attachments")

    def __str__(self):
        return self.name or self.file.name


class QuoteItemService(TimeStampedModel):
    """Service applied per item (e.g. design)."""

    quote_item = models.ForeignKey(
        QuoteItem,
        on_delete=models.CASCADE,
        related_name="services",
        verbose_name=_("quote item"),
        help_text=_("Quote item this service applies to."),
    )
    service_rate = models.ForeignKey(
        "pricing.ServiceRate",
        on_delete=models.CASCADE,
        related_name="quote_item_services",
        verbose_name=_("service rate"),
        help_text=_("Service rate (e.g. Design)."),
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
        verbose_name = _("quote item service")
        verbose_name_plural = _("quote item services")
        constraints = [
            models.UniqueConstraint(
                fields=["quote_item", "service_rate"],
                name="unique_quote_item_service",
            )
        ]

    def __str__(self):
        return f"{self.quote_item} - {self.service_rate.get_code_display()}"
