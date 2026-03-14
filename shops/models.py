"""
Shop model. Inventory (Machine, Paper), pricing (PrintingRate, FinishingRate, Material),
and catalog (Product) live in their respective apps.
"""
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from common.slug import AutoSlugMixin


class Shop(AutoSlugMixin, models.Model):
    """Print shop - owner is the seller."""

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
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether the shop is active and visible."),
    )
    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
        help_text=_("Business description for the shop."),
    )
    business_email = models.EmailField(
        blank=True,
        default="",
        verbose_name=_("business email"),
        help_text=_("Contact email for the shop."),
    )
    phone_number = models.CharField(
        max_length=32,
        blank=True,
        default="",
        verbose_name=_("phone number"),
        help_text=_("Contact phone for the shop."),
    )
    address_line = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("address"),
        help_text=_("Street address."),
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("city"),
        help_text=_("City."),
    )
    state = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("state or province"),
        help_text=_("State or province."),
    )
    country = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("country"),
        help_text=_("Country."),
    )
    zip_code = models.CharField(
        max_length=20,
        blank=True,
        default="",
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
