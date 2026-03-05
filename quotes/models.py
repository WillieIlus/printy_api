"""
Quote models: QuoteRequest (buyer's request), QuoteItem (line items), QuoteItemFinishing.
Direct FK references to avoid attribute-based lookups (no MultipleObjectsReturned).
No redundancy: QuoteRequest = header (shop, customer, status); QuoteItem = line items
with chosen product/paper/material/machine at quote time (not looked up by attributes).
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel
from core.querysets import QuoteRequestQuerySet, QuoteItemQuerySet
from shops.models import Shop


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


class QuoteRequest(TimeStampedModel):
    """Quote request - buyer creates, seller prices."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PRICED = "PRICED"
    SENT = "SENT"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    STATUS_CHOICES = [
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (PRICED, "Priced"),
        (SENT, "Sent"),
        (ACCEPTED, "Accepted"),
        (REJECTED, "Rejected"),
        (EXPIRED, "Expired"),
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
        help_text=_("Current status of the quote request."),
    )
    notes = models.TextField(
        blank=True,
        default="",
        verbose_name=_("notes"),
        help_text=_("Additional notes for the quote."),
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
    customer_inquiry = models.ForeignKey(
        CustomerInquiry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_requests",
        verbose_name=_("customer inquiry"),
        help_text=_("Optional linked customer inquiry."),
    )
    whatsapp_message = models.TextField(
        blank=True,
        default="",
        verbose_name=_("whatsapp message"),
        help_text=_("Message sent via WhatsApp when quote was sent."),
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("sent at"),
        help_text=_("When the quote was sent to the customer."),
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = _("quote request")
        verbose_name_plural = _("quote requests")

    def __str__(self):
        return f"Quote #{self.id} - {self.customer_name} ({self.status})"

    @property
    def totals(self):
        """Alias for total for API compatibility."""
        return self.total


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
    """Shareable link for a quote. Token is unguessable; optional expiry."""

    quote = models.ForeignKey(
        QuoteRequest,
        on_delete=models.CASCADE,
        related_name="share_links",
        verbose_name=_("quote"),
        help_text=_("Quote this share link points to."),
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
