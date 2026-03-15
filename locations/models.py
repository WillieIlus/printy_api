"""
Location model for SEO and marketplace pages.
Locations are geographic areas: neighborhoods (Westlands, Kilimani), cities (Nairobi, Mombasa).
"""
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel
from common.slug import AutoSlugMixin


class Location(AutoSlugMixin, models.Model):
    """
    Geographic location for SEO pages.
    Examples: Westlands, Kilimani, CBD (Nairobi neighborhoods); Nairobi, Mombasa (cities).
    """

    slug_source_field = "name"

    name = models.CharField(
        max_length=255,
        verbose_name=_("name"),
        help_text=_("Display name (e.g. Westlands, Nairobi)."),
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("city"),
        help_text=_("City (e.g. Nairobi, Mombasa)."),
    )
    county = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name=_("county"),
        help_text=_("County or region (optional)."),
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=_("latitude"),
        help_text=_("Latitude for geo display and search."),
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        verbose_name=_("longitude"),
        help_text=_("Longitude for geo display and search."),
    )
    google_place_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("Google Place ID"),
        help_text=_("Stable identifier from Google Places for reuse and geocoding."),
    )
    description = models.TextField(
        blank=True,
        default="",
        verbose_name=_("description"),
        help_text=_("SEO description for the location page."),
    )
    slug = models.SlugField(
        max_length=100,
        unique=True,
        blank=True,
        verbose_name=_("slug"),
        help_text=_("URL-friendly identifier. Auto-generated from name if blank."),
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name=_("parent"),
        help_text=_("Parent location (e.g. Westlands → Nairobi)."),
    )
    location_type = models.CharField(
        max_length=20,
        choices=[
            ("neighborhood", _("Neighborhood")),
            ("city", _("City")),
            ("county", _("County")),
        ],
        default="neighborhood",
        verbose_name=_("type"),
        help_text=_("Location type for hierarchy and display."),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("is active"),
        help_text=_("Whether this location is visible in SEO pages."),
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("created at"),
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_("updated at"),
    )

    class Meta:
        verbose_name = _("location")
        verbose_name_plural = _("locations")
        ordering = ["name"]
        indexes = [
            models.Index(fields=["slug"], name="locations_slug_idx"),
            models.Index(fields=["is_active"], name="locations_active_idx"),
        ]

    def __str__(self):
        return self.name
