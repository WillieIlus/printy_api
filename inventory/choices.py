"""Choice enums for inventory app."""

from django.db import models


class MachineType(models.TextChoices):
    OFFSET = "OFFSET", "Offset"
    DIGITAL = "DIGITAL", "Digital"
    LARGE_FORMAT = "LARGE_FORMAT", "Large Format"


class SheetSize(models.TextChoices):
    A4 = "A4", "A4"
    A3 = "A3", "A3"
    SRA3 = "SRA3", "SRA3"
    A2 = "A2", "A2"
    A1 = "A1", "A1"
    A0 = "A0", "A0"
    CUSTOM = "CUSTOM", "Custom"


class PaperType(models.TextChoices):
    COATED = "COATED", "Coated"
    UNCOATED = "UNCOATED", "Uncoated"
    RECYCLED = "RECYCLED", "Recycled"
    GLOSS = "GLOSS", "Gloss"
    MATTE = "MATTE", "Matte"
    OTHER = "OTHER", "Other"


class PaperCategory(models.TextChoices):
    MATTE = "matt", "Matt"
    GLOSS = "gloss", "Gloss"
    BOND = "bond", "Bond"
    IVORY = "ivory", "Ivory"
    TICTAC = "tictac", "Tictac"
    CONQUEROR = "conqueror", "Conqueror"
    ARTCARD = "artcard", "Art Card"
    COVER_BOARD = "cover_board", "Cover Board"
    KRAFT = "kraft", "Kraft"
    SPECIAL = "special", "Special Paper"
    OTHER = "other", "Other"


# Standard dimensions in mm (width, height) for auto-fill
SHEET_SIZE_DIMENSIONS = {
    SheetSize.A4: (210, 297),
    SheetSize.A3: (297, 420),
    SheetSize.SRA3: (320, 450),
    SheetSize.A2: (420, 594),
    SheetSize.A1: (594, 841),
    SheetSize.A0: (841, 1189),
}
