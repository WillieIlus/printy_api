"""Compatibility helpers for postponed managed-job payment models."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from jobs.choices import ManagedJobPaymentStatus
from jobs.models import ManagedJob

MPESA_DISABLED_MESSAGE = "Managed-job payment records are postponed for MVP; use canonical Payment records."


def _rate_to_percent(rate: Decimal) -> Decimal:
    return (rate * Decimal("100")).quantize(Decimal("0.01"))


def get_default_printer_side_fee_rate() -> Decimal:
    return Decimal("0.00")


def get_default_printer_side_fee_percent() -> Decimal:
    return _rate_to_percent(get_default_printer_side_fee_rate())


def get_default_partner_markup_rate(*, partner_user=None, partner_profile=None) -> Decimal:
    return Decimal("0.75")


def get_default_partner_markup_percent(*, partner_user=None, partner_profile=None) -> Decimal:
    return _rate_to_percent(
        get_default_partner_markup_rate(partner_user=partner_user, partner_profile=partner_profile)
    )


def calculate_settlement_split(*, managed_job: ManagedJob, payment_method: str | None = None) -> dict[str, Any]:
    return {
        "managed_job_id": managed_job.id,
        "payment_method": payment_method or "",
        "status": "postponed",
        "detail": MPESA_DISABLED_MESSAGE,
    }


def initialize_settlement_for_managed_job(
    *, managed_job: ManagedJob, payment_method: str | None = None, **kwargs
) -> dict[str, Any]:
    return calculate_settlement_split(managed_job=managed_job, payment_method=payment_method)


def create_job_payment(*, managed_job: ManagedJob, **kwargs) -> dict[str, Any]:
    return {
        "managed_job_id": managed_job.id,
        "status": "postponed",
        "detail": MPESA_DISABLED_MESSAGE,
        **kwargs,
    }


def mark_payment_confirmed(*, job_payment, confirmed_by=None, payload: dict[str, Any] | None = None):
    managed_job = job_payment.get("managed_job") if isinstance(job_payment, dict) else None
    if isinstance(managed_job, ManagedJob):
        managed_job.payment_status = ManagedJobPaymentStatus.CONFIRMED
        managed_job.save(update_fields=["payment_status", "updated_at"])
    if isinstance(job_payment, dict):
        job_payment["status"] = "confirmed"
        job_payment["confirmation_payload"] = payload or {}
    return job_payment


def mark_settlement_release_ready(*, settlement):
    if isinstance(settlement, dict):
        settlement["status"] = "release_ready"
    return settlement


def initiate_job_stk_push(*args, **kwargs) -> dict[str, Any]:
    return {"status": "disabled", "detail": MPESA_DISABLED_MESSAGE}


def reconcile_job_payment_status(*, job_payment) -> dict[str, Any]:
    return {"status": "disabled", "detail": MPESA_DISABLED_MESSAGE}


def handle_job_mpesa_callback(payload: dict[str, Any]) -> dict[str, str]:
    return {"status": "ignored", "detail": MPESA_DISABLED_MESSAGE}
