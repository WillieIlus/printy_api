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
    """Print shop - owner is the seller."""

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
    business_email = models.EmailField(
        blank=True,
        default="shop@example.com",
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
    location = models.ForeignKey(
        "locations.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shops",
        verbose_name=_("location"),
        help_text=_("SEO location (neighborhood/city) for marketplace pages."),
    )
    pricing_ready = models.BooleanField(
        default=False,
        verbose_name=_("pricing ready"),
        help_text=_("Denormalized flag: True when shop has at least one machine, paper, and printing rate."),
    )
    public_match_ready = models.BooleanField(
        default=False,
        verbose_name=_("public match ready"),
        help_text=_("True when the shop can produce usable public calculator preview results."),
    )
    supports_custom_requests = models.BooleanField(
        default=True,
        verbose_name=_("supports custom requests"),
        help_text=_("Whether public calculator custom requests can be matched to this shop."),
    )
    supports_catalog_requests = models.BooleanField(
        default=True,
        verbose_name=_("supports catalog requests"),
        help_text=_("Whether public catalog/tweak calculators can match against this shop."),
    )
    is_public = models.BooleanField(
        default=True,
        verbose_name=_("is public"),
        help_text=_("Whether this shop can appear in public marketplace matching and browsing."),
    )
    opening_time = models.TimeField(
        default="08:00",
        verbose_name=_("opening time"),
        help_text=_("Default opening time (e.g. 08:00). Used when no per-day override in OpeningHours."),
    )
    closing_time = models.TimeField(
        default="18:00",
        verbose_name=_("closing time"),
        help_text=_("Default closing time (e.g. 18:00). Used when no per-day override in OpeningHours."),
    )
    closing_soon_minutes = models.PositiveSmallIntegerField(
        default=30,
        verbose_name=_("closing soon minutes"),
        help_text=_("Minutes before closing to show 'Closing soon' status (e.g. 30)."),
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


class ShopMembership(models.Model):
    """Optional shop membership for staff and delegated operations."""

    class Role(models.TextChoices):
        STAFF = "staff", "Staff"
        MANAGER = "manager", "Manager"

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shop_memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STAFF,
    )
    can_manage_setup = models.BooleanField(default=False)
    can_manage_products = models.BooleanField(default=False)
    can_manage_pricing = models.BooleanField(default=False)
    can_manage_quotes = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("shop membership")
        verbose_name_plural = _("shop memberships")
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "user"],
                name="unique_shop_membership",
            )
        ]

    def __str__(self):
        return f"{self.user_id} @ {self.shop_id} ({self.role})"


class OpeningHours(models.Model):
    """
    Per-weekday opening hours. 1=Monday .. 7=Sunday (ISO).
    Default: Mon–Fri 08:00–18:00, Sat–Sun closed.
    """

    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="opening_hours",
        verbose_name=_("shop"),
        help_text=_("Shop these hours belong to."),
    )
    weekday = models.PositiveSmallIntegerField(
        default=WEEKDAY_MONDAY,
        verbose_name=_("weekday"),
        help_text=_("1=Monday, 2=Tuesday, ..., 7=Sunday (ISO)."),
    )
    from_hour = models.CharField(
        max_length=5,
        default="08:00",
        blank=True,
        verbose_name=_("from hour"),
        help_text=_("Opening time (HH:MM format, e.g. 08:00)."),
    )
    to_hour = models.CharField(
        max_length=5,
        default="18:00",
        blank=True,
        verbose_name=_("to hour"),
        help_text=_("Closing time (HH:MM format, e.g. 18:00)."),
    )
    is_closed = models.BooleanField(
        default=False,
        verbose_name=_("closed"),
        help_text=_("If True, shop is closed on this day."),
    )

    class Meta:
        verbose_name = _("opening hours")
        verbose_name_plural = _("opening hours")
        ordering = ["weekday"]
        constraints = [
            models.UniqueConstraint(
                fields=["shop", "weekday"],
                name="unique_shop_weekday",
            )
        ]

    def __str__(self):
        if self.is_closed:
            return f"{self.get_weekday_display()} — Closed"
        return f"{self.get_weekday_display()} {self.from_hour}–{self.to_hour}"

    def get_weekday_display(self):
        weekday_names = {
            1: "Monday",
            2: "Tuesday",
            3: "Wednesday",
            4: "Thursday",
            5: "Friday",
            6: "Saturday",
            7: "Sunday",
        }
        return weekday_names.get(self.weekday, f"Day {self.weekday}")


class FavoriteShop(models.Model):
    """Buyer favorite - one per (user, shop)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorite_shops",
        verbose_name=_("user"),
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="favorited_by",
        verbose_name=_("shop"),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
    )

    class Meta:
        verbose_name = _("favorite shop")
        verbose_name_plural = _("favorite shops")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "shop"],
                name="unique_user_shop_favorite",
            )
        ]

    def __str__(self):
        return f"{self.user} favorites {self.shop}"


class ShopRating(models.Model):
    """Buyer rating for a shop - one per (user, shop)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shop_ratings",
        verbose_name=_("user"),
    )
    shop = models.ForeignKey(
        Shop,
        on_delete=models.CASCADE,
        related_name="ratings",
        verbose_name=_("shop"),
    )
    stars = models.PositiveSmallIntegerField(
        verbose_name=_("stars"),
        help_text=_("Rating 1-5."),
    )
    comment = models.TextField(
        blank=True,
        default="",
        verbose_name=_("comment"),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
    )

    class Meta:
        verbose_name = _("shop rating")
        verbose_name_plural = _("shop ratings")
        constraints = [
            models.UniqueConstraint(
                fields=["user", "shop"],
                name="unique_user_shop_rating",
            )
        ]

    def __str__(self):
        return f"{self.user} rated {self.shop} {self.stars} stars"
