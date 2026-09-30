"""Manual payout release services for managed jobs."""

from __future__ import annotations

from decimal import Decimal

from common.money import money
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from jobs.audit_services import EVENT_PAYOUT_RELEASED, record_job_status_event
from jobs.choices import ManagedJobPaymentStatus, ManagedJobStatus
from jobs.models import ManagedJob, ManagedJobPayout
from payments.models import Payment


RELEASE_ALLOWED_JOB_STATUSES = {ManagedJobStatus.READY, ManagedJobStatus.COMPLETED}
PAYMENT_CONFIRMED_STATUSES = {
    ManagedJobPaymentStatus.CONFIRMED,
    ManagedJobPaymentStatus.RELEASE_READY,
    ManagedJobPaymentStatus.RELEASED,
}


def _money(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return money(value)


def _has_confirmed_payment(managed_job: ManagedJob) -> bool:
    if str(managed_job.payment_status) in PAYMENT_CONFIRMED_STATUSES:
        return True
    if managed_job.canonical_payments.filter(status=Payment.STATUS_PAID).exists():
        return True
    source_quote = getattr(managed_job, "source_quote", None)
    return bool(source_quote and source_quote.payments.filter(status=Payment.STATUS_PAID).exists())


def _release_reference(managed_job: ManagedJob, role: str) -> str:
    return f"MANUAL-{managed_job.managed_reference or managed_job.id}-{role}"[:100]


def _promised_split(managed_job: ManagedJob) -> dict | None:
    """The authoritative QuoteFinancialSplit values promised for this job."""
    from jobs.settlement_compat import get_financial_split_for_job

    return get_financial_split_for_job(managed_job)


def _promised_manager_payout(managed_job: ManagedJob) -> Decimal:
    """The manager share the authoritative QuoteFinancialSplit promised.

    ManagedJob.broker_payout is populated from this same split at job creation
    and is the value actually paid. This is only consulted to detect the
    failure mode where a promised manager payout never made it onto the job, so
    that it is refused loudly instead of being silently skipped.
    """
    split = _promised_split(managed_job) or {}
    return _money(split.get("broker_payout"))


def _release_payout(*, managed_job: ManagedJob, released_by, role: str, recipient, amount: Decimal, assignment=None, released_at, transfer_reference: str = ""):
    payout, created = ManagedJobPayout.objects.get_or_create(
        managed_job=managed_job,
        recipient_role=role,
        defaults={
            "assignment": assignment,
            "recipient": recipient,
            "amount": amount,
            "status": ManagedJobPayout.STATUS_RELEASED,
            "released_at": released_at,
            "released_by": released_by,
            "release_reference": _release_reference(managed_job, role),
            "disbursement_mode": ManagedJobPayout.DISBURSEMENT_MANUAL,
            "transfer_reference": transfer_reference or _release_reference(managed_job, role),
            "metadata": {"release_mode": "manual"},
        },
    )
    changed = created
    if not created and payout.status != ManagedJobPayout.STATUS_RELEASED:
        payout.assignment = assignment or payout.assignment
        payout.recipient = recipient or payout.recipient
        payout.amount = amount
        payout.status = ManagedJobPayout.STATUS_RELEASED
        payout.released_at = payout.released_at or released_at
        payout.released_by = payout.released_by or released_by
        payout.release_reference = payout.release_reference or _release_reference(managed_job, role)
        payout.disbursement_mode = ManagedJobPayout.DISBURSEMENT_MANUAL
        payout.transfer_reference = transfer_reference or payout.transfer_reference or payout.release_reference
        # A payout that previously failed is being retried, so clear the
        # failure trail and persist who retried it.
        payout.failure_reason = ""
        payout.failed_at = None
        metadata = dict(payout.metadata or {})
        metadata.setdefault("release_mode", "manual")
        metadata["retried_at"] = released_at.isoformat()
        metadata["retried_by"] = getattr(released_by, "id", None)
        payout.metadata = metadata
        payout.save(update_fields=[
            "assignment",
            "recipient",
            "amount",
            "status",
            "released_at",
            "released_by",
            "release_reference",
            "disbursement_mode",
            "transfer_reference",
            "failure_reason",
            "failed_at",
            "metadata",
            "updated_at",
        ])
        changed = True
    return payout, changed


def record_payout_failure(
    *,
    managed_job: ManagedJob,
    role: str,
    reason: str,
    recipient=None,
    amount: Decimal | None = None,
    assignment=None,
    provider_response: dict | None = None,
) -> ManagedJobPayout:
    """Persist a failed payout without ever marking it as successfully paid.

    The row is kept (never deleted) so the failure is auditable, and it stays
    retryable: a later release call re-uses this same row via get_or_create, so
    a retry can never produce a duplicate transfer.
    """
    payout, _ = ManagedJobPayout.objects.get_or_create(
        managed_job=managed_job,
        recipient_role=role,
        defaults={
            "assignment": assignment,
            "recipient": recipient,
            "amount": amount if amount is not None else Decimal("0.00"),
            "status": ManagedJobPayout.STATUS_FAILED,
            "failure_reason": reason,
            "provider_response": provider_response or {},
            "failed_at": timezone.now(),
            "disbursement_mode": ManagedJobPayout.DISBURSEMENT_MANUAL,
        },
    )
    if payout.status != ManagedJobPayout.STATUS_FAILED:
        payout.status = ManagedJobPayout.STATUS_FAILED
    payout.recipient = recipient or payout.recipient
    payout.assignment = assignment or payout.assignment
    if amount is not None:
        payout.amount = amount
    payout.failure_reason = reason
    payout.provider_response = provider_response or payout.provider_response or {}
    payout.failed_at = payout.failed_at or timezone.now()
    payout.save(update_fields=[
        "assignment",
        "recipient",
        "amount",
        "status",
        "failure_reason",
        "provider_response",
        "failed_at",
        "updated_at",
    ])
    return payout


@transaction.atomic
def release_managed_job_payouts(*, managed_job: ManagedJob, released_by) -> dict:
    managed_job = (
        ManagedJob.objects.select_for_update(of=("self",))
        .select_related("broker", "assigned_shop", "assigned_shop__owner", "source_quote")
        .get(pk=managed_job.pk)
    )
    if managed_job.payout_hold:
        raise ValidationError("Payout is on hold for this job.")
    if str(managed_job.status) not in RELEASE_ALLOWED_JOB_STATUSES:
        raise ValidationError("Payout can only be released after the job is ready or completed.")
    if not _has_confirmed_payment(managed_job):
        raise ValidationError("Client payment must be confirmed before payout release.")

    active_assignment = (
        managed_job.assignments.select_related("assigned_shop", "assigned_shop__owner")
        .filter(reassigned_from__isnull=True)
        .first()
    )
    released_at = timezone.now()
    released = []
    changed_any = False

    manager_amount = _money(managed_job.broker_payout)
    promised_manager_amount = _promised_manager_payout(managed_job)
    if managed_job.broker_id and promised_manager_amount > 0 and manager_amount <= 0:
        # The split promised this manager money but the job never received the
        # value. Releasing only the shop would silently swallow the manager's
        # share, so refuse the whole release and surface the discrepancy.
        raise ValidationError(
            "Manager payout is owed but the job carries no broker_payout. "
            f"Authoritative split promises {promised_manager_amount}. "
            "Re-derive the job from its QuoteFinancialSplit before releasing."
        )
    if manager_amount > 0 and managed_job.broker_id:
        payout, changed = _release_payout(
            managed_job=managed_job,
            released_by=released_by,
            role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER,
            recipient=managed_job.broker,
            amount=manager_amount,
            released_at=released_at,
            transfer_reference=f"MANUAL-MANAGER-{managed_job.managed_reference or managed_job.id}",
        )
        released.append(payout)
        changed_any = changed_any or changed

    shop_amount = _money(getattr(active_assignment, "shop_payout", None))
    shop_owner = getattr(getattr(active_assignment, "assigned_shop", None), "owner", None)
    if shop_amount > 0 and shop_owner is not None:
        payout, changed = _release_payout(
            managed_job=managed_job,
            released_by=released_by,
            role=ManagedJobPayout.RECIPIENT_ROLE_SHOP,
            recipient=shop_owner,
            amount=shop_amount,
            assignment=active_assignment,
            released_at=released_at,
            transfer_reference=f"MANUAL-SHOP-{active_assignment.id or managed_job.id}",
        )
        released.append(payout)
        changed_any = changed_any or changed

    if not released:
        raise ValidationError("No payout records could be released for this job.")

    # Reconciliation: the money that leaves Printy (payouts) plus the money
    # Printy keeps (printy_fee) must equal what the client paid. Printy's fee
    # is never a manager payout, so it is reported separately.
    split = _promised_split(managed_job)
    printy_fee = _money(split.get("printy_fee") if split else managed_job.printy_fee)
    client_total = _money(managed_job.client_total)
    paid_by_role = {payout.recipient_role: _money(payout.amount) for payout in released}
    total_paid = sum(paid_by_role.values(), Decimal("0.00"))
    reconciliation = {
        "manager_payout": str(paid_by_role.get(ManagedJobPayout.RECIPIENT_ROLE_MANAGER, Decimal("0.00"))),
        "shop_payout": str(paid_by_role.get(ManagedJobPayout.RECIPIENT_ROLE_SHOP, Decimal("0.00"))),
        "printy_fee": str(printy_fee),
        "total_paid_out": str(total_paid),
        "client_total": str(client_total),
        "balances": str(client_total - total_paid - printy_fee),
        "reconciled": (client_total - total_paid - printy_fee) == Decimal("0.00"),
    }

    if managed_job.payment_status != ManagedJobPaymentStatus.RELEASED:
        managed_job.payment_status = ManagedJobPaymentStatus.RELEASED
        managed_job.save(update_fields=["payment_status", "updated_at"])

    if changed_any:
        record_job_status_event(
            managed_job=managed_job,
            event_type=EVENT_PAYOUT_RELEASED,
            actor=released_by,
            summary="Manual payout released.",
            metadata={
                "payout_ids": [payout.id for payout in released],
                "recipient_roles": [payout.recipient_role for payout in released],
                "release_mode": "manual",
                "reconciliation": reconciliation,
            },
        )

    return {
        "managed_job": managed_job,
        "payouts": released,
        "created_or_updated": changed_any,
        "reconciliation": reconciliation,
    }
