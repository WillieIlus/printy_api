"""Serializers for super admin analytics endpoints."""
from rest_framework import serializers


class AnalyticsCountByLabelSerializer(serializers.Serializer):
    label = serializers.CharField()
    count = serializers.IntegerField()


class AnalyticsTimeSeriesQuerySerializer(serializers.Serializer):
    range = serializers.ChoiceField(
        choices=["today", "7d", "30d", "90d"],
        required=False,
        default="7d",
    )
    interval = serializers.ChoiceField(
        choices=["hour", "day", "week"],
        required=False,
        default="day",
    )

    def validate(self, attrs):
        selected_range = attrs.get("range", "7d")
        interval = attrs.get("interval", "day")

        if selected_range == "today" and interval in {"day", "week"}:
            attrs["interval"] = "hour"
        elif selected_range in {"7d", "30d"} and interval == "week":
            attrs["interval"] = "day"
        elif selected_range == "90d" and interval == "hour":
            attrs["interval"] = "day"

        return attrs


class AnalyticsRangeQuerySerializer(serializers.Serializer):
    range = serializers.ChoiceField(
        choices=["today", "7d", "30d", "90d"],
        required=False,
        default="7d",
    )


class AnalyticsDashboardSummarySerializer(serializers.Serializer):
    total_visits_today = serializers.IntegerField()
    total_visits_this_week = serializers.IntegerField()
    total_visits_this_month = serializers.IntegerField()
    unique_visitors_today = serializers.IntegerField()
    quote_requests_today = serializers.IntegerField()
    quote_requests_this_week = serializers.IntegerField()
    quote_conversion_rate_today = serializers.FloatField()
    recent_errors_count = serializers.IntegerField()
    top_cities = AnalyticsCountByLabelSerializer(many=True)
    top_paths = AnalyticsCountByLabelSerializer(many=True)
    top_searches = AnalyticsCountByLabelSerializer(many=True)


class AnalyticsTimeSeriesPointSerializer(serializers.Serializer):
    bucket = serializers.CharField()
    visits = serializers.IntegerField()
    unique_visitors = serializers.IntegerField()
    quote_starts = serializers.IntegerField()
    quote_submits = serializers.IntegerField()
    errors = serializers.IntegerField()


class AnalyticsTimeSeriesResponseSerializer(serializers.Serializer):
    range = serializers.CharField()
    interval = serializers.CharField()
    timezone = serializers.CharField()
    series = AnalyticsTimeSeriesPointSerializer(many=True)


class AnalyticsNamedMetricSerializer(serializers.Serializer):
    label = serializers.CharField()
    count = serializers.IntegerField()
    slug = serializers.CharField(required=False, allow_blank=True)
    path = serializers.CharField(required=False, allow_blank=True)


class AnalyticsTopMetricsSerializer(serializers.Serializer):
    top_viewed_products = AnalyticsNamedMetricSerializer(many=True)
    top_viewed_shops = AnalyticsNamedMetricSerializer(many=True)
    top_searched_keywords = AnalyticsCountByLabelSerializer(many=True)
    top_landing_pages = AnalyticsCountByLabelSerializer(many=True)


class AnalyticsLocationGroupSerializer(serializers.Serializer):
    label = serializers.CharField()
    count = serializers.IntegerField()


class AnalyticsIPBreakdownItemSerializer(serializers.Serializer):
    ip_address = serializers.CharField(allow_null=True)
    count = serializers.IntegerField()
    country = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True)
    region = serializers.CharField(allow_blank=True)
    last_seen_at = serializers.DateTimeField(allow_null=True)


class AnalyticsLocationBreakdownSerializer(serializers.Serializer):
    countries = AnalyticsLocationGroupSerializer(many=True)
    cities = AnalyticsLocationGroupSerializer(many=True)
    ip_addresses = serializers.DictField()


class AnalyticsErrorEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    event_type = serializers.CharField()
    path = serializers.CharField()
    status_code = serializers.IntegerField(allow_null=True)
    message = serializers.CharField(allow_blank=True)
    created_at = serializers.DateTimeField()


class AnalyticsErrorEventListSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = AnalyticsErrorEventSerializer(many=True)


class AnalyticsErrorAnalyticsSerializer(serializers.Serializer):
    latest_errors = AnalyticsErrorEventListSerializer()
    counts_by_path = AnalyticsCountByLabelSerializer(many=True)
    counts_by_status_code = AnalyticsCountByLabelSerializer(many=True)
    counts_by_event_type = AnalyticsCountByLabelSerializer(many=True)


class AnalyticsFunnelStageSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    count = serializers.IntegerField()
    conversion_from_previous = serializers.FloatField(allow_null=True)


class AnalyticsFunnelResponseSerializer(serializers.Serializer):
    range = serializers.CharField()
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    page_views = serializers.IntegerField()
    product_views = serializers.IntegerField()
    shop_views = serializers.IntegerField()
    quote_starts = serializers.IntegerField()
    quote_submits = serializers.IntegerField()
    product_view_rate = serializers.FloatField()
    shop_view_rate = serializers.FloatField()
    quote_start_rate = serializers.FloatField()
    quote_submit_rate = serializers.FloatField()
    overall_conversion_rate = serializers.FloatField()
    stages = AnalyticsFunnelStageSerializer(many=True)
