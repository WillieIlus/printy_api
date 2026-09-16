"""
Shop model. Inventory (Machine, Paper), pricing (PrintingRate, FinishingRate, Material),
and catalog (Product) live in their respective apps.
"""
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from common.slug import AutoSlugMixin

# Weekday constants: 1=Mon .. 7=Sun (ISO)
WEEKDAY_MONDAY = 1
WEEKDAY_SUNDAY = 7


class Shop(AutoSlugMixin, models.Model):
    """
    Print shop - owner is the seller.

    Pricing lives in canonical pricing settings and rate tables.
    """

    class VatMode(models.TextChoices):
        INCLUSIVE = "inclusive", _("Inclusive")
        EXCLUSIVE = "exclusive", _("Exclusive")

    slug_source_field = "name"

    name = models.CharField(
        max_length=255,
        default="",
        verbose_name=_("name"),
        help_text=_("Display name of the print shop."),
    )
    slug = models.SlugField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        verbose_name=_("slug"),
        help_text=_("URL-friendly identifier for the shop."),
    )
    currency = models.CharField(
        max_length=3,
        default="KES",
        verbose_name=_("currency"),
        help_text=_("ISO 4217 currency code (e.g. KES, USD)."),
    )
    is_vat_enabled = models.BooleanField(
        default=False,
        verbose_name=_("VAT enabled"),
        help_text=_("Whether VAT should be applied to quote calculations for this shop."),
    )
    vat_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default="16.00",
        verbose_name=_("VAT rate"),
        help_text=_("VAT rate percentage applied when VAT is enabled."),
    )
    vat_mode = models.CharField(
        max_length=20,
        choices=VatMode.choices,
        default=VatMode.EXCLUSIVE,
        verbose_name=_("VAT mode"),
        help_text=_("Whether prices returned by the pricing engine are VAT-inclusive or VAT-exclusive."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether the shop is active and visible."),
    )
    description = models.TextField(
        blank=True,
        default="Business description for the shop.",
        verbose_name=_("description"),
        help_text=_("Business description for the shop."),
    )
    service_area = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("service area"),
        help_text=_("Public-facing areas or delivery coverage for this shop."),
    )
    turnaround_statement = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("turnaround statement"),
        help_text=_("Short public-facing turnaround summary shown to buyers."),
    )
    opening_hours_text = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("opening hours text"),
        help_text=_("Optional public summary such as Mon-Sat, 8am-6pm."),
    )
    public_whatsapp_number = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name=_("public WhatsApp number"),
        help_text=_("Optional WhatsApp/business number intentionally shared with buyers."),
    )
    public_email = models.EmailField(
        blank=True,
        default="",
        verbose_name=_("public email"),
        help_text=_("Optional public contact email intentionally shared with buyers."),
    )
    business_email = models.EmailField(
        blank=True,
        default="shop@printy.ke",
        verbose_name=_("business email"),
        help_text=_("Contact email for the shop."),
    )
    phone_number = models.CharField(
        max_length=32,
        blank=True,
        default="+254 700 000 000",
        verbose_name=_("phone number"),
        help_text=_("Contact phone for the shop."),
    )
    address_line = models.CharField(
        max_length=255,
        blank=True,
        default="Street address",
        verbose_name=_("address"),
        help_text=_("Street address."),
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        default="Nairobi",
        verbose_name=_("city"),
        help_text=_("City."),
    )
    state = models.CharField(
        max_length=100,
        blank=True,
        default="Nairobi",
        verbose_name=_("state or province"),
        help_text=_("State or province."),
    )
    country = models.CharField(
        max_length=100,
        blank=True,
        default="Kenya",
        verbose_name=_("country"),
        help_text=_("Country."),
    )
    zip_code = models.CharField(
        max_length=20,
        blank=True,
        default="00100",
        verbose_name=_("postal code"),
        help_text=_("Postal or ZIP code."),
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=_("latitude"),
        help_text=_("Latitude for geo search."),
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=_("longitude"),
        help_text=_("Longitude for geo search."),
    )
    google_place_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("Google Place ID"),
        help_text=_("Stable identifier from Google Places for reuse and geocoding."),
    )
    pricing_ready = models.BooleanField(
        default=False,
        verbose_name=_("pricing ready"),
        help_text=_("DEPRECATED setup flag. Not an authoritative pricing source."),
    )
    public_match_ready = models.BooleanField(
        default=False,
        verbose_name=_("public match ready"),
        help_text=_("DEPRECATED routing flag; Batch 7 will replace calculator routing."),
    )
    supports_custom_requests = models.BooleanField(
        default=True,
        verbose_name=_("supports custom requests"),
        help_text=_("DEPRECATED routing flag; Batch 7 will replace calculator routing."),
    )
    supports_catalog_requests = models.BooleanField(
        default=True,
        verbose_name=_("supports catalog requests"),
        help_text=_("DEPRECATED routing flag; Batch 7 will replace calculator routing."),
    )
    is_public = models.BooleanField(
        default=True,
        verbose_name=_("is public"),
        help_text=_("Whether this shop can appear in public marketplace matching and browsing."),
    )
    opening_time = models.TimeField(
        default="08:00",
        verbose_name=_("opening time"),
        help_text=_("Default opening time (e.g. 08:00)."),
    )
    closing_time = models.TimeField(
        default="18:00",
        verbose_name=_("closing time"),
        help_text=_("Default closing time (e.g. 18:00)."),
    )
    closing_soon_minutes = models.PositiveSmallIntegerField(
        default=30,
        verbose_name=_("closing soon minutes"),
        help_text=_("Minutes before closing to show 'Closing soon' status (e.g. 30)."),
    )
    timezone = models.CharField(
        max_length=64,
        default="Africa/Nairobi",
        verbose_name=_("timezone"),
        help_text=_("IANA timezone used for ready-time projections (e.g. Africa/Nairobi)."),
    )
    same_day_cutoff_time = models.TimeField(
        null=True,
        blank=True,
        verbose_name=_("same-day cutoff time"),
        help_text=_("Optional cutoff after which new work starts on the next working slot."),
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_shops",
        verbose_name=_("owner"),
        help_text=_("User who owns this shop."),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
        help_text=_("Timestamp when the record was created."),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("updated at"),
        help_text=_("Timestamp when the record was last updated."),
    )

    class Meta:
        verbose_name = _("shop")
        verbose_name_plural = _("shops")
        indexes = [
            models.Index(fields=["latitude", "longitude"], name="shops_geo_idx"),
        ]

    def __str__(self):
        return self.name

    def is_seller(self, user):
        """Check if user is seller (owner) for this shop."""
        return user.is_authenticated and self.owner_id == user.pk


