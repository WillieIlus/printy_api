"""JobShare API serializers."""
from rest_framework import serializers

from jobs.models import JobClaim, JobRequest


class JobRequestCreateSerializer(serializers.ModelSerializer):
    """Create a job request (authenticated printer/staff)."""

    class Meta:
        model = JobRequest
        fields = ["title", "specs", "location", "deadline", "machine_type", "finishing_capabilities"]


class JobRequestListSerializer(serializers.ModelSerializer):
    """List job requests (safe fields)."""

    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)
    claims_count = serializers.SerializerMethodField()
    claims = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = JobRequest
        fields = [
            "id",
            "title",
            "specs",
            "location",
            "deadline",
            "status",
            "status_label",
            "created_by",
            "created_by_email",
            "created_at",
            "claims_count",
            "claims",
        ]

    def get_claims_count(self, obj):
        return obj.claims.count()

    def get_claims(self, obj):
        request = self.context.get("request")
        if request and request.user and obj.created_by_id == request.user.id:
            return JobClaimSerializer(obj.claims.all(), many=True).data
        return []


class JobRequestDetailSerializer(serializers.ModelSerializer):
    """Full detail for owner or authenticated users."""

    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)
    claims_count = serializers.SerializerMethodField()
    claims = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = JobRequest
        fields = [
            "id",
            "title",
            "specs",
            "location",
            "deadline",
            "status",
            "status_label",
            "machine_type",
            "finishing_capabilities",
            "created_by",
            "created_by_email",
            "created_at",
            "updated_at",
            "claims_count",
            "claims",
        ]

    def get_claims_count(self, obj):
        return obj.claims.count()

    def get_claims(self, obj):
        return JobClaimSerializer(obj.claims.all(), many=True).data


class JobClaimCreateSerializer(serializers.ModelSerializer):
    """Create a claim on a job request."""

    class Meta:
        model = JobClaim
        fields = ["price_offered", "message"]


class JobClaimSerializer(serializers.ModelSerializer):
    """Read claim with claimant info."""

    claimed_by_email = serializers.EmailField(source="claimed_by.email", read_only=True)
    job_request_title = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = JobClaim
        fields = [
            "id",
            "job_request",
            "job_request_title",
            "claimed_by",
            "claimed_by_email",
            "price_offered",
            "message",
            "status",
            "status_label",
            "created_at",
        ]

    def get_job_request_title(self, obj):
        return obj.job_request.title if obj.job_request_id else None


class JobRequestPublicSerializer(serializers.ModelSerializer):
    """Minimal safe fields for public token view. No internal data."""

    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = JobRequest
        fields = [
            "id",
            "title",
            "specs",
            "location",
            "deadline",
            "status",
            "status_label",
            "machine_type",
            "finishing_capabilities",
        ]
