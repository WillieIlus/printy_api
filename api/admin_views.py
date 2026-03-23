"""
Base views for super admin analytics endpoints.

Any analytics endpoint added here should remain superuser-only.
"""
from datetime import timedelta

from django.db.models import Case, CharField, Count, F, Max, Q, Value, When
from django.db.models.functions import Cast, Coalesce, Concat, TruncDay, TruncHour, TruncWeek
from django.utils import timezone
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import viewsets

from common.models import AnalyticsEvent
from quotes.models import QuoteRequest

from .permissions import IsSuperUser
from .admin_serializers import (
    AnalyticsDashboardSummarySerializer,
    AnalyticsErrorAnalyticsSerializer,
    AnalyticsFunnelResponseSerializer,
    AnalyticsRangeQuerySerializer,
    AnalyticsTimeSeriesQuerySerializer,
    AnalyticsTimeSeriesResponseSerializer,
    AnalyticsLocationBreakdownSerializer,
    AnalyticsTopMetricsSerializer,
)


RANGE_TO_DELTA = {
    "today": None,
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


def get_range_start(selected_range, now=None):
    current_time = now or timezone.now()
    today_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    if selected_range == "today":
        return today_start
    return current_time - RANGE_TO_DELTA[selected_range]


def get_visitor_key_expression():
    return Case(
        When(~Q(visitor_id=""), then=Concat(Value("visitor:"), F("visitor_id"))),
        When(~Q(session_key=""), then=Concat(Value("session:"), F("session_key"))),
        When(user_id__isnull=False, then=Concat(Value("user:"), Cast("user_id", output_field=CharField()))),
        When(ip_address__isnull=False, then=Concat(Value("ip:"), Cast("ip_address", output_field=CharField()))),
        default=Value(""),
        output_field=CharField(),
    )


def get_search_term_expression():
    return Coalesce(
        F("metadata__search_term"),
        F("metadata__search"),
        F("metadata__query"),
        F("metadata__q"),
        F("query_params__q"),
        F("query_params__query"),
        Value(""),
        output_field=CharField(),
    )


class SuperAdminAnalyticsAPIView(APIView):
    """Base API view for super admin analytics endpoints."""

    authentication_classes = APIView.authentication_classes
    permission_classes = [IsAuthenticated, IsSuperUser]


class SuperAdminAnalyticsViewSet(viewsets.ViewSet):
    """Base viewset for super admin analytics endpoints."""

    authentication_classes = viewsets.ViewSet.authentication_classes
    permission_classes = [IsAuthenticated, IsSuperUser]


class AnalyticsDashboardSummaryView(SuperAdminAnalyticsAPIView):
    """Dashboard summary metrics for the super admin analytics workspace."""

    def get(self, request):
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=today_start.weekday())
        month_start = today_start.replace(day=1)
        recent_error_cutoff = now - timedelta(hours=24)

        visit_events = AnalyticsEvent.objects.filter(
            event_type=AnalyticsEvent.EventType.PAGE_VIEW
        )

        visits_today = visit_events.filter(created_at__gte=today_start)
        visits_this_week = visit_events.filter(created_at__gte=week_start)
        visits_this_month = visit_events.filter(created_at__gte=month_start)

        unique_visitors_today = (
            visits_today
            .annotate(visitor_key=get_visitor_key_expression())
            .exclude(visitor_key="")
            .values("visitor_key")
            .distinct()
            .count()
        )

        quote_requests_today = QuoteRequest.objects.filter(created_at__gte=today_start).count()
        quote_requests_this_week = QuoteRequest.objects.filter(created_at__gte=week_start).count()

        quote_starts_today = AnalyticsEvent.objects.filter(
            event_type=AnalyticsEvent.EventType.QUOTE_START,
            created_at__gte=today_start,
        ).count()
        quote_submits_today = AnalyticsEvent.objects.filter(
            event_type=AnalyticsEvent.EventType.QUOTE_SUBMIT,
            created_at__gte=today_start,
        ).count()
        quote_conversion_rate_today = round(
            (quote_submits_today / quote_starts_today) * 100, 2
        ) if quote_starts_today else 0.0

        recent_errors_count = AnalyticsEvent.objects.filter(
            event_type__in=[
                AnalyticsEvent.EventType.API_ERROR,
                AnalyticsEvent.EventType.FRONTEND_ERROR,
            ],
            created_at__gte=recent_error_cutoff,
        ).count()

        top_cities = list(
            visit_events
            .exclude(city="")
            .values(label=F("city"))
            .annotate(count=Count("id"))
            .order_by("-count", "label")[:5]
        )

        top_paths = list(
            visit_events
            .exclude(path="")
            .values(label=F("path"))
            .annotate(count=Count("id"))
            .order_by("-count", "label")[:10]
        )

        top_searches = list(
            AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.SEARCH)
            .annotate(search_term=get_search_term_expression())
            .exclude(search_term="")
            .values(label=F("search_term"))
            .annotate(count=Count("id"))
            .order_by("-count", "label")[:10]
        )

        payload = {
            "total_visits_today": visits_today.count(),
            "total_visits_this_week": visits_this_week.count(),
            "total_visits_this_month": visits_this_month.count(),
            "unique_visitors_today": unique_visitors_today,
            "quote_requests_today": quote_requests_today,
            "quote_requests_this_week": quote_requests_this_week,
            "quote_conversion_rate_today": quote_conversion_rate_today,
            "recent_errors_count": recent_errors_count,
            "top_cities": top_cities,
            "top_paths": top_paths,
            "top_searches": top_searches,
        }
        serializer = AnalyticsDashboardSummarySerializer(payload)
        return Response(serializer.data)


