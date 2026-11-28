"""Admin registrations for canonical managed job models."""
from datetime import timedelta
from decimal import Decimal

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone

from jobs.audit_services import record_job_status_event
from jobs.models import JobAssignment, JobFile, ManagedJob, ManagedJobPayout, JobStatusEvent
from jobs.payment_services import initialize_settlement_for_managed_job
from jobs.payout_services import release_managed_job_payouts
from jobs.timing_services import record_lateness


def _active_assignment(managed_job: ManagedJob) -> JobAssignment | None:
    return (
        managed_job.assignments.filter(reassigned_from__isnull=True)
        .select_related("assigned_shop")
        .first()
    )


@admin.register(ManagedJob)
class ManagedJobAdmin(admin.ModelAdmin):
    list_display = [
        "managed_reference",
        "title",
        "assigned_shop",
        "status",
        "payment_status",
        "assignment_status",
        "payout_hold",
        "created_at",
    ]
    list_filter = ["status", "payment_status", "assignment_status", "payout_hold", "assigned_shop"]
    search_fields = ["managed_reference", "title"]
    actions = ["release_payouts", "flag_late_sla_hold", "clear_payout_hold"]
    # Prompt 7: status and the delivery/handover trail move only through the
    # workflow services (buyer handover confirmation, dispute resolution), so
    # the admin form must not be a side door to ``completed``. The handover
    # code itself is never shown.
    exclude = ["handover_code"]
    readonly_fields = [
        "status",
        "delivered_at",
        "completed_at",
        "handover_code_set_at",
        "handover_code_set_by",
        "handover_failed_attempts",
        "handover_confirmed_at",
        "handover_confirmed_by",
        "completion_method",
    ]

    @admin.action(description="Release manager and printer payouts")
    def release_payouts(self, request, queryset):
        released = 0
        for managed_job in queryset:
            try:
                initialize_settlement_for_managed_job(managed_job=managed_job)
                result = release_managed_job_payouts(managed_job=managed_job, released_by=request.user)
                released += 1
                reconciliation = result.get("reconciliation") or {}
                self.message_user(
                    request,
                    f"{managed_job.managed_reference}: paid out "
                    f"{reconciliation.get('total_paid_out', '0.00')} of "
                    f"{reconciliation.get('client_total', '0.00')} "
                    f"(Printy fee {reconciliation.get('printy_fee', '0.00')}).",
                    level=messages.SUCCESS,
                )
            except (ValidationError, ValueError) as exc:
                self.message_user(
                    request,
                    f"{managed_job.managed_reference}: payout not released — {exc}",
                    level=messages.ERROR,
                )
        if released:
            self.message_user(
                request,
                f"{released} job(s) released. The buyer already saw the completion confirmation.",
                level=messages.INFO,
            )

    @admin.action(description="Flag late SLA and hold 10%% of the printer payout")
    def flag_late_sla_hold(self, request, queryset):
        now = timezone.now()
        for managed_job in queryset:
            assignment = _active_assignment(managed_job)
            if managed_job.due_date is None or managed_job.due_date >= now:
                managed_job.due_date = now - timedelta(minutes=30)
                managed_job.save(update_fields=["due_date", "updated_at"])
            record_lateness(managed_job)
            managed_job.refresh_from_db(fields=["is_late", "lateness_minutes"])

            base = Decimal(str(getattr(assignment, "shop_payout", None) or "0.00"))
            hold = (base * Decimal("0.10")).quantize(Decimal("0.01"))
            snapshot = dict(managed_job.operational_snapshot or {})
            snapshot["lateness_hold"] = {
                "rate": "0.10",
                "base": "shop_payout",
                "base_amount": str(base),
                "amount": str(hold),
                "recorded_at": now.isoformat(),
                "recorded_by": request.user.id,
            }
            managed_job.operational_snapshot = snapshot
            managed_job.payout_hold = True
            managed_job.save(update_fields=["payout_hold", "operational_snapshot", "updated_at"])
            record_job_status_event(
                managed_job=managed_job,
                assignment=assignment,
                event_type="lateness_hold",
                actor=request.user,
                summary="Late SLA: 10% of the printer payout is held for review.",
                metadata={"hold_rate": "0.10", "hold_amount": str(hold), "base": "shop_payout"},
            )
            self.message_user(
                request,
                f"{managed_job.managed_reference}: late by {managed_job.lateness_minutes} min; "
                f"{hold} held (10% of the printer payout).",
                level=messages.WARNING,
            )

    @admin.action(description="Clear the payout hold (after ops review)")
    def clear_payout_hold(self, request, queryset):
        cleared = 0
        for managed_job in queryset.filter(payout_hold=True):
            managed_job.payout_hold = False
            snapshot = dict(managed_job.operational_snapshot or {})
            previous = snapshot.pop("lateness_hold", None)
            if previous:
                snapshot["lateness_hold_cleared"] = {
                    **previous,
                    "cleared_at": timezone.now().isoformat(),
                    "cleared_by": request.user.id,
                }
            managed_job.operational_snapshot = snapshot
            managed_job.save(update_fields=["payout_hold", "operational_snapshot", "updated_at"])
            record_job_status_event(
                managed_job=managed_job,
                event_type="lateness_hold_cleared",
                actor=request.user,
                summary="Ops cleared the lateness payout hold after review.",
                metadata={"previous_hold": previous or {}},
            )
            cleared += 1
        if cleared:
            self.message_user(
                request,
                f"{cleared} payout hold(s) cleared. Payouts can now be released.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(request, "No held payouts in that selection.", level=messages.INFO)


@admin.register(JobAssignment)
class JobAssignmentAdmin(admin.ModelAdmin):
    list_display = ["managed_job", "assigned_shop", "status", "due_at", "created_at"]
    list_filter = ["status", "assigned_shop"]


@admin.register(ManagedJobPayout)
class ManagedJobPayoutAdmin(admin.ModelAdmin):
    list_display = [
        "managed_job",
        "recipient_role",
        "recipient",
        "amount",
        "status",
        "disbursement_mode",
        "transfer_reference",
        "released_at",
    ]
    list_filter = ["recipient_role", "status", "disbursement_mode", "released_at"]
    search_fields = ["managed_job__managed_reference", "recipient__email", "release_reference", "transfer_reference"]
    readonly_fields = ["created_at", "updated_at", "released_at", "failed_at"]
    fieldsets = (
        (None, {"fields": ("managed_job", "recipient_role", "recipient", "amount", "currency", "assignment")}),
        (
            "Disbursement",
            {
                "fields": ("status", "disbursement_mode", "transfer_reference", "released_at", "released_by"),
                "description": (
                    "Printy has no automatic payout provider. 'released' means an admin "
                    "transferred the money manually and recorded the reference above. "
                    "Payouts left in 'pending' or 'failed' have NOT been paid."
                ),
            },
        ),
        ("Failure trail", {"fields": ("failure_reason", "failed_at", "provider_response")}),
        ("Audit", {"fields": ("release_reference", "metadata", "created_at", "updated_at")}),
    )


@admin.register(JobFile)
class JobFileAdmin(admin.ModelAdmin):
    list_display = ["managed_job", "original_filename", "file_type", "visibility", "status", "created_at"]
    list_filter = ["file_type", "visibility", "status"]


@admin.register(JobStatusEvent)
class JobStatusEventAdmin(admin.ModelAdmin):
    list_display = ["managed_job", "event_type", "actor", "created_at"]
    list_filter = ["event_type"]
