"""JobShare API serializers."""
from decimal import Decimal

from django.urls import reverse
from rest_framework import serializers

from api.visibility import (
    CLIENT_ACTOR,
    OPS_ACTOR,
    PARTNER_ACTOR,
    PUBLIC_ACTOR,
    SHOP_ACTOR,
    can_actor_view_shop_name,
    resolve_actor,
)
from jobs.file_services import managed_job_has_artwork
from jobs.models import JobAssignment, JobFile, ManagedJob, JobStatusEvent
from jobs.workflow import project_workflow_state


def canonicalize_job_status(*, payment_status: str, reconciliation_status: str) -> str:
    # TODO(batch-2): remove reference to deleted billing status helper
    return reconciliation_status or payment_status or ""


def canonical_label(status: str) -> str:
    # TODO(batch-2): remove reference to deleted billing status helper
    return str(status or "").replace("_", " ").title()


def _money(value) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except Exception:
        return None


class ManagedJobSerializer(serializers.ModelSerializer):
    workflow_projection = serializers.SerializerMethodField()
    file_count = serializers.SerializerMethodField()
    payment_count = serializers.SerializerMethodField()
    urgency_label = serializers.SerializerMethodField()
    artwork_uploaded = serializers.SerializerMethodField()

    class Meta:
        model = ManagedJob
        fields = [
            "id",
            "managed_reference",
            "title",
            "status",
            "payment_status",
            "assignment_status",
            "exception_status",
            "fulfillment_mode",
            "topology_type",
            "payout_hold",
            "dispute_open",
            "production_issue_flag",
            "delivery_issue_flag",
            "ops_review_required",
            "artwork_required",
            "artwork_uploaded",
            "urgency_type",
            "urgency_label",
            "urgency_fee",
            "after_hours_fee",
            "requested_deadline",
            "requested_delivery_time",
            "operational_priority_level",
            "file_count",
            "payment_count",
            "workflow_projection",
            "accepted_at",
            "payment_confirmed_at",
            "assigned_at",
            "ready_at",
            "delivered_at",
            "completed_at",
            "created_at",
            "updated_at",
        ]

    def get_workflow_projection(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        return project_workflow_state(
            status=obj.status,
            actor=actor,
            payment_status=obj.payment_status,
            assignment_status=obj.assignment_status,
            exception_status=obj.exception_status,
            urgency_type=obj.urgency_type,
            operational_priority_level=obj.operational_priority_level,
        )

    def get_file_count(self, obj):
        return obj.job_files.count()

    def get_payment_count(self, obj):
        return obj.payments.count()

    def get_urgency_label(self, obj):
        return getattr(obj, "get_urgency_type_display", lambda: "")() or ""

    def get_artwork_uploaded(self, obj):
        return managed_job_has_artwork(managed_job=obj)


class ManagedJobPublicTrackingSerializer(serializers.ModelSerializer):
    job_status = serializers.SerializerMethodField()
    estimated_ready = serializers.SerializerMethodField()
    proof_preview_url = serializers.SerializerMethodField()
    partner_name = serializers.SerializerMethodField()
    partner_contact = serializers.SerializerMethodField()

    class Meta:
        model = ManagedJob
        fields = [
            "job_status",
            "estimated_ready",
            "proof_preview_url",
            "partner_name",
            "partner_contact",
        ]

    def get_job_status(self, obj):
        return getattr(obj, "get_status_display", lambda: obj.status)()

    def get_estimated_ready(self, obj):
        estimated_ready = obj.ready_at or getattr(getattr(obj, "source_quote", None), "estimated_ready_at", None)
        return estimated_ready

    def get_proof_preview_url(self, obj):
        proof = obj.job_files.filter(
            file_type="proof",
            status="proof_approved",
        ).exclude(file="").order_by("-created_at", "-id").first()
        if not proof or not proof.file:
            return None
        url = proof.file.url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url

    def get_partner_name(self, obj):
        broker = getattr(obj, "broker", None)
        if broker is None:
            return "Printy support"
        return getattr(broker, "name", "") or getattr(broker, "email", "") or "Partner"

    def get_partner_contact(self, obj):
        broker = getattr(obj, "broker", None)
        if broker is None:
            return ""
        profile = getattr(broker, "profile", None)
        return getattr(profile, "phone", "") or getattr(broker, "email", "")


class JobAssignmentSerializer(serializers.ModelSerializer):
    shop_name = serializers.SerializerMethodField()
    managed_reference = serializers.CharField(source="managed_job.managed_reference", read_only=True)
    managed_job_status = serializers.CharField(source="managed_job.status", read_only=True)
    managed_job_payment_status = serializers.CharField(source="managed_job.payment_status", read_only=True)
    workflow_projection = serializers.SerializerMethodField()
    urgency_label = serializers.CharField(source="get_urgency_type_display", read_only=True)
    production_stage = serializers.SerializerMethodField()
    production_stage_label = serializers.SerializerMethodField()
    production_timeline_steps = serializers.SerializerMethodField()
    current_step = serializers.SerializerMethodField()
    next_allowed_actions = serializers.SerializerMethodField()
    payment_confirmed = serializers.SerializerMethodField()
    payout_amount = serializers.SerializerMethodField()
    payout_status_label = serializers.SerializerMethodField()
    artwork_available = serializers.SerializerMethodField()
    proof_status = serializers.SerializerMethodField()

    class Meta:
        model = JobAssignment
        fields = [
            "id",
            "managed_job",
            "managed_reference",
            "assigned_shop",
            "shop_name",
            "status",
            "urgency_type",
            "urgency_label",
            "operational_priority_level",
            "managed_job_status",
            "managed_job_payment_status",
            "production_stage",
            "production_stage_label",
            "production_timeline_steps",
            "current_step",
            "next_allowed_actions",
            "payment_confirmed",
            "payout_amount",
            "payout_status_label",
            "artwork_available",
            "proof_status",
            "workflow_projection",
            "production_order",
            "due_at",
            "requested_deadline",
            "accepted_at",
            "rejected_at",
            "assignment_notes",
        ]

    def get_shop_name(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if actor in {SHOP_ACTOR, OPS_ACTOR, PARTNER_ACTOR} and can_actor_view_shop_name(actor=actor, topology_mode="managed"):
            return getattr(obj.assigned_shop, "name", "") if obj.assigned_shop_id else ""
        return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if actor in {CLIENT_ACTOR, PUBLIC_ACTOR}:
            data.pop("assigned_shop", None)
            data.pop("shop_name", None)
        return data

    def get_workflow_projection(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        return project_workflow_state(
            status=obj.managed_job.status,
            actor=actor,
            payment_status=obj.managed_job.payment_status,
            assignment_status=obj.managed_job.assignment_status,
            exception_status=obj.managed_job.exception_status,
            urgency_type=obj.urgency_type or obj.managed_job.urgency_type,
            operational_priority_level=obj.operational_priority_level or obj.managed_job.operational_priority_level,
        )

    def _production_stage(self, obj) -> str:
        status = str(obj.status or "").lower()
        return {
            "pending": "dispatch_received",
            "accepted": "accepted",
            "in_production": "printing",
            "finishing": "finishing",
            "ready": "ready",
            "completed": "completed",
        }.get(status, status or "dispatch_received")

    def _requires_finishing(self, obj) -> bool:
        managed_job = obj.managed_job
        snapshot = getattr(getattr(managed_job, "source_quote_request", None), "request_snapshot", None)
        if not isinstance(snapshot, dict):
            return False
        request_snapshot = snapshot.get("request_snapshot") if isinstance(snapshot.get("request_snapshot"), dict) else snapshot
        return bool(
            request_snapshot.get("lamination")
            or request_snapshot.get("lamination_label")
            or request_snapshot.get("binding_type")
            or request_snapshot.get("cover_lamination")
        )

    def get_production_stage(self, obj):
        return self._production_stage(obj)

    def get_production_stage_label(self, obj):
        return self.get_production_stage(obj).replace("_", " ").title()

    def get_current_step(self, obj):
        return self.get_production_stage(obj)

    def get_next_allowed_actions(self, obj):
        status = str(obj.status or "").lower()
        if status == "pending":
            return ["accept", "reject"]
        if status == "accepted":
            return ["mark_printing"]
        if status == "in_production":
            actions = ["upload_proof"]
            if self._requires_finishing(obj):
                actions.append("mark_finishing")
            else:
                actions.append("mark_ready")
            return actions
        if status == "finishing":
            return ["upload_proof", "mark_ready"]
        if status == "ready":
            return ["mark_completed"]
        return []

    def get_payment_confirmed(self, obj):
        return str(obj.managed_job.payment_status or "").lower() in {"confirmed", "paid", "completed", "release_ready", "released"}

    def get_payout_amount(self, obj):
        return _money(obj.shop_payout)

    def get_payout_status_label(self, obj):
        status = str(obj.managed_job.payment_status or "").lower()
        if status == "released":
            return "Payout completed"
        if status == "release_ready":
            return "Payout ready"
        if status == "payout_on_hold":
            return "Payout on hold"
        if status == "confirmed":
            return "Waiting for job completion"
        return "Awaiting payment confirmation"

    def get_artwork_available(self, obj):
        return managed_job_has_artwork(managed_job=obj.managed_job)

    def get_proof_status(self, obj):
        latest_proof = obj.managed_job.job_files.filter(file_type="proof").order_by("-created_at", "-id").first()
        return getattr(latest_proof, "status", "")

    def get_production_timeline_steps(self, obj):
        operational_snapshot = obj.operational_snapshot if isinstance(obj.operational_snapshot, dict) else {}
        stage = self._production_stage(obj)
        requires_finishing = self._requires_finishing(obj)
        steps = [
            {
                "key": "dispatch_received",
                "label": "Dispatch received",
                "state": "completed",
                "completed_at": getattr(obj.managed_job, "dispatched_at", None) or obj.created_at,
            },
            {
                "key": "accepted",
                "label": "Accepted",
                "state": "completed" if stage in {"accepted", "printing", "finishing", "ready", "completed"} else ("current" if stage == "dispatch_received" else "pending"),
                "completed_at": obj.accepted_at,
            },
            {
                "key": "printing",
                "label": "Printing",
                "state": "completed" if stage in {"printing", "finishing", "ready", "completed"} else ("current" if stage == "accepted" else "pending"),
                "completed_at": getattr(obj.managed_job, "production_started_at", None),
            },
        ]
        if requires_finishing:
            steps.append(
                {
                    "key": "finishing",
                    "label": "Finishing",
                    "state": "completed" if stage in {"finishing", "ready", "completed"} else ("current" if stage == "printing" else "pending"),
                    "completed_at": operational_snapshot.get("finishing_started_at"),
                }
            )
        steps.extend(
            [
                {
                    "key": "ready",
                    "label": "Ready",
                    "state": "completed" if stage in {"ready", "completed"} else ("current" if stage == ("finishing" if requires_finishing else "printing") else "pending"),
                    "completed_at": getattr(obj.managed_job, "ready_at", None),
                },
                {
                    "key": "completed",
                    "label": "Complete",
                    "state": "completed" if stage == "completed" else ("current" if stage == "ready" else "pending"),
                    "completed_at": getattr(obj.managed_job, "completed_at", None),
                },
            ]
        )
        return steps


class JobFileSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()
    notes = serializers.SerializerMethodField()

    class Meta:
        model = JobFile
        fields = [
            "id",
            "managed_job",
            "assignment",
            "file_type",
            "visibility",
            "status",
            "version",
            "original_filename",
            "notes",
            "created_at",
            "download_url",
        ]

    def get_download_url(self, obj):
        request = self.context.get("request")
        path = reverse("job-file-download", kwargs={"pk": obj.pk})
        if request:
            return request.build_absolute_uri(path)
        return path

    def get_notes(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if actor == OPS_ACTOR:
            return obj.notes
        if actor in {SHOP_ACTOR, PARTNER_ACTOR} and obj.visibility != "internal":
            return obj.notes
        return ""


class JobStatusEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = JobStatusEvent
        fields = [
            "id",
            "event_type",
            "summary",
            "metadata",
            "actor_name",
            "created_at",
        ]

    def get_actor_name(self, obj):
        if not obj.actor_id:
            return "System"
        return getattr(obj.actor, "name", "") or getattr(obj.actor, "email", "") or "User"


class JobActionSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)


class JobPaymentSerializer(serializers.Serializer):
    """Postponed managed-job payment projection."""

    def to_representation(self, instance):
        if isinstance(instance, dict):
            return instance
        return {"status": "postponed", "detail": "Managed-job payments are postponed for MVP."}


class ManagedJobStkInitiateSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)


class JobPaymentQuerySerializer(serializers.Serializer):
    checkout_request_id = serializers.CharField(max_length=100)


class JobSettlementSplitSerializer(serializers.Serializer):
    """Postponed managed-job settlement projection."""

    def to_representation(self, instance):
        if isinstance(instance, dict):
            return instance
        return {"status": "postponed", "detail": "Managed-job settlements are postponed for MVP."}

