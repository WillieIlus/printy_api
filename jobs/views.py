"""JobShare API views."""
from django.conf import settings
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils.translation import gettext as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.visibility import CLIENT_ACTOR, OPS_ACTOR, PARTNER_ACTOR, SHOP_ACTOR, resolve_actor
from api.services.actor_serializer import select_actor_serializer
from jobs.assignment_services import (
    accept_assignment,
    mark_assignment_finishing,
    mark_assignment_completed,
    mark_assignment_in_production,
    mark_assignment_ready,
    reject_assignment,
    report_assignment_issue,
)
from jobs.file_services import (
    approve_job_proof,
    get_visible_job_files_for_actor,
    manager_approve_job_proof,
    manager_reject_job_proof,
    mark_file_print_ready,
    reject_job_proof,
    request_revision,
    sync_managed_job_artwork_requirement,
    upload_artwork_for_managed_job,
    upload_proof_for_managed_job,
)
from jobs.choices import JobFileStatus, JobFileType
from jobs.models import JobAssignment, JobFile, ManagedJob
from jobs.payment_services import (
    initialize_settlement_for_managed_job,
)
from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent
from quotes.services_workflow import save_calculator_draft
from jobs.serializers import (
    JobActionSerializer,
    JobAssignmentSerializer,
    JobFileSerializer,
    JobStatusEventSerializer,
    ManagedJobPublicTrackingSerializer,
    ManagedJobSerializer,
    JobPaymentSerializer,
    JobSettlementSplitSerializer,
)


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _normalize_reorder_finishing_list(*, request_snapshot: dict, quote_item=None) -> list[str]:
    values: list[str] = []
    raw_finishings = request_snapshot.get("finishings")
    if isinstance(raw_finishings, list):
        for entry in raw_finishings:
            if isinstance(entry, dict):
                label = (
                    entry.get("label")
                    or entry.get("name")
                    or entry.get("value")
                    or entry.get("slug")
                )
            else:
                label = entry
            label = str(label or "").strip()
            if label:
                values.append(label)
    lamination = str(request_snapshot.get("lamination") or "").strip()
    if lamination:
        values.append(lamination)
    if quote_item is not None:
        for finishing in quote_item.finishings.select_related("finishing_rate").all():
            rate = getattr(finishing, "finishing_rate", None)
            label = ""
            if rate is not None:
                label = str(getattr(rate, "name", "") or "").strip()
            if label:
                values.append(label)
    unique_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_values.append(value)
    return unique_values


def _format_finished_size(*, request_snapshot: dict, quote_item=None) -> str:
    explicit = _first_non_empty(
        request_snapshot.get("finished_size"),
        request_snapshot.get("size"),
        request_snapshot.get("size_label"),
    )
    if explicit is not None:
        return str(explicit).strip()
    if quote_item is None:
        return ""
    width = getattr(quote_item, "chosen_width_mm", None)
    height = getattr(quote_item, "chosen_height_mm", None)
    if width and height:
        return f"{width}x{height}mm"
    return ""


