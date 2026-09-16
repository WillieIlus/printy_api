"""Choice enums mirroring printy_workflow/data/printy.ts unions verbatim.

The TS source of truth defines:

    type StageKey = "quote" | "artwork" | "approval" | "payment"
      | "production" | "printing" | "finishing" | "qc"
      | "delivery" | "completed";
    type Status = "on-track" | "at-risk" | "overdue" | "disputed" | "completed";
    type Custody = "awaiting" | "held" | "released";
    type Role = "buyer" | "manager" | "printer" | "admin";
    type PressState = "accept" | "ready" | "active" | "hold" | null;

These TextChoices keep the DB values identical to those literals.
"""
from django.db import models
from django.utils.translation import gettext_lazy as _


class WorkflowStage(models.TextChoices):
    QUOTE = "quote", _("Quote")
    ARTWORK = "artwork", _("Artwork")
    APPROVAL = "approval", _("Approval")
    PAYMENT = "payment", _("Payment")
    PRODUCTION = "production", _("Production")
    PRINTING = "printing", _("Printing")
    FINISHING = "finishing", _("Finishing")
    QC = "qc", _("Quality Control")
    DELIVERY = "delivery", _("Delivery")
    COMPLETED = "completed", _("Completed")


class WorkflowStatus(models.TextChoices):
    ON_TRACK = "on-track", _("On track")
    AT_RISK = "at-risk", _("At risk")
    OVERDUE = "overdue", _("Overdue")
    DISPUTED = "disputed", _("Disputed")
    COMPLETED = "completed", _("Completed")


class WorkflowCustody(models.TextChoices):
    AWAITING = "awaiting", _("Awaiting")
    HELD = "held", _("Held")
    RELEASED = "released", _("Released")


class WorkflowRole(models.TextChoices):
    BUYER = "buyer", _("Buyer")
    MANAGER = "manager", _("Print Manager")
    PRINTER = "printer", _("Printer")
    ADMIN = "admin", _("Admin")


class WorkflowPressState(models.TextChoices):
    ACCEPT = "accept", _("Accept")
    READY = "ready", _("Ready")
    ACTIVE = "active", _("Active")
    HOLD = "hold", _("Hold")


class WorkflowAction(models.TextChoices):
    """Transitions exposed by the job viewset (mirrors ActionId, trimmed)."""

    APPROVE = "approve", _("Approve artwork")
    REQUEST_CHANGES = "request-changes", _("Request changes")
    PAY = "pay", _("Pay")
    ASSIGN = "assign", _("Assign printer")
    PRESS_ADVANCE = "press-advance", _("Advance press")
    CONFIRM_DELIVERY = "confirm-delivery", _("Confirm delivery")
    RESOLVE_DISPUTE = "resolve-dispute", _("Resolve dispute")
    NUDGE = "nudge", _("Nudge")
    RESET = "reset", _("Reset demo")