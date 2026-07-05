"""Manual payout release services for managed jobs."""

from __future__ import annotations

from decimal import Decimal

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
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _has_confirmed_payment(managed_job: ManagedJob) -> bool:
    if str(managed_job.payment_status) in PAYMENT_CONFIRMED_STATUSES:
        return True
    if managed_job.canonical_payments.filter(status=Payment.STATUS_PAID).exists():
        return True
    source_quote = getattr(managed_job, "source_quote", None)
    return bool(source_quote and source_quote.payments.filter(status=Payment.STATUS_PAID).exists())


def _release_reference(managed_job: ManagedJob, role: str) -> str:
    return f"MANUAL-{managed_job.managed_reference or managed_job.id}-{role}"[:100]


def _release_payout(*, managed_job: ManagedJob, released_by, role: str, recipient, amount: Decimal, assignment=None, released_at):
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
        metadata = dict(payout.metadata or {})
        metadata.setdefault("release_mode", "manual")
        payout.metadata = metadata
        payout.save(update_fields=[
            "assignment",
            "recipient",
            "amount",
            "status",
            "released_at",
            "released_by",
            "release_reference",
            "metadata",
            "updated_at",
        ])
        changed = True
    return payout, changed


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
    if manager_amount > 0 and managed_job.broker_id:
        payout, changed = _release_payout(
            managed_job=managed_job,
            released_by=released_by,
            role=ManagedJobPayout.RECIPIENT_ROLE_MANAGER,
            recipient=managed_job.broker,
            amount=manager_amount,
            released_at=released_at,
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
        )
        released.append(payout)
        changed_any = changed_any or changed

    if not released:
        raise ValidationError("No payout records could be released for this job.")

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
            },
        )

    return {
        "managed_job": managed_job,
        "payouts": released,
        "created_or_updated": changed_any,
    }
