"""Analytics event ingestion endpoints."""
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from common.models import AnalyticsEvent
from common.request_meta import (
    get_client_ip,
    get_referer,
    get_request_method,
    get_request_path,
    get_request_query_params,
    get_session_key,
    get_user_agent,
    get_visitor_id,
    resolve_geo_from_ip,
)

from .analytics_serializers import AnalyticsEventIngestSerializer
from .throttling import AnalyticsEventThrottle


class AnalyticsEventIngestView(APIView):
    """POST /api/analytics/events/ - ingest analytics events from frontend/backend clients."""

    permission_classes = [AllowAny]
    throttle_classes = [AnalyticsEventThrottle]

    def post(self, request):
        serializer = AnalyticsEventIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        validated = serializer.validated_data
        ip_address = get_client_ip(request)
        geo = resolve_geo_from_ip(ip_address)

        metadata = dict(validated.get("metadata") or {})
        error_payload = validated.get("error")
        request_path = get_request_path(request)
        request_method = get_request_method(request)

        if error_payload is not None:
            metadata["error"] = error_payload
        metadata.setdefault("ingestion_path", request_path)
        metadata.setdefault("ingestion_method", request_method)

        AnalyticsEvent.objects.create(
            event_type=validated["event_type"],
            user=request.user if getattr(request.user, "is_authenticated", False) else None,
            session_key=get_session_key(request),
            visitor_id=get_visitor_id(request),
            ip_address=ip_address,
            user_agent=get_user_agent(request),
            referer=get_referer(request),
            path=validated.get("path") or request_path,
            method=request_method,
            query_params=get_request_query_params(request),
            metadata=metadata,
            country=geo["country"],
            region=geo["region"],
            city=geo["city"],
            status_code=validated.get("status_code"),
        )

        return Response({"ok": True}, status=status.HTTP_202_ACCEPTED)