def _build_reorder_draft_payload(*, managed_job: ManagedJob) -> dict:
    quote_request = getattr(managed_job, "source_quote_request", None)
    snapshot_root = _as_dict(getattr(quote_request, "request_snapshot", None))
    request_snapshot = _as_dict(snapshot_root.get("request_snapshot")) or snapshot_root
    quote_item = None
    if quote_request is not None:
        quote_item = (
            quote_request.items.select_related("paper")
            .prefetch_related("finishings__finishing_rate")
            .order_by("id")
            .first()
        )
    if not request_snapshot and quote_item is None:
        raise ValueError("Original job specs unavailable for reorder.")

    product_type = str(
        _first_non_empty(
            request_snapshot.get("product_type"),
            _as_dict(snapshot_root.get("calculator_inputs")).get("product_type"),
        ) or ""
    ).strip()
    quantity = _first_non_empty(
        request_snapshot.get("quantity"),
        _as_dict(snapshot_root.get("calculator_inputs")).get("quantity"),
        getattr(quote_item, "quantity", None),
        1,
    )
    paper_stock = str(_first_non_empty(request_snapshot.get("paper_stock"), "") or "").strip()
    requested_gsm = _first_non_empty(
        request_snapshot.get("requested_gsm"),
        getattr(getattr(quote_item, "paper", None), "gsm", None),
    )
    print_sides = str(
        _first_non_empty(request_snapshot.get("print_sides"), getattr(quote_item, "sides", None), "SIMPLEX") or "SIMPLEX"
    ).strip() or "SIMPLEX"
    color_mode = str(
        _first_non_empty(request_snapshot.get("color_mode"), getattr(quote_item, "color_mode", None), "COLOR") or "COLOR"
    ).strip() or "COLOR"
    lamination = str(_first_non_empty(request_snapshot.get("lamination"), "none") or "none").strip() or "none"
    finished_size = _format_finished_size(request_snapshot=request_snapshot, quote_item=quote_item)
    finishing_list = _normalize_reorder_finishing_list(request_snapshot=request_snapshot, quote_item=quote_item)
    special_instructions = str(
        _first_non_empty(
            request_snapshot.get("special_instructions"),
            request_snapshot.get("custom_brief"),
            getattr(quote_item, "special_instructions", None),
            getattr(quote_request, "notes", None),
            "",
        ) or ""
    ).strip()

    product_label = str(
        _first_non_empty(
            request_snapshot.get("product_label"),
            product_type.replace("_", " ").title() if product_type else "",
        ) or "Print Job"
    ).strip()
    title = f"Reorder {product_label}".strip()
    if len(title) > 255:
        title = title[:255]

    calculator_inputs_snapshot = {
        "product_type": product_type,
        "quantity": int(quantity or 1),
        "finished_size": finished_size,
        "paper_stock": paper_stock,
        "requested_gsm": int(requested_gsm) if requested_gsm not in (None, "") else None,
        "print_sides": print_sides,
        "color_mode": color_mode,
        "lamination": lamination,
        "finishings": finishing_list,
        "custom_brief": special_instructions,
        "special_instructions": special_instructions,
    }
    request_details_snapshot = {
        "title": title,
        "notes": special_instructions,
        "request_snapshot": {
            **calculator_inputs_snapshot,
            "product_label": product_label,
            "size_label": str(request_snapshot.get("size_label") or finished_size or "").strip(),
            "paper_label": str(request_snapshot.get("paper_label") or "").strip(),
            "print_sides_label": str(request_snapshot.get("print_sides_label") or "").strip(),
            "color_mode_label": str(request_snapshot.get("color_mode_label") or "").strip(),
            "lamination_label": str(request_snapshot.get("lamination_label") or "").strip(),
        },
        "reorder_meta": {
            "source_job_id": managed_job.id,
            "specs_copied_from": managed_job.id,
            "finishing_list": finishing_list,
        },
    }
    return {
        "title": title,
        "calculator_inputs_snapshot": calculator_inputs_snapshot,
        "request_details_snapshot": request_details_snapshot,
    }


class PublicJobView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        return Response({"detail": "Overflow public job links are disabled in this batch."}, status=status.HTTP_410_GONE)


class PublicManagedJobTrackingView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, token):
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("broker", "broker__profile", "source_quote"),
            tracking_token=token,
        )
        serializer = ManagedJobPublicTrackingSerializer(managed_job, context={"request": request})
        return Response(serializer.data)


def _can_access_managed_job(*, user, managed_job: ManagedJob, actor: str) -> bool:
    if actor == OPS_ACTOR:
        return True
    if _is_managed_job_broker(user=user, managed_job=managed_job):
        return True
    if actor == SHOP_ACTOR:
        if managed_job.assigned_shop_id and getattr(managed_job.assigned_shop, "owner_id", None) == user.id:
            return True
        return managed_job.assignments.filter(
            reassigned_from__isnull=True,
            assigned_shop__owner=user,
        ).exists()
    if actor == PARTNER_ACTOR:
        return managed_job.broker_id == user.id
    return managed_job.client_id == user.id or managed_job.created_by_id == user.id


def _is_managed_job_broker(*, user, managed_job: ManagedJob) -> bool:
    return bool(managed_job.broker_id and managed_job.broker_id == getattr(user, "id", None))


def _effective_managed_job_actor(*, user, managed_job: ManagedJob, actor: str) -> str:
    if actor != OPS_ACTOR and _is_managed_job_broker(user=user, managed_job=managed_job):
        return PARTNER_ACTOR
    return actor


