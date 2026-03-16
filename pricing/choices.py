"""Choice enums for pricing app."""

from django.db import models


class ColorMode(models.TextChoices):
    BW = "BW", "Black & White"
    COLOR = "COLOR", "Color"


class Sides(models.TextChoices):
    SIMPLEX = "SIMPLEX", "Simplex (1-sided)"
    DUPLEX = "DUPLEX", "Duplex (2-sided)"


class ChargeUnit(models.TextChoices):
    PER_PIECE = "PER_PIECE", "Per Piece"
    PER_SIDE = "PER_SIDE", "Per Side"
    PER_SHEET = "PER_SHEET", "Per Sheet"
    PER_SIDE_PER_SHEET = "PER_SIDE_PER_SHEET", "Per Side Per Sheet"
    PER_SQM = "PER_SQM", "Per Square Meter"
    FLAT = "FLAT", "Flat"


class FinishingSides(models.TextChoices):
    """Whether finishing applies to one side or both sides."""

    SINGLE = "SINGLE", "Single-sided"
    DOUBLE = "DOUBLE", "Double-sided"
    BOTH = "BOTH", "Both (follows print sides)"


class ServicePricingType(models.TextChoices):
    """How a service charge is calculated."""

    FIXED = "FIXED", "Fixed price"
    TIERED_DISTANCE = "TIERED_DISTANCE", "Distance-based tiers"


class ServiceCode(models.TextChoices):
    """Standard service codes."""

    DESIGN = "DESIGN", "Design"
    DELIVERY = "DELIVERY", "Delivery"
    RUSH = "RUSH", "Rush / Urgent"
    SETUP = "SETUP", "Setup"
