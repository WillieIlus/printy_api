"""JobShare API views."""
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.filters import JobRequestFilterSet
from jobs.formatter import format_job_for_whatsapp_share
from jobs.models import JobClaim, JobNotification, JobRequest
from jobs.serializers import (
    JobClaimCreateSerializer,
    JobClaimSerializer,
    JobRequestCreateSerializer,
    JobRequestDetailSerializer,
    JobRequestListSerializer,
    JobRequestPublicSerializer,
)


class JobRequestViewSet(viewsets.ModelViewSet):
    """
    JobShare API.
    POST /api/job-requests/ — create (authenticated printer/staff)
    GET /api/job-requests/?status=OPEN — list
    GET /api/job-requests/{id}/ — detail
    POST /api/job-requests/{id}/whatsapp-share/ — shareable message + public_view_url
    """

    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend]
    filterset_class = JobRequestFilterSet

    def get_queryset(self):
        return JobRequest.objects.select_related("created_by").prefetch_related(
            "claims"
        ).order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "create":
            return JobRequestCreateSerializer
        if self.action in ("list",):
            return JobRequestListSerializer
        return JobRequestDetailSerializer

    def create(self, request, *args, **kwargs):
        from rest_framework import status
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            JobRequestDetailSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="whatsapp-share")
    def whatsapp_share(self, request, pk=None):
        """Returns shareable message + public_view_url (tokenized)."""
        job = self.get_object()
        job.ensure_public_token()
        message = format_job_for_whatsapp_share(job)
        frontend_url = getattr(settings, "FRONTEND_URL", "https://printy.ke")
        public_view_url = f"{frontend_url.rstrip('/')}/public/job/{job.public_token}"
        return Response({
            "message": message,
            "public_view_url": public_view_url,
        })

    @action(detail=True, methods=["post"], url_path="claims")
    def create_claim(self, request, pk=None):
        """POST /api/job-requests/{id}/claims/ — create a claim (only OPEN jobs)."""
        job = self.get_object()
        if job.status != JobRequest.OPEN:
            return Response(
                {"detail": _("Only open jobs can be claimed.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if job.created_by_id == request.user.id:
            return Response(
                {"detail": _("You cannot claim your own job.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = JobClaimCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        claim, created = JobClaim.objects.get_or_create(
            job_request=job,
            claimed_by=request.user,
            defaults={
                "price_offered": serializer.validated_data.get("price_offered"),
                "message": serializer.validated_data.get("message", ""),
            },
        )
        if not created:
            return Response(
                {"detail": _("You have already claimed this job.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            JobClaimSerializer(claim).data,
            status=status.HTTP_201_CREATED,
        )


class JobClaimViewSet(viewsets.ReadOnlyModelViewSet):
    """
    JobClaim API.
    GET /api/job-claims/?claimed_by=me — list (filter by claimed_by)
    GET /api/job-claims/{id}/ — retrieve claim
    POST /api/job-claims/{id}/accept/ — job owner accepts (marks job CLAIMED, creates notification)
    POST /api/job-claims/{id}/reject/ — job owner rejects
    """

    permission_classes = [IsAuthenticated]
    serializer_class = JobClaimSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["job_request", "status"]

    def get_queryset(self):
        qs = JobClaim.objects.select_related("job_request", "claimed_by").order_by("-created_at")
        if self.request.query_params.get("claimed_by") == "me":
            qs = qs.filter(claimed_by=self.request.user)
        return qs

    @action(detail=True, methods=["post"])
    def accept(self, request, pk=None):
        """Job owner accepts claim. Marks job CLAIMED, creates notification."""
        claim = self.get_object()
        if claim.job_request.created_by_id != request.user.id:
            return Response(
                {"detail": _("Only the job owner can accept claims.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if claim.status != JobClaim.PENDING:
            return Response(
                {"detail": _("Claim is no longer pending.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        claim.status = JobClaim.ACCEPTED
        claim.save(update_fields=["status", "updated_at"])
        claim.job_request.status = JobRequest.CLAIMED
        claim.job_request.save(update_fields=["status", "updated_at"])
        JobNotification.objects.create(
            user=claim.claimed_by,
            job_request=claim.job_request,
            job_claim=claim,
            message=_("Your claim on '%(title)s' was accepted!") % {"title": claim.job_request.title},
        )
        return Response(JobClaimSerializer(claim).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """Job owner rejects claim."""
        claim = self.get_object()
        if claim.job_request.created_by_id != request.user.id:
            return Response(
                {"detail": _("Only the job owner can reject claims.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if claim.status != JobClaim.PENDING:
            return Response(
                {"detail": _("Claim is no longer pending.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        claim.status = JobClaim.REJECTED
        claim.save(update_fields=["status", "updated_at"])
        return Response(JobClaimSerializer(claim).data)


class PublicJobView(APIView):
    """
    GET /api/public/job/{token}/ — minimal read-only info for public share.
    No auth required. Token must be valid.
    """

    permission_classes = [AllowAny]

    def get(self, request, token):
        job = get_object_or_404(JobRequest, public_token=token)
        serializer = JobRequestPublicSerializer(job)
        data = serializer.data
        # Add CTA hint
        data["claim_cta"] = _("Claim job")
        data["requires_login"] = True
        return Response(data)