def _can_manage_assignment(*, user, assignment: JobAssignment, actor: str) -> bool:
    if actor == OPS_ACTOR:
        return True
    if actor == SHOP_ACTOR:
        if assignment.assigned_shop_id and getattr(assignment.assigned_shop, "owner_id", None) == user.id:
            return True
        return False
    return False


class ManagedJobFileListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("assigned_shop", "client", "broker", "created_by"),
            pk=pk,
        )
        actor = resolve_actor(request.user)
        if not _can_access_managed_job(user=request.user, managed_job=managed_job, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        files = get_visible_job_files_for_actor(
            managed_job=managed_job,
            actor=_effective_managed_job_actor(user=request.user, managed_job=managed_job, actor=actor),
        )
        return Response(JobFileSerializer(files, many=True, context={"request": request}).data)


class ManagedJobArtworkUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("assigned_shop", "client", "broker", "created_by"),
            pk=pk,
        )
        actor = resolve_actor(request.user)
        if not _can_access_managed_job(user=request.user, managed_job=managed_job, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        if actor not in {OPS_ACTOR, CLIENT_ACTOR, PARTNER_ACTOR}:
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": _("An artwork file is required.")}, status=status.HTTP_400_BAD_REQUEST)
        assignment = managed_job.assignments.filter(reassigned_from__isnull=True).first()
        try:
            job_file = upload_artwork_for_managed_job(
                managed_job=managed_job,
                assignment=assignment,
                uploaded_by=request.user,
                file=upload,
                original_filename=getattr(upload, "name", ""),
                notes=request.data.get("note", "") or "Artwork uploaded for production.",
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(JobFileSerializer(job_file, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ManagedJobListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request.user)
        queryset = ManagedJob.objects.select_related("assigned_shop", "client", "broker", "created_by").prefetch_related("job_files")
        if actor == OPS_ACTOR:
            items = queryset.order_by("-operational_priority_level", "-created_at")
        elif actor == SHOP_ACTOR:
            items = queryset.filter(Q(assigned_shop__owner=request.user) | Q(broker=request.user)).distinct().order_by("-operational_priority_level", "-created_at")
        elif actor == PARTNER_ACTOR:
            items = queryset.filter(broker=request.user).order_by("-operational_priority_level", "-created_at")
        else:
            items = queryset.filter(client=request.user).order_by("-operational_priority_level", "-created_at")
        return Response(
            [
                select_actor_serializer("managed_job", request.user, default=ManagedJobSerializer, instance=item)(
                    item,
                    context={"request": request},
                ).data
                for item in items
            ]
        )


class ManagedJobReorderView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        actor = resolve_actor(request.user)
        if actor != CLIENT_ACTOR:
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("source_quote_request").prefetch_related("source_quote_request__items__finishings__finishing_rate"),
            Q(client=request.user) | Q(created_by=request.user),
            pk=pk,
        )
        if managed_job.status != "completed":
            return Response({"detail": "Can only reorder completed jobs"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            payload = _build_reorder_draft_payload(managed_job=managed_job)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        draft = save_calculator_draft(
            user=request.user,
            source_job=managed_job,
            title=payload["title"],
            calculator_inputs_snapshot=payload["calculator_inputs_snapshot"],
            request_details_snapshot=payload["request_details_snapshot"],
            calculator_context=CalculatorDraftContext.CLIENT_DASHBOARD,
            intent=CalculatorDraftIntent.SAVE_DRAFT,
        )
        return Response(
            {
                "draft_id": draft.id,
                "specs_copied_from": managed_job.id,
            },
            status=status.HTTP_201_CREATED,
        )


class ManagedJobPaymentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("assigned_shop", "client", "broker", "created_by"),
            pk=pk,
        )
        actor = resolve_actor(request.user)
        if not _can_access_managed_job(user=request.user, managed_job=managed_job, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        return Response([])


class ManagedJobSettlementDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("assigned_shop", "client", "broker", "created_by"),
            pk=pk,
        )
        actor = resolve_actor(request.user)
        if not _can_access_managed_job(user=request.user, managed_job=managed_job, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        settlement = initialize_settlement_for_managed_job(managed_job=managed_job)
        return Response(JobSettlementSplitSerializer(settlement, context={"request": request}).data)


class JobStatusEventListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("assigned_shop", "client", "broker", "created_by"),
            pk=pk,
        )
        actor = resolve_actor(request.user)
        if not _can_access_managed_job(user=request.user, managed_job=managed_job, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        events = managed_job.events.select_related("actor").order_by("-created_at", "-id")[:50]
        return Response(JobStatusEventSerializer(events, many=True).data)


class ManagedJobProofUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("assigned_shop", "client", "broker", "created_by"),
            pk=pk,
        )
        actor = resolve_actor(request.user)
        if not _can_access_managed_job(user=request.user, managed_job=managed_job, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        if actor not in {OPS_ACTOR, SHOP_ACTOR, PARTNER_ACTOR}:
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": _("A proof file is required.")}, status=status.HTTP_400_BAD_REQUEST)
        job_file = upload_proof_for_managed_job(
            managed_job=managed_job,
            assignment=managed_job.assignments.filter(reassigned_from__isnull=True).first(),
            uploaded_by=request.user,
            file=upload,
            original_filename=getattr(upload, "name", ""),
            notes=request.data.get("note", "") or "Proof uploaded for approval.",
        )
        return Response(JobFileSerializer(job_file, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ManagerJobProofApprovalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("assigned_shop", "client", "broker", "created_by"),
            pk=job_id,
        )
        actor = resolve_actor(request.user)
        if not _can_access_managed_job(user=request.user, managed_job=managed_job, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        effective_actor = _effective_managed_job_actor(user=request.user, managed_job=managed_job, actor=actor)
        if effective_actor not in {OPS_ACTOR, PARTNER_ACTOR}:
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)

        serializer = JobActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get("note", "")
        action_name = str(request.data.get("action") or "approve").lower()
        proof = managed_job.job_files.filter(
            file_type=JobFileType.PROOF,
            status__in=[
                JobFileStatus.MANAGER_REVIEW,
                JobFileStatus.PROOF_UPLOADED,
            ],
        ).order_by("-created_at", "-id").first()
        if proof is None:
            return Response({"detail": _("No proof is waiting for manager approval.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            if action_name == "approve":
                proof = manager_approve_job_proof(job_file=proof, actor=request.user, notes=note)
            elif action_name == "reject":
                proof = manager_reject_job_proof(job_file=proof, actor=request.user, notes=note)
            else:
                return Response({"detail": _("Unsupported proof approval action.")}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(JobFileSerializer(proof, context={"request": request}).data)


class JobFileActionView(APIView):
    permission_classes = [IsAuthenticated]
    action_name = ""

    def post(self, request, pk):
        job_file = get_object_or_404(
            JobFile.objects.select_related("managed_job__assigned_shop", "managed_job__client", "managed_job__broker", "managed_job__created_by", "assignment"),
            pk=pk,
        )
        managed_job = job_file.managed_job
        actor = resolve_actor(request.user)
        if not _can_access_managed_job(user=request.user, managed_job=managed_job, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        effective_actor = _effective_managed_job_actor(user=request.user, managed_job=managed_job, actor=actor)

        serializer = JobActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get("note", "")

        if self.action_name in {"approve", "reject", "revision"} and effective_actor not in {OPS_ACTOR, CLIENT_ACTOR, PARTNER_ACTOR}:
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        if self.action_name == "print_ready" and effective_actor not in {OPS_ACTOR, SHOP_ACTOR}:
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)

        try:
            if self.action_name == "approve":
                if effective_actor in {OPS_ACTOR, PARTNER_ACTOR} and job_file.status in {JobFileStatus.MANAGER_REVIEW, JobFileStatus.PROOF_UPLOADED}:
                    job_file = manager_approve_job_proof(job_file=job_file, actor=request.user, notes=note)
                else:
                    job_file = approve_job_proof(job_file=job_file, actor=request.user, notes=note)
            elif self.action_name == "reject":
                if effective_actor in {OPS_ACTOR, PARTNER_ACTOR} and job_file.status in {JobFileStatus.MANAGER_REVIEW, JobFileStatus.PROOF_UPLOADED}:
                    job_file = manager_reject_job_proof(job_file=job_file, actor=request.user, notes=note)
                else:
                    job_file = reject_job_proof(job_file=job_file, actor=request.user, notes=note)
            elif self.action_name == "revision":
                job_file = request_revision(job_file=job_file, actor=request.user, notes=note)
            else:
                job_file = mark_file_print_ready(job_file=job_file, actor=request.user, notes=note)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(JobFileSerializer(job_file, context={"request": request}).data)


class JobFileApproveView(JobFileActionView):
    action_name = "approve"


class JobFileRejectView(JobFileActionView):
    action_name = "reject"


class JobFileRevisionView(JobFileActionView):
    action_name = "revision"


class JobFilePrintReadyView(JobFileActionView):
    action_name = "print_ready"


class ShopAssignmentListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        actor = resolve_actor(request.user)
        if actor not in {OPS_ACTOR, SHOP_ACTOR}:
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        queryset = JobAssignment.objects.select_related("managed_job", "assigned_shop", "production_order").filter(reassigned_from__isnull=True)
        if actor == SHOP_ACTOR:
            queryset = queryset.filter(assigned_shop__owner=request.user)
        return Response(JobAssignmentSerializer(queryset.order_by("-operational_priority_level", "-created_at"), many=True, context={"request": request}).data)


class JobAssignmentActionView(APIView):
    permission_classes = [IsAuthenticated]
    action_name = ""

    def post(self, request, pk):
        assignment = get_object_or_404(
            JobAssignment.objects.select_related("managed_job", "assigned_shop", "production_order"),
            pk=pk,
        )
        actor = resolve_actor(request.user)
        if not _can_manage_assignment(user=request.user, assignment=assignment, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        serializer = JobActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get("note", "")
        try:
            if self.action_name == "accept":
                assignment = accept_assignment(assignment=assignment, actor=request.user, note=note)
            elif self.action_name == "reject":
                assignment = reject_assignment(assignment=assignment, actor=request.user, note=note)
            elif self.action_name == "in_production":
                assignment = mark_assignment_in_production(assignment=assignment, actor=request.user, note=note)
            elif self.action_name == "finishing":
                assignment = mark_assignment_finishing(assignment=assignment, actor=request.user, note=note)
            elif self.action_name == "ready":
                assignment = mark_assignment_ready(assignment=assignment, actor=request.user, note=note)
            elif self.action_name == "completed":
                assignment = mark_assignment_completed(assignment=assignment, actor=request.user, note=note)
            else:
                assignment = report_assignment_issue(assignment=assignment, actor=request.user, note=note)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        sync_managed_job_artwork_requirement(managed_job=assignment.managed_job)
        return Response(JobAssignmentSerializer(assignment, context={"request": request}).data)


class JobAssignmentAcceptView(JobAssignmentActionView):
    action_name = "accept"


class JobAssignmentRejectView(JobAssignmentActionView):
    action_name = "reject"


class JobAssignmentInProductionView(JobAssignmentActionView):
    action_name = "in_production"


class JobAssignmentFinishingView(JobAssignmentActionView):
    action_name = "finishing"


class JobAssignmentReadyView(JobAssignmentActionView):
    action_name = "ready"


class JobAssignmentCompletedView(JobAssignmentActionView):
    action_name = "completed"


class JobAssignmentIssueView(JobAssignmentActionView):
    action_name = "issue"


class JobFileDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        job_file = get_object_or_404(
            JobFile.objects.select_related(
                "managed_job__assigned_shop",
                "managed_job__client",
                "managed_job__broker",
                "managed_job__created_by",
            ),
            pk=pk,
        )
        managed_job = job_file.managed_job
        actor = resolve_actor(request.user)
        if not _can_access_managed_job(user=request.user, managed_job=managed_job, actor=actor):
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        effective_actor = _effective_managed_job_actor(user=request.user, managed_job=managed_job, actor=actor)
        visible_ids = set(
            get_visible_job_files_for_actor(managed_job=managed_job, actor=effective_actor).values_list("id", flat=True)
        )
        if job_file.id not in visible_ids:
            return Response({"detail": _("Not authorized.")}, status=status.HTTP_403_FORBIDDEN)
        if not job_file.file:
            return Response({"detail": _("File is not available for download.")}, status=status.HTTP_404_NOT_FOUND)
        return FileResponse(
            job_file.file.open("rb"),
            as_attachment=True,
            filename=job_file.original_filename or job_file.file.name.rsplit("/", 1)[-1],
        )
