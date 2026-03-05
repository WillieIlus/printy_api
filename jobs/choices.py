"""JobShare choices."""
from django.db import models


class JobMachineType(models.TextChoices):
    DIGITAL = "DIGITAL", "Digital"
    LARGE_FORMAT = "LARGE_FORMAT", "Large Format"
    UV = "UV", "UV"
    OFFSET = "OFFSET", "Offset"
    OTHER = "OTHER", "Other"
