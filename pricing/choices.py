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


class FinishingBillingBasis(models.TextChoices):
    PER_SHEET = "per_sheet", "Per Sheet"
    PER_PIECE = "per_piece", "Per Piece"
    FLAT_PER_JOB = "flat_per_job", "Flat Per Job"
    FLAT_PER_GROUP = "flat_per_group", "Flat Per Group"
    FLAT_PER_LINE = "flat_per_line", "Flat Per Line"


class FinishingSideMode(models.TextChoices):
    IGNORE_SIDES = "ignore_sides", "Ignore Sides"
    PER_SELECTED_SIDE = "per_selected_side", "Per Selected Side"


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
