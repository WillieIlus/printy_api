"""API filters for list views."""
import django_filters

from jobs.models import JobRequest
from quotes.models import QuoteRequest


class QuoteFilterSet(django_filters.FilterSet):
    """Filters for /api/quotes/ list."""

    status = django_filters.ChoiceFilter(choices=QuoteRequest.STATUS_CHOICES)
    created_by = django_filters.NumberFilter(field_name="created_by_id")
    product = django_filters.NumberFilter(
        field_name="items__product_id",
        distinct=True,
        label="Product ID (quote has item with this product)",
    )
    date_from = django_filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="gte",
        label="Created on or after",
    )
    date_to = django_filters.DateTimeFilter(
        field_name="created_at",
        lookup_expr="lte",
        label="Created on or before",
    )

    class Meta:
        model = QuoteRequest
        fields = ["status", "created_by", "product", "shop"]


class JobRequestFilterSet(django_filters.FilterSet):
    """Filters for /api/job-requests/ list."""

    status = django_filters.ChoiceFilter(choices=JobRequest.STATUS_CHOICES)
    created_by = django_filters.NumberFilter(field_name="created_by_id")
    machine_type = django_filters.ChoiceFilter(choices=JobRequest._meta.get_field("machine_type").choices)
    finishing = django_filters.CharFilter(
        method="filter_finishing",
        label="Finishing capability (job requires this)",
    )

    class Meta:
        model = JobRequest
        fields = ["status", "created_by", "machine_type"]

    def filter_finishing(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(finishing_capabilities__contains=[value])
