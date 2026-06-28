"""Choice enums for catalog app."""

from django.db import models


class PricingMode(models.TextChoices):
    SHEET = "SHEET", "Sheet"
    LARGE_FORMAT = "LARGE_FORMAT", "Large Format"


class ProductStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    PUBLISHED = "PUBLISHED", "Published"
    UNAVAILABLE = "UNAVAILABLE", "Unavailable"


class ProductKind(models.TextChoices):
    FLAT = "FLAT", "Flat"
    BOOKLET = "BOOKLET", "Booklet"


class BindingType(models.TextChoices):
    SADDLE_STITCH = "SADDLE_STITCH", "Saddle Stitch"
    PERFECT_BIND = "PERFECT_BIND", "Perfect Bind"
