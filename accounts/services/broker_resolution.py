from __future__ import annotations

from accounts.models import User, UserProfile
from accounts.services.system_accounts import ensure_house_broker_user
from jobs.models import ManagedJob


def is_broker_profile_active(user: User | None) -> bool:
    if user is None:
        return False
    try:
        return bool(user.profile.broker_profile_active)
    except UserProfile.DoesNotExist:
        return True


def resolve_effective_broker(client) -> int | None:
    latest_brokered_job = (
        ManagedJob.objects.filter(client=client, broker_id__isnull=False)
        .select_related("broker", "broker__profile")
        .order_by("-created_at", "-id")
        .first()
    )
    if latest_brokered_job is None:
        return None

    broker = latest_brokered_job.broker
    if is_broker_profile_active(broker):
        return broker.id

    house_broker, _profile, _created = ensure_house_broker_user()
    return house_broker.id