class AnalyticsTimeSeriesView(SuperAdminAnalyticsAPIView):
    """Chart-ready analytics time series for the super admin dashboard."""

    INTERVAL_TO_TRUNC = {
        "hour": TruncHour,
        "day": TruncDay,
        "week": TruncWeek,
    }

    def get(self, request):
        query_serializer = AnalyticsTimeSeriesQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        params = query_serializer.validated_data

        now = timezone.now()
        selected_range = params["range"]
        interval = params["interval"]
        start = get_range_start(selected_range, now=now)

        trunc = self.INTERVAL_TO_TRUNC[interval]

        base_queryset = AnalyticsEvent.objects.filter(created_at__gte=start)
        bucketed_events = (
            base_queryset
            .annotate(bucket=trunc("created_at"))
            .values("bucket")
            .annotate(
                visits=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.PAGE_VIEW)),
                quote_starts=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.QUOTE_START)),
                quote_submits=Count("id", filter=Q(event_type=AnalyticsEvent.EventType.QUOTE_SUBMIT)),
                errors=Count(
                    "id",
                    filter=Q(
                        event_type__in=[
                            AnalyticsEvent.EventType.API_ERROR,
                            AnalyticsEvent.EventType.FRONTEND_ERROR,
                        ]
                    ),
                ),
            )
            .order_by("bucket")
        )

        unique_visitors_rows = (
            base_queryset
            .annotate(bucket=trunc("created_at"), visitor_key=get_visitor_key_expression())
            .exclude(visitor_key="")
            .values("bucket", "visitor_key")
            .distinct()
            .order_by("bucket", "visitor_key")
        )

        series_by_bucket = {}
        for row in bucketed_events:
            bucket = row["bucket"]
            series_by_bucket[bucket] = {
                "bucket": bucket.isoformat() if bucket else "",
                "visits": row["visits"],
                "unique_visitors": 0,
                "quote_starts": row["quote_starts"],
                "quote_submits": row["quote_submits"],
                "errors": row["errors"],
            }

        unique_counts = {}
        for row in unique_visitors_rows:
            bucket = row["bucket"]
            unique_counts[bucket] = unique_counts.get(bucket, 0) + 1

        for bucket, unique_count in unique_counts.items():
            if bucket not in series_by_bucket:
                series_by_bucket[bucket] = {
                    "bucket": bucket.isoformat() if bucket else "",
                    "visits": 0,
                    "unique_visitors": 0,
                    "quote_starts": 0,
                    "quote_submits": 0,
                    "errors": 0,
                }
            series_by_bucket[bucket]["unique_visitors"] = unique_count

        payload = {
            "range": selected_range,
            "interval": interval,
            "timezone": timezone.get_current_timezone_name(),
            "series": [
                series_by_bucket[key]
                for key in sorted(series_by_bucket.keys())
            ],
        }
        serializer = AnalyticsTimeSeriesResponseSerializer(payload)
        return Response(serializer.data)


class AnalyticsIPBreakdownPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


