"""Read-only role-aware job card plus its one perform-next-action endpoint."""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.roles import (
    ActiveRoleNotHeld,
    get_request_active_role_raw,
    validate_active_role,
)
from jobs.action_resolver import resolve_next_step
from jobs.artwork_confirmation import respond_to_client_artwork_confirmation
from jobs.artwork_workflow import manager_approve_artwork
from jobs.audit_services import record_job_status_event
from jobs.card_projection import build_job_card
from jobs.choices import JobAssignmentStatus
from jobs.delivery_services import (
    confirm_managed_job_completed,
    mark_managed_job_delivered,
)
from jobs.models import ManagedJob
from jobs.printer_workflow import (
    PrinterWorkflowError,
    advance_printer_job,
    build_printer_progress,
)
from jobs.services.dispatch import dispatch_job_to_shop
from shops.models import Shop

ACTIVE_ROLE_TO_VIEWER = {
    "client": "buyer",
    "partner": "manager",
    "production": "printer",
    "super_admin": "admin",
}


def _can_view_card(*, user, job: ManagedJob, viewer_role: str) -> bool:
    if viewer_role == "admin":
        return True
    if viewer_role == "buyer":
        return job.client_id == user.id or job.created_by_id == user.id
    if viewer_role == "manager":
        return job.broker_id == user.id
    if viewer_role == "printer":
        if job.assigned_shop_id and getattr(job.assigned_shop, "owner_id", None) == user.id:
            return True
        return job.assignments.filter(
            reassigned_from__isnull=True,
            assigned_shop__owner=user,
        ).exists()
    return False


