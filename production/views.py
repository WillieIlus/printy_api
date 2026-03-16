"""
Production tracking viewsets and dashboard views.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum, Count, Q
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from api.permissions import IsJobCustomerOrShopOwner, IsJobShopOwner
from .models import (
    Customer,
    ProductionOrder,
    JobProcess,
    Operator,
    PriceCard,
    PricingMethod,
    Process,
    ProductionMaterial,
    ProductionProduct,
    WastageStage,
)
from .serializers import (
    CustomerSerializer,
    JobProcessSerializer,
    JobProcessWriteSerializer,
    ProductionOrderListSerializer,
    ProductionOrderSerializer,
    ProductionOrderWriteSerializer,
    OperatorSerializer,
    PriceCardSerializer,
    PricingMethodSerializer,
    ProcessSerializer,
    ProductionMaterialSerializer,
    ProductionProductSerializer,
    WastageStageSerializer,
)


def _get_shop_from_request(request):
    """
    Get shop from request. Only returns shops the user owns.
    Staff can access any shop via ?shop=<slug>. Non-staff: only their owned shop.
    """
    from shops.models import Shop

    user = request.user
    if not user or not user.is_authenticated:
        return None
    shop_slug = request.query_params.get("shop") or request.data.get("shop")
    if shop_slug:
        shop = Shop.objects.filter(slug=shop_slug, is_active=True).first()
        if not shop:
            return None
        # Staff can access any shop; others must own it
        if user.is_staff or shop.owner_id == user.id:
            return shop
        return None
    return user.owned_shops.filter(is_active=True).first()


class CustomerViewSet(viewsets.ModelViewSet):
    serializer_class = CustomerSerializer
    filterset_fields = ["name"]

    def get_queryset(self):
        shop = _get_shop_from_request(self.request)
        if not shop:
            return Customer.objects.none()
        return Customer.objects.filter(shop=shop)


class ProductionProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductionProductSerializer
    filterset_fields = ["name"]

    def get_queryset(self):
        shop = _get_shop_from_request(self.request)
        if not shop:
            return ProductionProduct.objects.none()
        return ProductionProduct.objects.filter(shop=shop)


class ProductionMaterialViewSet(viewsets.ModelViewSet):
    serializer_class = ProductionMaterialSerializer
    filterset_fields = ["name", "unit"]

    def get_queryset(self):
        shop = _get_shop_from_request(self.request)
        if not shop:
            return ProductionMaterial.objects.none()
        return ProductionMaterial.objects.filter(shop=shop)


class ProcessViewSet(viewsets.ModelViewSet):
    serializer_class = ProcessSerializer
    filterset_fields = ["slug"]

    def get_queryset(self):
        shop = _get_shop_from_request(self.request)
        if not shop:
            return Process.objects.none()
        return Process.objects.filter(shop=shop)


class OperatorViewSet(viewsets.ModelViewSet):
    serializer_class = OperatorSerializer
    filterset_fields = ["is_active"]

    def get_queryset(self):
        shop = _get_shop_from_request(self.request)
        if not shop:
            return Operator.objects.none()
        return Operator.objects.filter(shop=shop)


class PricingMethodViewSet(viewsets.ModelViewSet):
    serializer_class = PricingMethodSerializer
    filterset_fields = ["slug"]

    def get_queryset(self):
        shop = _get_shop_from_request(self.request)
        if not shop:
            return PricingMethod.objects.none()
        return PricingMethod.objects.filter(shop=shop)


class WastageStageViewSet(viewsets.ModelViewSet):
    serializer_class = WastageStageSerializer

    def get_queryset(self):
        shop = _get_shop_from_request(self.request)
        if not shop:
            return WastageStage.objects.none()
        return WastageStage.objects.filter(shop=shop)


class PriceCardViewSet(viewsets.ModelViewSet):
    serializer_class = PriceCardSerializer

    def get_queryset(self):
        shop = _get_shop_from_request(self.request)
        if not shop:
            return PriceCard.objects.none()
        return PriceCard.objects.filter(shop=shop)


class JobViewSet(viewsets.ModelViewSet):
    """Production orders (jobs). API kept as /jobs/ for backward compatibility."""
    permission_classes = [IsJobCustomerOrShopOwner]
    serializer_class = ProductionOrderSerializer
    filterset_fields = ["status", "customer", "product"]

    def get_queryset(self):
        request = self.request
        # Customer view: jobs from their accepted quotes (read-only)
        if request.query_params.get("as_customer"):
            return ProductionOrder.objects.filter(
                shop_quote__quote_request__created_by=request.user
            ).select_related("customer", "product", "shop_quote").prefetch_related("processes")
        # Shop view: jobs for owned shop
        shop = _get_shop_from_request(request)
        if not shop:
            return ProductionOrder.objects.none()
        return ProductionOrder.objects.filter(shop=shop).select_related(
            "customer", "product", "shop_quote"
        ).prefetch_related("processes")

    def _is_shop_owner_for_job(self, job):
        return job.shop.owner_id == self.request.user.id

    def create(self, request, *args, **kwargs):
        shop = _get_shop_from_request(request)
        if not shop:
            return Response({"detail": "Shop required."}, status=status.HTTP_400_BAD_REQUEST)
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        job = self.get_object()
        if not self._is_shop_owner_for_job(job) and not request.user.is_staff:
            return Response({"detail": "Only the shop can update jobs."}, status=status.HTTP_403_FORBIDDEN)
        old_status = job.status
        response = super().update(request, *args, **kwargs)
        job.refresh_from_db()
        if job.status != old_status and job.shop_quote_id:
            qr = job.shop_quote.quote_request
            if qr.created_by_id and qr.created_by_id != request.user.id:
                from notifications.models import Notification
                from notifications.services import notify

                notify(
                    recipient=qr.created_by,
                    notification_type=Notification.JOB_STATUS_UPDATED,
                    message=f"Job #{job.id} ({job.title or job.order_number or 'Order'}) is now {job.get_status_display()}",
                    object_type="production_order",
                    object_id=job.id,
                    actor=request.user,
                )
        return response

    def partial_update(self, request, *args, **kwargs):
        job = self.get_object()
        if not self._is_shop_owner_for_job(job) and not request.user.is_staff:
            return Response({"detail": "Only the shop can update jobs."}, status=status.HTTP_403_FORBIDDEN)
        old_status = job.status
        response = super().partial_update(request, *args, **kwargs)
        job.refresh_from_db()
        if job.status != old_status and job.shop_quote_id:
            qr = job.shop_quote.quote_request
            if qr.created_by_id and qr.created_by_id != request.user.id:
                from notifications.models import Notification
                from notifications.services import notify

                notify(
                    recipient=qr.created_by,
                    notification_type=Notification.JOB_STATUS_UPDATED,
                    message=f"Job #{job.id} ({job.title or job.order_number or 'Order'}) is now {job.get_status_display()}",
                    object_type="production_order",
                    object_id=job.id,
                    actor=request.user,
                )
        return response

    def destroy(self, request, *args, **kwargs):
        job = self.get_object()
        if not self._is_shop_owner_for_job(job) and not request.user.is_staff:
            return Response({"detail": "Only the shop can delete jobs."}, status=status.HTTP_403_FORBIDDEN)
        return super().destroy(request, *args, **kwargs)

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return ProductionOrderWriteSerializer
        if self.action == "list":
            return ProductionOrderListSerializer
        return ProductionOrderSerializer

    @action(detail=True, methods=["get", "post"])
    def processes(self, request, pk=None):
        job = self.get_object()
        if request.method == "GET":
            processes = job.processes.select_related(
                "process", "operator", "material", "pricing_method"
            ).all()
            serializer = JobProcessSerializer(processes, many=True)
            return Response(serializer.data)
        if request.method == "POST":
            if not self._is_shop_owner_for_job(job) and not request.user.is_staff:
                return Response(
                    {"detail": "Only the shop can add processes."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            serializer = JobProcessWriteSerializer(data=request.data)
            if serializer.is_valid():
                serializer.save(production_order=job)
                out = JobProcessSerializer(serializer.instance)
                return Response(out.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)


class JobProcessViewSet(viewsets.ModelViewSet):
    serializer_class = JobProcessSerializer

    def get_queryset(self):
        shop = _get_shop_from_request(self.request)
        if not shop:
            return JobProcess.objects.none()
        return JobProcess.objects.filter(production_order__shop=shop).select_related(
            "production_order", "process", "operator", "material", "pricing_method"
        )

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return JobProcessWriteSerializer
        return JobProcessSerializer


# ---------------------------------------------------------------------------
# Dashboard views
# ---------------------------------------------------------------------------


class DashboardViewSet(viewsets.ViewSet):
    """Dashboard analytics endpoints."""

    @action(detail=False, methods=["get"], url_path="job-summary")
    def job_summary(self, request):
        shop = _get_shop_from_request(request)
        if not shop:
            return Response({"detail": "Shop required."}, status=status.HTTP_400_BAD_REQUEST)
        jobs = ProductionOrder.objects.filter(shop=shop)
        total_revenue = jobs.aggregate(
            total=Sum("processes__line_total")
        )["total"] or Decimal("0")
        by_status = list(
            jobs.values("status").annotate(count=Count("id")).order_by("status")
        )
        return Response({
            "total_jobs": jobs.count(),
            "total_revenue": float(total_revenue),
            "by_status": by_status,
        })

    @action(detail=False, methods=["get"], url_path="weekly-summary")
    def weekly_summary(self, request):
        shop = _get_shop_from_request(request)
        if not shop:
            return Response({"detail": "Shop required."}, status=status.HTTP_400_BAD_REQUEST)
        today = timezone.now().date()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        processes = JobProcess.objects.filter(
            production_order__shop=shop,
            date__gte=week_start,
            date__lte=week_end,
        )
        revenue = processes.aggregate(total=Sum("line_total"))["total"] or Decimal("0")
        job_count = ProductionOrder.objects.filter(
            shop=shop,
            processes__date__gte=week_start,
            processes__date__lte=week_end,
        ).distinct().count()
        waste_total = processes.aggregate(total=Sum("waste"))["total"] or Decimal("0")
        return Response({
            "week_start": str(week_start),
            "week_end": str(week_end),
            "revenue": float(revenue),
            "jobs_count": job_count,
            "waste_total": float(waste_total),
        })

    @action(detail=False, methods=["get"], url_path="boss-dashboard")
    def boss_dashboard(self, request):
        shop = _get_shop_from_request(request)
        if not shop:
            return Response({"detail": "Shop required."}, status=status.HTTP_400_BAD_REQUEST)
        today = timezone.now().date()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        # Total revenue (all time)
        total_revenue = JobProcess.objects.filter(production_order__shop=shop).aggregate(
            total=Sum("line_total")
        )["total"] or Decimal("0")

        # This week's revenue
        week_revenue = JobProcess.objects.filter(
            production_order__shop=shop,
            date__gte=week_start,
            date__lte=week_end,
        ).aggregate(total=Sum("line_total"))["total"] or Decimal("0")

        # Operator productivity (this week)
        operator_stats = list(
            JobProcess.objects.filter(
                production_order__shop=shop,
                date__gte=week_start,
                date__lte=week_end,
                operator__isnull=False,
            )
            .values("operator__name")
            .annotate(
                job_count=Count("id"),
                revenue=Sum("line_total"),
                waste=Sum("waste"),
            )
        )

        # Material wastage (this week)
        material_waste = list(
            JobProcess.objects.filter(
                production_order__shop=shop,
                date__gte=week_start,
                date__lte=week_end,
                material__isnull=False,
            )
            .values("material__name")
            .annotate(waste=Sum("waste"))
        )

        # Job profitability (top jobs this week by revenue)
        top_jobs = list(
            ProductionOrder.objects.filter(
                shop=shop,
                processes__date__gte=week_start,
                processes__date__lte=week_end,
            )
            .annotate(revenue=Sum("processes__line_total"))
            .order_by("-revenue")[:10]
            .values("id", "order_number", "title", "revenue", "quantity")
        )

        return Response({
            "total_revenue": float(total_revenue),
            "week_revenue": float(week_revenue),
            "week_start": str(week_start),
            "week_end": str(week_end),
            "operator_productivity": operator_stats,
            "material_wastage": material_waste,
            "top_jobs": top_jobs,
        })