class AnalyticsTopMetricsView(SuperAdminAnalyticsAPIView):
    """Top viewed entities and high-signal discovery metrics."""

    def get(self, request):
        top_viewed_products = list(
            {
                "label": row["label"],
                "slug": row["slug"],
                "path": row["path_value"],
                "count": row["count"],
            }
            for row in (
                AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.PRODUCT_VIEW)
                .annotate(
                    label=Coalesce(
                        F("metadata__product_name"),
                        F("metadata__name"),
                        F("path"),
                        Value(""),
                        output_field=CharField(),
                    ),
                    slug=Coalesce(
                        F("metadata__product_slug"),
                        F("metadata__slug"),
                        Value(""),
                        output_field=CharField(),
                    ),
                    path_value=Coalesce(F("path"), Value(""), output_field=CharField()),
                )
                .exclude(label="")
                .values("label", "slug", "path_value")
                .annotate(count=Count("id"))
                .order_by("-count", "label")[:10]
            )
        )

        top_viewed_shops = list(
            {
                "label": row["label"],
                "slug": row["slug"],
                "path": row["path_value"],
                "count": row["count"],
            }
            for row in (
                AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.SHOP_VIEW)
                .annotate(
                    label=Coalesce(
                        F("metadata__shop_name"),
                        F("metadata__name"),
                        F("path"),
                        Value(""),
                        output_field=CharField(),
                    ),
                    slug=Coalesce(
                        F("metadata__shop_slug"),
                        F("metadata__slug"),
                        Value(""),
                        output_field=CharField(),
                    ),
                    path_value=Coalesce(F("path"), Value(""), output_field=CharField()),
                )
                .exclude(label="")
                .values("label", "slug", "path_value")
                .annotate(count=Count("id"))
                .order_by("-count", "label")[:10]
            )
        )

        top_searched_keywords = list(
            AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.SEARCH)
            .annotate(label=get_search_term_expression())
            .exclude(label="")
            .values("label")
            .annotate(count=Count("id"))
            .order_by("-count", "label")[:10]
        )

        current_host = request.get_host().split(":")[0].strip().lower()
        landing_page_filter = Q(referer="") | Q(referer__isnull=True)
        if current_host:
            landing_page_filter |= ~Q(referer__icontains=current_host)

        top_landing_pages = list(
            AnalyticsEvent.objects.filter(event_type=AnalyticsEvent.EventType.PAGE_VIEW)
            .filter(landing_page_filter)
            .exclude(path="")
            .values(label=F("path"))
            .annotate(count=Count("id"))
            .order_by("-count", "label")[:10]
        )

        payload = {
            "top_viewed_products": top_viewed_products,
            "top_viewed_shops": top_viewed_shops,
            "top_searched_keywords": top_searched_keywords,
            "top_landing_pages": top_landing_pages,
        }
        serializer = AnalyticsTopMetricsSerializer(payload)
        return Response(serializer.data)


