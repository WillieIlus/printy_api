from django.db import models
from django.utils.translation import gettext_lazy as _


class PaymentStatus(models.TextChoices):
    INITIATED = "initiated", _("Initiated")
    PENDING = "pending", _("Pending")
    PAID = "paid", _("Paid")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")
    NEEDS_REVIEW = "needs_review", _("Needs Review")
