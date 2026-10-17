"""Internal, role-gated production analytics endpoint (law 1.3 / 10.3).

Sensitive supplier/shop costs and margin summaries are only reachable by
authorized manager (partner) and admin accounts. Buyers, shops, and anonymous
callers are refused, and a manager only ever sees aggregates over their own
jobs/quotes.
"""

from __future__ import annotations

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.roles import (
    CANONICAL_PARTNER_ROLE,
    CANONICAL_SUPER_ADMIN_ROLE,
    has_role,
)
from jobs.analytics import summarize
from jobs.models import ManagedJob
from quotes.models import QuoteVersion


class ProductionAnalyticsView(APIView):
    """Internal estimate-vs-actual / turnaround / reprint / acceptance summary."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if has_role(user, CANONICAL_SUPER_ADMIN_ROLE):
            scope = "admin"
            managed_job_ids = None
            quote_versions = QuoteVersion.objects.all()
        elif has_role(user, CANONICAL_PARTNER_ROLE):
            scope = "manager"
            managed_job_ids = list(
                ManagedJob.objects.filter(
                    Q(broker=user) | Q(created_by=user) | Q(client=user)
                )
                .values_list("id", flat=True)
                .distinct()
            )
            quote_versions = QuoteVersion.objects.filter(
                Q(quote_request__created_by=user)
                | Q(quote_request__assigned_manager=user)
                | Q(quote_request__managed_jobs__broker=user)
            ).distinct()
        else:
            raise PermissionDenied(
                detail={
                    "detail": "Internal production analytics is restricted to "
                    "authorized manager and admin accounts."
                }
            )

        metrics = summarize(
            managed_job_ids=managed_job_ids,
            quote_versions=quote_versions,
        )
        return Response({"scope": scope, "metrics": metrics})