class AnalyticsLocationBreakdownView(SuperAdminAnalyticsAPIView):
    """Grouped analytics location data with paginated IP breakdown."""

    pagination_class = AnalyticsIPBreakdownPagination

    def get(self, request):
        base_queryset = AnalyticsEvent.objects.exclude(ip_address__isnull=True)

        countries = list(
            base_queryset.exclude(country="")
            .values(label=F("country"))
            .annotate(count=Count("id"))
            .order_by("-count", "label")[:20]
        )
        cities = list(
            base_queryset.exclude(city="")
            .values(label=F("city"))
            .annotate(count=Count("id"))
            .order_by("-count", "label")[:20]
        )
        ip_queryset = (
            base_queryset
            .values("ip_address", "country", "city", "region")
            .annotate(count=Count("id"), last_seen_at=Max("created_at"))
            .order_by("-count", "-last_seen_at", "ip_address")
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(ip_queryset, request, view=self)
        ip_items = [
            {
                "ip_address": row["ip_address"],
                "count": row["count"],
                "country": row["country"] or "",
                "city": row["city"] or "",
                "region": row["region"] or "",
                "last_seen_at": row["last_seen_at"],
            }
            for row in page
        ]

        payload = {
            "countries": countries,
            "cities": cities,
            "ip_addresses": {
                "count": paginator.page.paginator.count,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": ip_items,
            },
        }
        serializer = AnalyticsLocationBreakdownSerializer(payload)
        return Response(serializer.data)


class AnalyticsErrorAnalyticsView(SuperAdminAnalyticsAPIView):
    """Latest errors and grouped error breakdowns."""

    def get(self, request):
        error_queryset = AnalyticsEvent.objects.filter(
            event_type__in=[
                AnalyticsEvent.EventType.API_ERROR,
                AnalyticsEvent.EventType.FRONTEND_ERROR,
            ]
        )

        latest_errors = [
            {
                "id": event.id,
                "event_type": event.event_type,
                "path": event.path or "",
                "status_code": event.status_code,
                "message": str((event.metadata or {}).get("message") or (event.metadata or {}).get("error", "")),
                "created_at": event.created_at,
            }
            for event in error_queryset.order_by("-created_at", "-id")[:25]
        ]

        counts_by_path = list(
            error_queryset.exclude(path="")
            .values(label=F("path"))
            .annotate(count=Count("id"))
            .order_by("-count", "label")[:20]
        )
        counts_by_status_code = [
            {
                "label": "unknown" if row["status_code"] is None else str(row["status_code"]),
                "count": row["count"],
            }
            for row in (
                error_queryset.values("status_code")
                .annotate(count=Count("id"))
                .order_by("-count", "status_code")[:20]
            )
        ]
        counts_by_event_type = list(
            error_queryset.values(label=F("event_type"))
            .annotate(count=Count("id"))
            .order_by("-count", "label")
        )

        payload = {
            "latest_errors": {
                "count": len(latest_errors),
                "next": None,
                "previous": None,
                "results": latest_errors,
            },
            "counts_by_path": counts_by_path,
            "counts_by_status_code": counts_by_status_code,
            "counts_by_event_type": counts_by_event_type,
        }
        serializer = AnalyticsErrorAnalyticsSerializer(payload)
        return Response(serializer.data)


class AnalyticsFunnelView(SuperAdminAnalyticsAPIView):
    """Quote funnel metrics for super admin analytics."""

    def get(self, request):
        query_serializer = AnalyticsRangeQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        selected_range = query_serializer.validated_data["range"]

        now = timezone.now()
        start = get_range_start(selected_range, now=now)

        base_queryset = AnalyticsEvent.objects.filter(created_at__gte=start)
        counts = {
            "page_views": base_queryset.filter(event_type=AnalyticsEvent.EventType.PAGE_VIEW).count(),
            "product_views": base_queryset.filter(event_type=AnalyticsEvent.EventType.PRODUCT_VIEW).count(),
            "shop_views": base_queryset.filter(event_type=AnalyticsEvent.EventType.SHOP_VIEW).count(),
            "quote_starts": base_queryset.filter(event_type=AnalyticsEvent.EventType.QUOTE_START).count(),
            "quote_submits": base_queryset.filter(event_type=AnalyticsEvent.EventType.QUOTE_SUBMIT).count(),
        }

        def percent(current, previous):
            return round((current / previous) * 100, 2) if previous else 0.0

        stages = [
            {
                "key": "page_views",
                "label": "Page views",
                "count": counts["page_views"],
                "conversion_from_previous": None,
            },
            {
                "key": "product_views",
                "label": "Product views",
                "count": counts["product_views"],
                "conversion_from_previous": percent(counts["product_views"], counts["page_views"]),
            },
            {
                "key": "shop_views",
                "label": "Shop views",
                "count": counts["shop_views"],
                "conversion_from_previous": percent(counts["shop_views"], counts["product_views"]),
            },
            {
                "key": "quote_starts",
                "label": "Quote starts",
                "count": counts["quote_starts"],
                "conversion_from_previous": percent(counts["quote_starts"], counts["shop_views"]),
            },
            {
                "key": "quote_submits",
                "label": "Quote submits",
                "count": counts["quote_submits"],
                "conversion_from_previous": percent(counts["quote_submits"], counts["quote_starts"]),
            },
        ]

        payload = {
            "range": selected_range,
            "start": start,
            "end": now,
            **counts,
            "product_view_rate": percent(counts["product_views"], counts["page_views"]),
            "shop_view_rate": percent(counts["shop_views"], counts["product_views"]),
            "quote_start_rate": percent(counts["quote_starts"], counts["shop_views"]),
            "quote_submit_rate": percent(counts["quote_submits"], counts["quote_starts"]),
            "overall_conversion_rate": percent(counts["quote_submits"], counts["page_views"]),
            "stages": stages,
        }
        serializer = AnalyticsFunnelResponseSerializer(payload)
        return Response(serializer.data)
