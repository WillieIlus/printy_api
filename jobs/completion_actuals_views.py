"""Manager/admin endpoint to record real completion actuals on a job (law 3.4).

This is the capture path behind :func:`jobs.completion_actuals.record_completion_actuals`.
It is restricted to authorized manager/admin accounts (a manager only on their
own jobs); buyers and shops can never reach it, so internal costs stay internal.
Every figure is optional: omitted values are stored as ``null`` (unknown), never
coerced to zero.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.roles import (
    CANONICAL_PARTNER_ROLE,
    CANONICAL_SUPER_ADMIN_ROLE,
    has_role,
)
from jobs.completion_actuals import record_completion_actuals
from jobs.models import ManagedJob


class CompletionActualsSerializer(serializers.Serializer):
    actual_production_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    actual_client_total = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    material_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    material_quantity = serializers.DecimalField(
        max_digits=12, decimal_places=3, required=False, allow_null=True
    )
    material_unit = serializers.CharField(
        max_length=32, required=False, allow_blank=True, default=""
    )
    finishing_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    delivery_cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    quantity_produced = serializers.IntegerField(required=False, allow_null=True)
    quantity_delivered = serializers.IntegerField(required=False, allow_null=True)
    billable_sheets = serializers.IntegerField(required=False, allow_null=True)
    spoilage_sheets = serializers.IntegerField(required=False, allow_null=True)
    reprints = serializers.IntegerField(required=False, allow_null=True)
    turnaround_hours = serializers.IntegerField(required=False, allow_null=True)
    stage_durations = serializers.JSONField(required=False, allow_null=True)
    delivered_at = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class JobCompletionActualsView(APIView):
    """POST real completion actuals for one managed job."""

    permission_classes = [IsAuthenticated]

    def _jobs_for(self, user):
        if has_role(user, CANONICAL_SUPER_ADMIN_ROLE):
            return ManagedJob.objects.all()
        if has_role(user, CANONICAL_PARTNER_ROLE):
            return ManagedJob.objects.filter(
                Q(broker=user) | Q(created_by=user) | Q(client=user)
            ).distinct()
        raise PermissionDenied(
            detail={
                "detail": "Recording completion actuals is restricted to "
                "authorized manager and admin accounts."
            }
        )

    def post(self, request, job_id):
        job = get_object_or_404(self._jobs_for(request.user), pk=job_id)
        serializer = CompletionActualsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            event = record_completion_actuals(
                managed_job=job,
                recorded_by=request.user,
                **serializer.validated_data,
            )
        except DjangoValidationError as exc:
            raise ValidationError(detail=exc.messages)
        return Response(
            {"id": event.id, "metadata": event.metadata},
            status=201,
        )
