"""JobShare choices."""
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.payment_constants import PaymentStatus


class JobMachineType(models.TextChoices):
    DIGITAL = "DIGITAL", _("Digital")
    LARGE_FORMAT = "LARGE_FORMAT", _("Large Format")
    UV = "UV", _("UV")
    OFFSET = "OFFSET", _("Offset")
    OTHER = "OTHER", _("Other")


class ManagedJobStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    QUOTED = "quoted", _("Quoted")
    AWAITING_PAYMENT = "awaiting_payment", _("Awaiting payment")
    PAYMENT_CONFIRMED = "payment_confirmed", _("Payment confirmed")
    ASSIGNED = "assigned", _("Assigned")
    IN_PRODUCTION = "in_production", _("In production")
    FINISHING = "finishing", _("Finishing")
    READY = "ready", _("Ready")
    DELIVERED = "delivered", _("Delivered")
    COMPLETED = "completed", _("Completed")
    DISPUTED = "disputed", _("Disputed")
    CANCELLED = "cancelled", _("Cancelled")


class ManagedJobPaymentStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    CONFIRMATION_PENDING = "confirmation_pending", _("Confirmation pending")
    CONFIRMED = "confirmed", _("Confirmed")
    RELEASE_READY = "release_ready", _("Release ready")
    PAYOUT_ON_HOLD = "payout_on_hold", _("Payout on hold")
    RELEASED = "released", _("Released")
    REFUNDED = "refunded", _("Refunded")


class ManagedJobAssignmentStatus(models.TextChoices):
    UNASSIGNED = "unassigned", _("Unassigned")
    ASSIGNMENT_PENDING = "assignment_pending", _("Assignment pending")
    ASSIGNED = "assigned", _("Assigned")
    REASSIGNMENT_REQUIRED = "reassignment_required", _("Reassignment required")
    OVERFLOW_REVIEW = "overflow_review", _("Overflow review")


class ManagedJobExceptionStatus(models.TextChoices):
    CLEAR = "clear", _("Clear")
    PRODUCTION_ISSUE = "production_issue", _("Production issue")
    DELIVERY_ISSUE = "delivery_issue", _("Delivery issue")
    DISPUTE_OPEN = "dispute_open", _("Dispute open")
    OPS_REVIEW = "ops_review", _("Ops review")


class ManagedJobFulfillmentMode(models.TextChoices):
    PRINTY_RIDER = "printy_rider", _("Printy rider")
    OWN_RIDER = "own_rider", _("Own rider")
    PICKUP = "pickup", _("Pickup")


class ManagedJobUrgencyType(models.TextChoices):
    STANDARD = "standard", _("Standard")
    SAME_DAY = "same_day", _("Same-day")
    EXPRESS = "express", _("Express")
    AFTER_HOURS = "after_hours", _("After-hours")
    EMERGENCY = "emergency", _("Emergency")


class ManagedJobTopologyType(models.TextChoices):
    CLIENT_PARTNER = "client_partner", _("Client to partner")
    CLIENT_PRINTY_SUPPORT = "client_printy_support", _("Client to Printy support")
    PARTNER_SHOP = "partner_shop", _("Partner to shop")
    SHOP_OPS = "shop_ops", _("Shop to ops")
    OPS_INTERNAL = "ops_internal", _("Ops internal")


class JobAssignmentStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")
    IN_PRODUCTION = "in_production", _("In production")
    FINISHING = "finishing", _("Finishing")
    READY = "ready", _("Ready")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")
    REASSIGNED = "reassigned", _("Reassigned")


class JobFileType(models.TextChoices):
    ARTWORK = "artwork", _("Artwork")
    CUSTOMER_UPLOAD = "customer_upload", _("Customer upload")
    BROKER_REVISION = "broker_revision", _("Broker revision")
    PROOF = "proof", _("Proof")
    PRINT_READY = "print_ready", _("Print ready")
    DELIVERY_EVIDENCE = "delivery_evidence", _("Delivery evidence")


class JobFileVisibility(models.TextChoices):
    CLIENT = "client", _("Client")
    PARTNER = "partner", _("Partner")
    SHOP = "shop", _("Shop")
    OPS = "ops", _("Ops")
    INTERNAL = "internal", _("Internal")


class JobFileStatus(models.TextChoices):
    UPLOADED = "uploaded", _("Uploaded")
    MANAGER_REVIEW = "manager_review", _("Manager review")
    MANAGER_APPROVED = "manager_approved", _("Manager approved")
    MANAGER_REJECTED = "manager_rejected", _("Manager rejected")
    PROOF_UPLOADED = "proof_uploaded", _("Proof uploaded")
    PROOF_APPROVED = "proof_approved", _("Proof approved")
    PROOF_REJECTED = "proof_rejected", _("Proof rejected")
    REVISION_REQUESTED = "revision_requested", _("Revision requested")
    PRINT_READY = "print_ready", _("Print ready")
    UNDER_REVIEW = "under_review", _("Under review")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    REPLACED = "replaced", _("Replaced")


class JobPaymentMethod(models.TextChoices):
    MPESA = "mpesa", _("M-Pesa")
    CARD = "card", _("Card")
    CASH = "cash", _("Cash")
    MANUAL = "manual", _("Manual")


class JobPaymentStatus(models.TextChoices):
    INITIATED = PaymentStatus.INITIATED
    PENDING = PaymentStatus.PENDING
    PAID = PaymentStatus.PAID
    FAILED = PaymentStatus.FAILED
    CANCELLED = PaymentStatus.CANCELLED
    NEEDS_REVIEW = PaymentStatus.NEEDS_REVIEW


LEGACY_JOB_PAYMENT_STATUS_ALIASES = {
    "stk_push_sent": JobPaymentStatus.INITIATED,
    "manual_payment_pending": JobPaymentStatus.PENDING,
    "confirmation_pending": JobPaymentStatus.PENDING,
    "confirmed": JobPaymentStatus.PAID,
    "refunded": JobPaymentStatus.CANCELLED,
}


class JobPaymentChannel(models.TextChoices):
    STK_PUSH = "stk_push", _("STK push")
    PAYBILL_MANUAL = "paybill_manual", _("Paybill manual")
    QR = "qr", _("QR")
    CASH = "cash", _("Cash")
    MANUAL = "manual", _("Manual")


class JobPaymentReconciliationStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    CALLBACK_RECEIVED = "callback_received", _("Callback received")
    CONFIRMED = "confirmed", _("Confirmed")
    AMOUNT_MISMATCH = "amount_mismatch", _("Amount mismatch")
    UNKNOWN_REFERENCE = "unknown_reference", _("Unknown reference")
    DUPLICATE_CALLBACK = "duplicate_callback", _("Duplicate callback")
    DUPLICATE_RECEIPT = "duplicate_receipt", _("Duplicate receipt")
    FAILED = "failed", _("Failed")
    MANUAL_REVIEW = "manual_review", _("Manual review")


class JobSettlementStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    HELD = "held", _("Held")
    RELEASE_READY = "release_ready", _("Release ready")
    RELEASED = "released", _("Released")
    CANCELLED = "cancelled", _("Cancelled")
    REFUNDED = "refunded", _("Refunded")
