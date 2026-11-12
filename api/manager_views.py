"""Public manager recommendations and buyer manager-lock endpoints.

The public endpoint prices the job through the existing calculator and then
asks the recommendation service who can actually meet each speed tier. The
lock endpoints only persist a shortlist; they never reserve capacity or move
money.
"""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ManagerLock, User, UserProfile
from accounts.services.manager_recommendation import (
    TERMINAL_JOB_STATUSES,
    locked_manager_payloads,
    recommend_managers,
)
from jobs.models import ManagedJob

from .permissions import IsBuyerActor
from .workflow_serializers import CalculatorConfigPreviewSerializer

MAX_LOCKED_MANAGERS = 3


def _cache_key(payload: dict, *, host: str = "") -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"public-recommended-managers:{host}:{digest}"


def _lock_error(code: str, detail: str, *, http_status=status.HTTP_409_CONFLICT) -> Response:
    return Response({"code": code, "detail": detail}, status=http_status)


def _profile_for_public_id(public_id):
    return get_object_or_404(
        UserProfile.objects.select_related("user"),
        public_id=public_id,
    )


def _manager_lock_block_reason(profile: UserProfile) -> tuple[str, str] | None:
    """Return a stable error code/detail when this manager cannot be locked."""
    manager = profile.user
    if profile.is_system_account or not getattr(manager, "is_active", False):
        return "manager_unavailable", "This manager is not accepting new jobs."
    if not (manager.is_broker_role or getattr(manager, "partner_profile_enabled", False)):
        return "manager_unavailable", "This profile is not a manager account."
    if not profile.manager_verified:
        return "manager_not_verified", "This manager has not been verified yet."
    if not profile.manager_public_opt_in:
        return "manager_unavailable", "This manager is not accepting new jobs."
    if not profile.accepts_new_jobs:
        return "manager_unavailable", "This manager has paused new jobs."
    active_jobs = (
        ManagedJob.objects.filter(broker_id=manager.id)
        .exclude(status__in=TERMINAL_JOB_STATUSES)
        .count()
    )
    if int(profile.max_concurrent_jobs or 0) <= active_jobs:
        return "manager_at_capacity", "This manager is at capacity right now."
    return None


class PublicRecommendedManagersView(APIView):
    """POST /api/public/recommended-managers/ - all three speed tiers."""

    permission_classes = [AllowAny]
    throttle_scope = "recommended_managers"

    def post(self, request):
        serializer = CalculatorConfigPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = dict(serializer.validated_data)

        user = getattr(request, "user", None)
        authenticated = bool(user is not None and getattr(user, "is_authenticated", False))
        cache_key = _cache_key(payload, host=request.get_host())
        timeout = int(getattr(settings, "PRINTY_RECOMMENDED_MANAGERS_CACHE_SECONDS", 20))

        if not authenticated:
            cached = cache.get(cache_key)
            if cached is not None:
                response = Response(cached)
                response["Cache-Control"] = f"public, max-age={max(timeout, 0)}"
                return response

        data = recommend_managers(
            payload=payload,
            buyer=user if authenticated else None,
            request=request,
        )

        if authenticated:
            response = Response(data)
            response["Cache-Control"] = "no-store"
            return response

        cache.set(cache_key, data, timeout=timeout)
        response = Response(data)
        response["Cache-Control"] = f"public, max-age={max(timeout, 0)}"
        return response


class BuyerLockedManagerListView(APIView):
    """GET /api/buyer/locked-managers/ - the buyer's shortlist."""

    permission_classes = [IsAuthenticated, IsBuyerActor]

    def get(self, request):
        rows = locked_manager_payloads(buyer=request.user, request=request)
        return Response(
            {
                "results": rows,
                "count": len(rows),
                "cap": MAX_LOCKED_MANAGERS,
            }
        )


class BuyerLockedManagerDetailView(APIView):
    """PUT/DELETE /api/buyer/locked-managers/<public_id>/."""

    permission_classes = [IsAuthenticated, IsBuyerActor]

    def put(self, request, public_id):
        profile = _profile_for_public_id(public_id)
        manager = profile.user

        with transaction.atomic():
            # Serialize lock creation per buyer. Locking the buyer row (not just
            # the existing lock rows) closes the phantom-insert race where two
            # concurrent requests could both read count=2 and create a fourth.
            User.objects.select_for_update().get(pk=request.user.pk)
            existing = (
                ManagerLock.objects.select_for_update()
                .filter(buyer=request.user, manager=manager)
                .first()
            )
            if existing is not None:
                count = ManagerLock.objects.filter(buyer=request.user).count()
                return Response(
                    {
                        "locked": True,
                        "created": False,
                        "public_id": str(profile.public_id),
                        "display_name": profile.public_display_name
                        or getattr(manager, "name", ""),
                        "count": count,
                        "cap": MAX_LOCKED_MANAGERS,
                    }
                )

            count = (
                ManagerLock.objects.select_for_update()
                .filter(buyer=request.user)
                .count()
            )
            if count >= MAX_LOCKED_MANAGERS:
                return _lock_error(
                    "lock_limit_reached",
                    f"You can lock at most {MAX_LOCKED_MANAGERS} managers.",
                )

            block = _manager_lock_block_reason(profile)
            if block is not None:
                code, detail = block
                return _lock_error(code, detail)

            ManagerLock.objects.create(buyer=request.user, manager=manager)
            count += 1

        return Response(
            {
                "locked": True,
                "created": True,
                "public_id": str(profile.public_id),
                "display_name": profile.public_display_name or getattr(manager, "name", ""),
                "count": count,
                "cap": MAX_LOCKED_MANAGERS,
            },
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request, public_id):
        profile = _profile_for_public_id(public_id)
        ManagerLock.objects.filter(buyer=request.user, manager=profile.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