class ManagedJobCardView(APIView):
    """The one card envelope: card id + a jobs list (one job today).

    Read-only. The role comes from ``X-Printy-Active-Role`` and every field is
    projected here, not in the client.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            active_role = validate_active_role(
                request.user,
                get_request_active_role_raw(request),
            )
        except ActiveRoleNotHeld as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        viewer_role = ACTIVE_ROLE_TO_VIEWER.get(active_role)
        if viewer_role is None:
            return Response(
                {"detail": "This account role cannot open a job card."},
                status=status.HTTP_403_FORBIDDEN,
            )

        job = get_object_or_404(
            ManagedJob.objects.select_related(
                "client",
                "broker",
                "assigned_shop",
                "source_quote_request",
                "source_quote",
            ).prefetch_related(
                "assignments",
                "job_events",
                "events",
                "canonical_payments",
                "job_files",
            ),
            pk=pk,
        )
        if not _can_view_card(user=request.user, job=job, viewer_role=viewer_role):
            return Response(
                {"detail": "Not authorized."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(build_job_card(job, viewer_role=viewer_role, user=request.user))


PRINTER_ACTION_CODES = {"accept_job", "start_printing", "advance_production", "pass_qc"}


def _recent_action(*, job: ManagedJob, code: str, user) -> bool:
    cutoff = timezone.now() - timedelta(minutes=10)
    return job.events.filter(
        event_type="next_action_performed",
        created_at__gte=cutoff,
        metadata__code=code,
        metadata__actor_id=getattr(user, "id", None),
    ).exists()


def _perform_next_action(*, job: ManagedJob, request, code: str, note: str) -> bool:
    """Call the existing service for the current next action.

    Every branch delegates to the service that already owns the transition.
    Nothing here re-implements a stage rule.
    """
    user = request.user
    assignment = (
        job.assignments.filter(reassigned_from__isnull=True)
        .exclude(
            status__in=[
                JobAssignmentStatus.REJECTED,
                JobAssignmentStatus.CANCELLED,
            ]
        )
        .order_by("-id")
        .first()
    )

    if code in PRINTER_ACTION_CODES:
        if assignment is None:
            raise ValidationError("This job has no active production assignment.")
        progress = build_printer_progress(assignment)
        printer_action = progress.get("next_action") or {}
        stage_key = str(printer_action.get("stage_key") or "")
        if not stage_key:
            raise ValidationError("There is no printer stage to advance.")
        expected = {
            "accept_job": "accept",
            "start_printing": "printing",
            "pass_qc": "quality_check",
        }.get(code)
        if expected and stage_key != expected:
            raise ValidationError("That printer stage is not the current one.")
        if code == "advance_production" and (
            stage_key in {"accept", "quality_check"}
            or stage_key == ""
        ):
            raise ValidationError("That printer stage is not the current one.")
        _assignment, _progress, applied = advance_printer_job(
            assignment=assignment,
            actor=user,
            expected_stage=stage_key,
            request_id=f"card-next:{job.id}:{code}:{stage_key}",
        )
        return bool(applied)

    if code in {"dispatch_job", "reassign_job"}:
        shop_id = request.data.get("shop_id") or job.assigned_shop_id
        if not shop_id:
            source_quote = getattr(job, "source_quote", None)
            shop_id = getattr(source_quote, "shop_id", None)
        if not shop_id:
            raise ValidationError("Choose a production shop first.")
        shop = Shop.objects.filter(pk=shop_id).first()
        if shop is None:
            raise ValidationError("That production shop was not found.")
        dispatch_job_to_shop(
            managed_job=job,
            dispatched_by=user,
            shop=shop,
            notes=note,
        )
        return True

    if code == "confirm_delivery":
        confirm_managed_job_completed(managed_job=job, actor=user, note=note)
        return True

    if code in {"coordinate_delivery", "mark_dispatched"}:
        mark_managed_job_delivered(managed_job=job, actor=user, note=note)
        return True

    if code == "approve_artwork":
        respond_to_client_artwork_confirmation(
            managed_job=job,
            actor=user,
            approved=True,
            note=note,
        )
        return True

    if code == "upload_artwork":
        raise ValidationError(
            "Upload the artwork file first; the card action cannot carry a file."
        )

    if code == "review_artwork":
        manager_approve_artwork(managed_job=job, actor=user, note=note)
        return True

    if code in {"accept_quote", "prepare_quote"}:
        raise ValidationError("Use the quote workflow for this action.")

    raise ValidationError("That action is not supported by the job card.")


class ManagedJobNextActionView(APIView):
    """Perform the one next action currently projected on the job card.

    The caller must hold the active role that owns the action. The code must
    equal the resolver's current next_action code. Replaying a completed call
    returns ``applied: false`` instead of moving the job twice.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            active_role = validate_active_role(
                request.user,
                get_request_active_role_raw(request),
            )
        except ActiveRoleNotHeld as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        viewer_role = ACTIVE_ROLE_TO_VIEWER.get(active_role)
        if viewer_role is None:
            return Response(
                {"detail": "This account role cannot act on a job card."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if viewer_role == "admin":
            return Response(
                {"detail": "Only the current owner can perform a job action.", "code": "not_owner"},
                status=status.HTTP_403_FORBIDDEN,
            )

        job = get_object_or_404(
            ManagedJob.objects.select_related(
                "client",
                "broker",
                "assigned_shop",
                "source_quote_request",
                "source_quote",
            ).prefetch_related(
                "assignments",
                "job_events",
                "events",
                "canonical_payments",
                "job_files",
            ),
            pk=pk,
        )
        if not _can_view_card(user=request.user, job=job, viewer_role=viewer_role):
            return Response(
                {"detail": "Not authorized."},
                status=status.HTTP_403_FORBIDDEN,
            )

        code = str(request.data.get("code") or "").strip()
        note = str(request.data.get("note") or "").strip()
        if not code:
            return Response(
                {"detail": "A next_action code is required.", "code": "code_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        resolver_role = "manager" if viewer_role == "admin" else viewer_role
        step = resolve_next_step(job, resolver_role)
        current_code = str((step.get("next_action") or {}).get("key") or "none")

        if code != current_code:
            if _recent_action(job=job, code=code, user=request.user):
                return Response(
                    {
                        "applied": False,
                        "idempotent": True,
                        "code": code,
                        "card": build_job_card(
                            job,
                            viewer_role=viewer_role,
                            user=request.user,
                        ),
                    }
                )
            return Response(
                {
                    "detail": "That is not the current next action.",
                    "code": "wrong_action",
                    "current_action": current_code,
                },
                status=status.HTTP_409_CONFLICT,
            )

        if current_code == "none" or not step.get("is_mine"):
            return Response(
                {"detail": "You are not the current owner of this action.", "code": "not_owner"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            applied = _perform_next_action(
                job=job,
                request=request,
                code=code,
                note=note,
            )
        except PrinterWorkflowError as exc:
            return Response(
                {"detail": exc.detail, "code": exc.code},
                status=exc.status_code,
            )
        except ValidationError as exc:
            return Response(
                {"detail": "; ".join(exc.messages), "code": "invalid_action"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc), "code": "invalid_action"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if applied:
            record_job_status_event(
                managed_job=job,
                event_type="next_action_performed",
                actor=request.user,
                summary=f"Next action: {code}",
                metadata={
                    "code": code,
                    "note": note,
                    "actor_id": getattr(request.user, "id", None),
                },
            )

        job.refresh_from_db()
        return Response(
            {
                "applied": bool(applied),
                "code": code,
                "card": build_job_card(
                    job,
                    viewer_role=viewer_role,
                    user=request.user,
                ),
            }
        )


__all__ = ["ManagedJobCardView", "ManagedJobNextActionView"]
