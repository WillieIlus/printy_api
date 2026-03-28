"""JobShare choices."""
from django.db import models
from django.utils.translation import gettext_lazy as _


class JobMachineType(models.TextChoices):
    DIGITAL = "DIGITAL", _("Digital")
    LARGE_FORMAT = "LARGE_FORMAT", _("Large Format")
    UV = "UV", _("UV")
    OFFSET = "OFFSET", _("Offset")
    OTHER = "OTHER", _("Other")


class JobRequestStatus(models.TextChoices):
    OPEN = "OPEN", _("Open")
    CLAIMED = "CLAIMED", _("Claimed")
    CLOSED = "CLOSED", _("Closed")


class JobClaimStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    ACCEPTED = "ACCEPTED", _("Accepted")
    REJECTED = "REJECTED", _("Rejected")
