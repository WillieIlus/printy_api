"""Canonical partner/manager visibility rules for ManagedJob.

The partner job list and the partner dispatch endpoint must agree on which jobs
a broker may act on. Keeping both on one rule prevents them from drifting, which
previously let any partner dispatch any other partner's job.
"""

from __future__ import annotations

from django.db.models import Q

from jobs.models import ManagedJob


def partner_managed_job_filter(user) -> Q:
    """Scope ManagedJob rows to those this partner/manager may act on."""
    return (
        Q(broker=user)
        | Q(source_quote_request__created_by=user)
        | Q(source_quote_request__assigned_manager=user)
        | Q(source_quote__created_by=user)
        | Q(created_by=user)
    )


def partner_can_access_managed_job(*, user, managed_job: ManagedJob) -> bool:
    """True when `user` is within scope for `managed_job` as a partner."""
    return (
        ManagedJob.objects.filter(pk=managed_job.pk)
        .filter(partner_managed_job_filter(user))
        .exists()
    )
