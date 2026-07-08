from __future__ import annotations

import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.roles import (
    CANONICAL_CLIENT_ROLE,
    CANONICAL_PARTNER_ROLE,
    CANONICAL_PRODUCTION_ROLE,
    CANONICAL_SUPER_ADMIN_ROLE,
    is_super_admin,
    resolve_user_roles,
)
from accounts.services.capabilities import has_capability
from accounts.models import UserProfile
from api.services.admin_dashboard import build_admin_dashboard_payload
from api.visibility import project_shop_identity
from jobs.choices import JobAssignmentStatus, ManagedJobAssignmentStatus, ManagedJobPaymentStatus, ManagedJobStatus
from jobs.artwork_confirmation import get_artwork_confirmation_payload, require_artwork_confirmation_dispatch_ready
from jobs.managed_services import create_assignment_for_managed_job
from jobs.models import JobAssignment, ManagedJob
from inventory.models import Paper
from notifications.models import Notification
from notifications.services import notify_quote_event
from jobs.file_services import managed_job_artwork_state, managed_job_has_artwork, notify_missing_artwork
from pricing.models import FinishingRate, PrintingRate
from pricing.services.platform_fee_policy import calculate_financial_split, create_quote_financial_split
from pricing.services.production_cost_calculator import calculate_client_price_with_waste_setup_and_quantity_tier
from jobs.settlement_compat import get_financial_split_for_job
from payments.models import Payment
from quotes.guardrails import calculate_quote_expiry, validate_partner_markup_amount
from quotes.models import QuoteRequest, Quote
from quotes.partner_services import respond_to_assigned_quote_request
from quotes.services_workflow import update_quote_response
from services.production_matching import build_partner_production_matches
from services.pricing.breakdown_projection import production_breakdown_from_preview
from services.pricing.finishing_normalization import is_empty_finishing, normalize_finishing_slug
from services.pricing.partner_market_rates import build_partner_market_rate_payload
from shops.models import Shop
from .workflow_serializers import (
    ClientQuoteRequestDetailSerializer,
    PartnerAssignedRequestShopOptionsSerializer,
    PartnerQuoteAttachClientSerializer,
    PartnerProductionMatchResponseSerializer,
    PartnerQuotePreviewSerializer,
    QuoteRequestReadSerializer,
    QuoteResponseReadSerializer,
)


class PartnerClientCreateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=50)
    email = serializers.EmailField(required=False, allow_blank=True)
    company = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        if not attrs.get("email") and not attrs.get("phone") and not attrs.get("name"):
            raise serializers.ValidationError("Email, phone, or name is required.")
        if not attrs.get("email") and not attrs.get("phone"):
            raise serializers.ValidationError("Email or phone is required to create a partner client.")
        return attrs


def _normalize_phone(value: str) -> str:
    return str(value or "").strip()


def _fallback_partner_client_email(*, partner_id: int, phone: str) -> str:
    digits = "".join(character for character in phone if character.isdigit()) or f"partner{partner_id}"
    return f"partner-client-{partner_id}-{digits}@printy.local"


def _dashboard_int(value):
    try:
        if value in (None, ""):
            return None
        return int(Decimal(str(value)))
    except Exception:
        return None


def _dashboard_size(payload: dict[str, object]) -> dict[str, int | None]:
    width = _dashboard_int(payload.get("width_mm"))
    height = _dashboard_int(payload.get("height_mm"))
    if width and height:
        return {"width_mm": width, "height_mm": height}
    match = re.search(r"(\d+(?:\.\d+)?)\s*[xXx]\s*(\d+(?:\.\d+)?)", str(payload.get("finished_size") or payload.get("size") or ""))
    if match:
        return {"width_mm": _dashboard_int(match.group(1)), "height_mm": _dashboard_int(match.group(2))}
    return {"width_mm": None, "height_mm": None}


def _dashboard_sides(value) -> str:
    raw = str(value or "").strip().lower()
    return "double" if raw in {"duplex", "double", "both", "two_sided", "two-sided"} else "single"


def _dashboard_color_mode(value) -> str:
    raw = str(value or "").strip().lower()
    return "black_only" if raw in {"black", "black_only", "mono", "monochrome", "bw"} else "full_color"


def _quote_request_reference(quote_request: QuoteRequest | None) -> str:
    if quote_request is None:
        return ""
    return quote_request.request_reference or f"QR-{quote_request.id}"


def _quote_reference(quote: Quote | None) -> str:
    if quote is None:
        return ""
    return quote.quote_reference or f"Q-{quote.id}"


def _assignment_reference(assignment: JobAssignment | None) -> str:
    if assignment is None:
        return ""
    return f"PA-{assignment.id:04d}"


def _partner_client_username(*, phone: str, email: str, partner_id: int) -> str:
    return phone or email or f"partner-client-{partner_id}"


def _resolve_or_create_partner_client(
    *,
    partner_user,
    client_user=None,
    client_name: str = "",
    client_email: str = "",
    client_phone: str = "",
    client_company: str = "",
):
    User = get_user_model()
    name = str(client_name or "").strip()
    phone = _normalize_phone(client_phone)
    email = str(client_email or "").strip().lower()
    company = str(client_company or "").strip()

    resolved_user = client_user
    if resolved_user is None and phone:
        resolved_user = User.objects.filter(username=phone).first()
    if resolved_user is None and email:
        resolved_user = User.objects.filter(email__iexact=email).first()

    if resolved_user is not None and getattr(resolved_user, "role", "") != User.Role.CLIENT:
        raise ValueError("Existing account cannot be linked as a partner client.")

    created_user = False
    if resolved_user is None:
        fallback_email = email or _fallback_partner_client_email(partner_id=partner_user.id, phone=phone)
        resolved_user = User.objects.create_user(
            email=fallback_email,
            password=None,
            username=_partner_client_username(phone=phone, email=fallback_email, partner_id=partner_user.id),
            name=name or fallback_email or phone or "Client",
            role=User.Role.CLIENT,
            is_active=True,
        )
        created_user = True

    record, created_record = PartnerClient.objects.get_or_create(
        partner=partner_user,
        client_user=resolved_user,
        defaults={
            "name": name or getattr(resolved_user, "name", "") or getattr(resolved_user, "email", "") or "Client",
            "phone": phone,
            "email": email or getattr(resolved_user, "email", "") or "",
            "company": company,
        },
    )
    update_fields: list[str] = []
    desired_name = name or record.name or getattr(resolved_user, "name", "") or getattr(resolved_user, "email", "") or "Client"
    if desired_name and record.name != desired_name:
        record.name = desired_name
        update_fields.append("name")
    if phone and record.phone != phone:
        record.phone = phone
        update_fields.append("phone")
    if email and record.email != email:
        record.email = email
        update_fields.append("email")
    if company != record.company:
        record.company = company
        update_fields.append("company")
    if update_fields:
        update_fields.append("updated_at")
        record.save(update_fields=update_fields)

    return {
        "client_user": resolved_user,
        "client_id": resolved_user.id,
        "name": record.name,
        "phone": record.phone,
        "email": record.email,
        "company": record.company,
        "is_new": created_user and created_record,
    }


def _partner_client_row(record: PartnerClient) -> dict[str, object]:
    client_user = getattr(record, "client_user", None)
    return {
        "id": record.id,
        "client_id": record.client_user_id,
        "name": record.name or getattr(client_user, "name", "") or getattr(client_user, "email", "") or "Client",
        "phone": record.phone or getattr(client_user, "username", "") or "",
        "email": record.email or getattr(client_user, "email", "") or "",
        "company": record.company or "",
        "is_new": False,
    }


class BaseDashboardHomeView(APIView):
    permission_classes = [IsAuthenticated]
    dashboard_role = ""
    allowed_roles: tuple[str, ...] = ()

    def has_dashboard_access(self, user) -> bool:
        roles = set(resolve_user_roles(user))
        return bool(roles.intersection(self.allowed_roles))

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user and request.user.is_authenticated and not self.has_dashboard_access(request.user):
            raise PermissionDenied(
                detail={
                    "detail": f"This workspace is only available to {self.dashboard_role} accounts.",
                    "expected_dashboard_role": self.dashboard_role,
                }
            )


class ClientDashboardHomeView(BaseDashboardHomeView):
    dashboard_role = "client"
    allowed_roles = (CANONICAL_CLIENT_ROLE,)

    def get(self, request):
        jobs = ManagedJob.objects.filter(
            Q(client=request.user) | Q(created_by=request.user)
        ).select_related("assigned_shop").order_by("-updated_at", "-created_at").distinct()
        payments = Payment.objects.filter(managed_job__in=jobs).order_by("-created_at")
        recent_jobs = [
            {
                "id": job.id,
                "reference": job.managed_reference,
                "job_reference": job.managed_reference,
                "quote_request_reference": _quote_request_reference(getattr(job, "source_quote_request", None)),
                "quote_reference": _quote_reference(getattr(job, "source_quote", None)),
                "title": job.title or "Print job",
                "status": job.status,
                "payment_status": job.payment_status,
                "assigned_shop_name": project_shop_identity(
                    getattr(job.assigned_shop, "name", ""),
                    actor="client",
                    topology_mode="managed",
                ),
                "client_total": str(job.client_total) if job.client_total is not None else None,
            }
            for job in jobs[:6]
        ]
        payment_rows = [
            {
                "id": payment.id,
                "managed_job_id": payment.managed_job_id,
                "reference": getattr(payment.managed_job, "managed_reference", ""),
                "job_reference": getattr(payment.managed_job, "managed_reference", ""),
                "payment_reference": payment.account_reference or f"Payment {payment.id}",
                "amount": str(payment.amount) if payment.amount is not None else None,
                "payment_status": payment.status,
                "method": payment.method,
                "channel": payment.method,
                "checkout_request_id": payment.checkout_request_id,
            }
            for payment in payments.select_related("managed_job")[:6]
        ]
        return Response(
            {
                "role": "client",
                "stats": {
                    "open_jobs": jobs.exclude(status__in=["completed", "cancelled"]).count(),
                    "awaiting_payment": jobs.filter(payment_status__in=["pending", "initiated"]).count(),
                    "in_production": jobs.filter(status__in=["accepted", "in_production", "ready"]).count(),
                },
                "recent_jobs": recent_jobs,
                "payments": payment_rows,
                "actions": {
                    "primary": "/quotes",
                    "secondary": "/dashboard/client#payments",
                },
            }
        )


class PartnerDashboardHomeView(BaseDashboardHomeView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get(self, request):
        jobs = ManagedJob.objects.filter(broker=request.user).select_related(
            "client",
            "assigned_shop",
        ).order_by("-updated_at", "-created_at")
        quote_requests = QuoteRequest.objects.filter(
            Q(created_by=request.user) | Q(assigned_manager=request.user) | Q(managed_jobs__broker=request.user)
        ).select_related("shop", "assigned_manager").order_by("-updated_at", "-created_at").distinct()
        recent_jobs = [
            {
                "id": job.id,
                "reference": job.managed_reference,
                "job_reference": job.managed_reference,
                "quote_request_reference": _quote_request_reference(getattr(job, "source_quote_request", None)),
                "quote_reference": _quote_reference(getattr(job, "source_quote", None)),
                "title": job.title or "Managed print job",
                "status": job.status,
                "payment_status": job.payment_status,
                "assignment_status": job.assignment_status,
                "client_name": getattr(job.client, "name", "") or getattr(job.client, "email", "") or "Client",
                "client_total": str(job.client_total) if job.client_total is not None else None,
                "assigned_shop_name": getattr(job.assigned_shop, "name", "") or "Awaiting assignment",
            }
            for job in jobs[:8]
        ]
        request_rows = [
            {
                "id": quote_request.id,
                "reference": _quote_request_reference(quote_request),
                "quote_request_reference": _quote_request_reference(quote_request),
                "status": quote_request.status,
                "customer_name": quote_request.customer_name or "Client",
                "shop_name": getattr(quote_request.shop, "name", "") or "Awaiting production match",
            }
            for quote_request in quote_requests[:8]
        ]
        return Response(
            {
                "role": "partner",
                "stats": {
                    "active_clients": jobs.exclude(client_id__isnull=True).values("client_id").distinct().count(),
                    "managed_jobs": jobs.count(),
                    "awaiting_client_payment": jobs.filter(payment_status__in=["pending", "initiated"]).count(),
                },
                "recent_jobs": recent_jobs,
                "quote_requests": request_rows,
                "actions": {
                    "primary": "/quotes",
                    "secondary": "/for-shops",
                },
            }
        )


class PartnerMarketRateListView(BaseDashboardHomeView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get(self, request):
        return Response(build_partner_market_rate_payload(user=request.user))


class PartnerDashboardProfileView(BaseDashboardHomeView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def _get_profile(self, user):
        profile, _ = UserProfile.objects.get_or_create(
            user=user,
            defaults={"default_markup_rate": Decimal("0.75")},
        )
        return profile

    def get(self, request):
        profile = self._get_profile(request.user)
        return Response({"default_markup_rate": str(profile.default_markup_rate)})

    def patch(self, request):
        profile = self._get_profile(request.user)
        serializer = serializers.Serializer(data=request.data)
        serializer.fields["default_markup_rate"] = serializers.DecimalField(max_digits=5, decimal_places=4)
        serializer.is_valid(raise_exception=True)
        profile.default_markup_rate = serializer.validated_data["default_markup_rate"]
        profile.save(update_fields=["default_markup_rate", "updated_at"])
        return Response({"default_markup_rate": str(profile.default_markup_rate)})


class ProductionDashboardHomeView(BaseDashboardHomeView):
    dashboard_role = "production"
    allowed_roles = (CANONICAL_PRODUCTION_ROLE,)

    def get(self, request):
        assignments = JobAssignment.objects.filter(
            assigned_shop__owner=request.user,
            reassigned_from__isnull=True,
        ).select_related("managed_job", "assigned_shop").order_by("-operational_priority_level", "-id")
        jobs = ManagedJob.objects.filter(assigned_shop__owner=request.user).select_related("assigned_shop", "source_quote", "source_quote_request").order_by(
            "-operational_priority_level",
            "-updated_at",
        )
        recent_assignments = [
            {
                "id": assignment.id,
                "assignment_reference": _assignment_reference(assignment),
                "managed_job_id": assignment.managed_job_id,
                "reference": getattr(assignment.managed_job, "managed_reference", ""),
                "job_reference": getattr(assignment.managed_job, "managed_reference", ""),
                "quote_request_reference": _quote_request_reference(getattr(assignment.managed_job, "source_quote_request", None)),
                "quote_reference": _quote_reference(getattr(assignment.managed_job, "source_quote", None)),
                "status": assignment.status,
                "managed_job_status": getattr(assignment.managed_job, "status", ""),
                "payment_status": getattr(assignment.managed_job, "payment_status", ""),
                "priority": assignment.operational_priority_level,
                "due_at": assignment.due_at,
            }
            for assignment in assignments[:8]
        ]
        queue_rows = [
            {
                "id": job.id,
                "reference": job.managed_reference,
                "job_reference": job.managed_reference,
                "quote_request_reference": _quote_request_reference(getattr(job, "source_quote_request", None)),
                "quote_reference": _quote_reference(getattr(job, "source_quote", None)),
                "title": job.title or "Production job",
                "status": job.status,
                "payment_status": job.payment_status,
                "assignment_status": job.assignment_status,
                "source": _job_source(job),
            }
            for job in jobs[:8]
        ]
        return Response(
            {
                "role": "production",
                "stats": {
                    "incoming_assignments": assignments.filter(status="pending").count(),
                    "in_production": assignments.filter(status__in=["accepted", "in_production"]).count(),
                    "payment_holds": jobs.filter(payment_status__in=["pending", "initiated"]).count(),
                },
                "assignments": recent_assignments,
                "queue": queue_rows,
                "actions": {
                    "primary": "/dashboard/production#assignments",
                    "secondary": "/shop/jobs/incoming",
                },
            }
        )


class AdminDashboardHomeView(BaseDashboardHomeView):
    dashboard_role = "super_admin"
    allowed_roles = (CANONICAL_SUPER_ADMIN_ROLE,)

    def has_dashboard_access(self, user) -> bool:
        return is_super_admin(user)

    def get(self, request):
        return Response(build_admin_dashboard_payload())


class DashboardCountsView(APIView):
    permission_classes = [IsAuthenticated]

    def _unread_notifications(self, user) -> int:
        return Notification.objects.filter(user=user, read_at__isnull=True).count()

    def _client_counts(self, user) -> dict[str, int]:
        quotes = QuoteRequest.objects.filter(Q(created_by=user) | Q(on_behalf_of=user)).distinct()
        jobs = ManagedJob.objects.filter(Q(client=user) | Q(created_by=user)).distinct()
        return {
            "new_quotes": quotes.filter(quotes__status__in=["sent", "revised"]).distinct().count(),
            "payments_processing": Payment.objects.filter(
                Q(payer=user) | Q(managed_job__in=jobs),
                status__in=[Payment.STATUS_PENDING, Payment.STATUS_PROCESSING],
            ).distinct().count(),
            "active_jobs": jobs.exclude(status__in=[ManagedJobStatus.COMPLETED, ManagedJobStatus.CANCELLED]).count(),
            "completed_jobs": jobs.filter(status=ManagedJobStatus.COMPLETED).count(),
            "unread_notifications": self._unread_notifications(user),
        }

    def _manager_counts(self, user) -> dict[str, int]:
        requests = QuoteRequest.objects.filter(
            Q(created_by=user) | Q(assigned_manager=user) | Q(managed_jobs__broker=user)
        ).distinct()
        jobs = ManagedJob.objects.filter(broker=user).distinct()
        return {
            "new_requests": requests.filter(status="submitted", quotes__isnull=True).count(),
            "quotes_awaiting_client": requests.filter(quotes__status__in=["sent", "revised"]).distinct().count(),
            "paid_jobs_awaiting_dispatch": jobs.filter(
                payment_status=ManagedJobPaymentStatus.CONFIRMED,
                assignment_status=ManagedJobAssignmentStatus.UNASSIGNED,
            ).count(),
            "active_jobs": jobs.exclude(status__in=[ManagedJobStatus.COMPLETED, ManagedJobStatus.CANCELLED]).count(),
            "unread_notifications": self._unread_notifications(user),
        }

    def _shop_counts(self, user) -> dict[str, int]:
        assignments = JobAssignment.objects.filter(assigned_shop__owner=user, reassigned_from__isnull=True)
        jobs = ManagedJob.objects.filter(assigned_shop__owner=user).distinct()
        return {
            "new_assignments": assignments.filter(status=JobAssignmentStatus.PENDING).count(),
            "active_jobs": jobs.filter(status__in=[ManagedJobStatus.ASSIGNED, ManagedJobStatus.IN_PRODUCTION, ManagedJobStatus.FINISHING]).count(),
            "ready_jobs": jobs.filter(status=ManagedJobStatus.READY).count(),
            "unread_notifications": self._unread_notifications(user),
        }

    def get(self, request):
        roles = set(resolve_user_roles(request.user))
        if CANONICAL_PRODUCTION_ROLE in roles:
            role = "production"
            counts = self._shop_counts(request.user)
        elif CANONICAL_PARTNER_ROLE in roles:
            role = "partner"
            counts = self._manager_counts(request.user)
        elif CANONICAL_CLIENT_ROLE in roles:
            role = "client"
            counts = self._client_counts(request.user)
        else:
            role = "super_admin"
            counts = {"unread_notifications": self._unread_notifications(request.user)}
        return Response({"role": role, "counts": counts})


def _production_shop_filter(user):
    return Q(assigned_shop__owner=user)


def _job_source(job: ManagedJob) -> str:
    quote_request = getattr(job, "source_quote_request", None)
    quote = getattr(job, "source_quote", None)
    request_snapshot = getattr(quote_request, "request_snapshot", None) or {}
    response_snapshot = getattr(quote, "response_snapshot", None) or {}
    if (
        request_snapshot.get("direct_shop_intake")
        or request_snapshot.get("source") == "direct_shop_submission"
        or response_snapshot.get("direct_shop_intake")
    ):
        return "direct_shop"
    return "brokered"


def _job_pricing_snapshot(job: ManagedJob, role: str) -> dict[str, str | None]:
    split = getattr(getattr(job, "source_quote", None), "financial_split", None)
    client_total = str(split.client_total) if split is not None else (str(job.client_total) if job.client_total is not None else None)
    production_cost = str(split.production_cost) if split is not None else None
    shop_payout = str(split.shop_payout) if split is not None else None
    broker_payout = str(split.broker_payout) if split is not None else None
    printy_fee = str(split.printy_fee) if split is not None else None
    if role == CANONICAL_PRODUCTION_ROLE:
        return {
            "production_cost": production_cost,
            "shop_payout": shop_payout,
            "paper_price": None,
            "finishing_price": None,
            "client_total": None,
            "broker_payout": None,
            "printy_fee": None,
        }
    if role == CANONICAL_PARTNER_ROLE:
        return {
            "production_cost": production_cost,
            "shop_payout": shop_payout,
            "client_total": client_total,
            "broker_payout": broker_payout,
            "printy_fee": printy_fee,
        }
    return {
        "client_total": client_total,
        "printy_fee": None,
    }


def _quote_customer_pricing_payload(quote: Quote | None) -> dict[str, str]:
    if quote is None:
        return {}
    split = getattr(quote, "financial_split", None)
    if split is not None:
        return {
            "production_cost": str(split.production_cost),
            "gross_margin": str(split.gross_margin),
            "broker_margin_amount": str(split.gross_margin),
            "broker_payout": str(split.broker_payout),
            "printy_fee": str(split.printy_fee),
            "final_client_price": str(split.client_total),
            "estimated_total": str(split.client_total),
        }
    snapshot = quote.response_snapshot if isinstance(quote.response_snapshot, dict) else {}
    customer_pricing = snapshot.get("customer_pricing")
    return dict(customer_pricing) if isinstance(customer_pricing, dict) else {}


class BaseRoleDetailView(BaseDashboardHomeView):
    def _has_artwork(self, job: ManagedJob) -> bool:
        return job.job_files.filter(file_type__in=["artwork", "customer_upload"]).exists()

    def _request_snapshot_root(self, quote_request: QuoteRequest | None) -> dict[str, object]:
        return quote_request.request_snapshot if quote_request and isinstance(quote_request.request_snapshot, dict) else {}

    def _request_snapshot(self, quote_request: QuoteRequest | None) -> dict[str, object]:
        if not quote_request:
            return {}
        snapshot = self._request_snapshot_root(quote_request)
        nested = snapshot.get("request_snapshot")
        if isinstance(nested, dict):
            return nested
        return snapshot

    def _dispatch_missing_specs(self, job: ManagedJob) -> list[str]:
        quote_request = getattr(job, "source_quote_request", None)
        request_snapshot = self._request_snapshot(quote_request)
        root_snapshot = self._request_snapshot_root(quote_request)
        calculator_inputs = root_snapshot.get("calculator_inputs") if isinstance(root_snapshot.get("calculator_inputs"), dict) else {}
        required_specs = {
            "product_type": request_snapshot.get("product_type") or request_snapshot.get("product_label") or calculator_inputs.get("product_type"),
            "quantity": request_snapshot.get("quantity") or calculator_inputs.get("quantity"),
            "size": request_snapshot.get("finished_size") or request_snapshot.get("size_label") or calculator_inputs.get("finished_size"),
            "paper": request_snapshot.get("paper_stock") or request_snapshot.get("paper_label") or calculator_inputs.get("paper_stock"),
            "print_sides": request_snapshot.get("print_sides") or request_snapshot.get("print_sides_label") or calculator_inputs.get("print_sides"),
            "color_mode": request_snapshot.get("color_mode") or request_snapshot.get("color_mode_label") or calculator_inputs.get("color_mode"),
        }
        return [key for key, value in required_specs.items() if value in (None, "", [])]

    def _assigned_request_match_payload(self, quote_request: QuoteRequest, overrides: dict[str, object] | None = None) -> dict[str, object]:
        snapshot = self._request_snapshot_root(quote_request)
        nested = self._request_snapshot(quote_request)
        calculator_inputs = snapshot.get("calculator_inputs") if isinstance(snapshot.get("calculator_inputs"), dict) else {}
        overrides = overrides or {}

        def _pick(*values):
            for value in values:
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                return value
            return None

        payload = {
            "product_type": _pick(overrides.get("product_type"), nested.get("product_type"), calculator_inputs.get("product_type")),
            "quantity": _pick(overrides.get("quantity"), nested.get("quantity"), calculator_inputs.get("quantity")),
            "finished_size": _pick(overrides.get("finished_size"), nested.get("finished_size"), nested.get("size_label"), calculator_inputs.get("finished_size")),
            "paper_stock": _pick(overrides.get("paper_stock"), nested.get("paper_stock"), calculator_inputs.get("paper_stock")),
            "print_sides": _pick(overrides.get("print_sides"), nested.get("print_sides"), calculator_inputs.get("print_sides")),
            "color_mode": _pick(overrides.get("color_mode"), nested.get("color_mode"), calculator_inputs.get("color_mode")),
            "lamination": _pick(overrides.get("lamination"), nested.get("lamination"), calculator_inputs.get("lamination")),
            "urgency_type": _pick(overrides.get("urgency_type"), nested.get("urgency_type"), calculator_inputs.get("urgency_type")),
            "requested_paper_category": _pick(overrides.get("requested_paper_category"), nested.get("requested_paper_category"), calculator_inputs.get("requested_paper_category")),
            "requested_gsm": _pick(overrides.get("requested_gsm"), nested.get("requested_gsm"), calculator_inputs.get("requested_gsm")),
            "total_pages": _pick(overrides.get("total_pages"), nested.get("total_pages"), calculator_inputs.get("total_pages")),
            "cover_stock": _pick(overrides.get("cover_stock"), nested.get("cover_stock"), calculator_inputs.get("cover_stock")),
            "insert_stock": _pick(overrides.get("insert_stock"), nested.get("insert_stock"), calculator_inputs.get("insert_stock")),
            "requested_cover_paper_category": _pick(overrides.get("requested_cover_paper_category"), nested.get("requested_cover_paper_category"), calculator_inputs.get("requested_cover_paper_category")),
            "requested_cover_gsm": _pick(overrides.get("requested_cover_gsm"), nested.get("requested_cover_gsm"), calculator_inputs.get("requested_cover_gsm")),
            "requested_insert_paper_category": _pick(overrides.get("requested_insert_paper_category"), nested.get("requested_insert_paper_category"), calculator_inputs.get("requested_insert_paper_category")),
            "requested_insert_gsm": _pick(overrides.get("requested_insert_gsm"), nested.get("requested_insert_gsm"), calculator_inputs.get("requested_insert_gsm")),
            "cover_lamination": _pick(overrides.get("cover_lamination"), nested.get("cover_lamination"), calculator_inputs.get("cover_lamination")),
            "binding_type": _pick(overrides.get("binding_type"), nested.get("binding_type"), calculator_inputs.get("binding_type")),
            "material_type": _pick(overrides.get("material_type"), nested.get("material_type"), calculator_inputs.get("material_type")),
            "product_subtype": _pick(overrides.get("product_subtype"), nested.get("product_subtype"), calculator_inputs.get("product_subtype")),
            "width_mm": _pick(overrides.get("width_mm"), nested.get("width_mm"), calculator_inputs.get("width_mm"), nested.get("custom_width_mm"), calculator_inputs.get("custom_width_mm")),
            "height_mm": _pick(overrides.get("height_mm"), nested.get("height_mm"), calculator_inputs.get("height_mm"), nested.get("custom_height_mm"), calculator_inputs.get("custom_height_mm")),
        }
        for key in ("lamination", "cover_lamination"):
            if payload.get(key) and not is_empty_finishing(payload.get(key)):
                payload[key] = normalize_finishing_slug(payload[key])
        return {key: value for key, value in payload.items() if value not in (None, "", [])}

    def _production_specs_snapshot(self, job: ManagedJob) -> dict[str, object]:
        request_snapshot = self._request_snapshot(getattr(job, "source_quote_request", None))
        operational_snapshot = job.operational_snapshot if isinstance(job.operational_snapshot, dict) else {}

        def _first_value(*values):
            for value in values:
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                return value
            return None

        def _label(raw):
            if raw is None:
                return None
            return str(raw).replace("_", " ").replace("-", " ").strip().title()

        paper_name = _first_value(request_snapshot.get("paper_label"), request_snapshot.get("paper_stock"))
        paper_gsm = request_snapshot.get("requested_gsm")
        paper_label = None
        if paper_name and paper_gsm:
            paper_label = f"{paper_name} ({paper_gsm}gsm)"
        elif paper_name:
            paper_label = str(paper_name)
        elif paper_gsm:
            paper_label = f"{paper_gsm}gsm stock"

        finishing = _first_value(request_snapshot.get("lamination_label"), _label(request_snapshot.get("lamination")))
        notes = _first_value(
            operational_snapshot.get("needs_confirmation"),
            request_snapshot.get("custom_brief"),
            getattr(getattr(job, "source_quote_request", None), "notes", ""),
        )
        if isinstance(notes, list):
            notes = ", ".join(str(item).strip() for item in notes if str(item).strip())

        return {
            "product": _first_value(
                request_snapshot.get("product_label"),
                _label(request_snapshot.get("product_type")),
                job.title,
            ),
            "quantity": request_snapshot.get("quantity"),
            "size": _first_value(request_snapshot.get("finished_size"), request_snapshot.get("size_label")),
            "paper": paper_label,
            "print_sides": _first_value(request_snapshot.get("print_sides_label"), _label(request_snapshot.get("print_sides"))),
            "color_mode": _first_value(request_snapshot.get("color_mode_label"), _label(request_snapshot.get("color_mode"))),
            "finishing": finishing,
            "notes": notes,
            "matched_specs": operational_snapshot.get("matched_specs") or [],
        }

    def _client_tracking_payload(self, job: ManagedJob | None) -> dict[str, object | None]:
        if not job:
            return {
                "tracking_token": None,
                "public_token": None,
            }
        return {
            "tracking_token": str(job.tracking_token) if getattr(job, "tracking_token", None) else None,
            "public_token": None,
        }

    def _quote_row(self, quote_request: QuoteRequest, *, request=None) -> dict[str, object]:
        serialized = QuoteRequestReadSerializer(quote_request, context={"request": request}).data
        latest_response = quote_request.quotes.exclude(status=Quote.PENDING).select_related("financial_split").order_by("-created_at", "-id").first()
        latest_response_payload = serialized.get("latest_response")
        customer_pricing = _quote_customer_pricing_payload(latest_response)
        if latest_response_payload and customer_pricing:
            latest_response_payload = dict(latest_response_payload)
            response_snapshot = dict(latest_response_payload.get("response_snapshot") or {})
            response_snapshot["customer_pricing"] = {
                **dict(response_snapshot.get("customer_pricing") or {}),
                **customer_pricing,
            }
            latest_response_payload["response_snapshot"] = response_snapshot
        row = {
            "id": quote_request.id,
            "reference": _quote_request_reference(quote_request),
            "quote_request_reference": _quote_request_reference(quote_request),
            "quote_reference": _quote_reference(latest_response),
            "status": serialized.get("status") or quote_request.status,
            "status_label": serialized.get("status_label") or quote_request.status,
            "customer_name": quote_request.customer_name or "Client",
            "shop_name": getattr(quote_request.shop, "name", "") or "Awaiting production match",
            "assigned_manager": serialized.get("assigned_manager"),
            "assigned_manager_name": (serialized.get("assigned_manager") or {}).get("display_name") or "",
            "request_snapshot": serialized.get("request_snapshot") or {},
            "attachments": serialized.get("attachments") or [],
            "latest_response": latest_response_payload,
            "created_at": quote_request.created_at,
            "updated_at": quote_request.updated_at,
        }
        managed_job = quote_request.managed_jobs.order_by("-id").first()
        row["managed_job"] = {
            "id": managed_job.id,
            "job_reference": managed_job.managed_reference,
            "quote_request_reference": _quote_request_reference(quote_request),
            "quote_reference": _quote_reference(getattr(managed_job, "source_quote", None)),
            **self._client_tracking_payload(managed_job),
        } if managed_job else None
        return row

    def _job_row(self, job: ManagedJob, *, role: str) -> dict[str, object]:
        artwork_state = managed_job_artwork_state(managed_job=job)
        assignment = job.assignments.filter(reassigned_from__isnull=True).order_by("-id").first()
        row = {
            "id": job.id,
            "reference": job.managed_reference,
            "job_reference": job.managed_reference,
            "quote_request_reference": _quote_request_reference(getattr(job, "source_quote_request", None)),
            "quote_reference": _quote_reference(getattr(job, "source_quote", None)),
            "tracking_reference": job.managed_reference or (str(job.tracking_token) if getattr(job, "tracking_token", None) else ""),
            "production_assignment_reference": _assignment_reference(assignment),
            "title": job.title or "Managed print job",
            "status": job.status,
            "payment_status": job.payment_status,
            "assignment_status": job.assignment_status,
            "requested_deadline": job.requested_deadline,
            "updated_at": job.updated_at,
            **artwork_state,
            "artwork_confirmation": get_artwork_confirmation_payload(job),
            "payment_confirmed": str(job.payment_status or "").lower() in {"confirmed", "release_ready", "released"},
            "pricing": _job_pricing_snapshot(job, role),
        }
        if role == CANONICAL_CLIENT_ROLE:
            row.update(self._client_tracking_payload(job))
        if role == CANONICAL_PARTNER_ROLE:
            row["client_name"] = getattr(job.client, "name", "") or getattr(job.client, "email", "") or "Client"
            row["assigned_shop_name"] = getattr(job.assigned_shop, "name", "") or "Awaiting assignment"
        if role == CANONICAL_PRODUCTION_ROLE:
            row["assigned_shop_name"] = getattr(job.assigned_shop, "name", "") or "Production Shop"
            row["specs"] = self._production_specs_snapshot(job)
            row["source"] = _job_source(job)
        return row


class ClientQuoteListView(BaseRoleDetailView):
    dashboard_role = "client"
    allowed_roles = (CANONICAL_CLIENT_ROLE,)

    def get(self, request):
        rows = (
            QuoteRequest.objects.filter(Q(created_by=request.user) | Q(on_behalf_of=request.user))
            .select_related("shop", "assigned_manager")
            .distinct()
            .order_by("-updated_at", "-created_at")
        )
        return Response({"role": "client", "results": [self._quote_row(item, request=request) for item in rows]})


class ClientQuoteDetailView(BaseRoleDetailView):
    dashboard_role = "client"
    allowed_roles = (CANONICAL_CLIENT_ROLE,)

    def get(self, request, pk):
        quote_request = get_object_or_404(
            QuoteRequest.objects.select_related("shop", "on_behalf_of", "assigned_manager"),
            Q(created_by=request.user) | Q(on_behalf_of=request.user),
            pk=pk,
        )
        return Response({"role": "client", "quote": ClientQuoteRequestDetailSerializer(quote_request, context={"request": request}).data})


class ClientJobListView(BaseRoleDetailView):
    dashboard_role = "client"
    allowed_roles = (CANONICAL_CLIENT_ROLE,)

    def get(self, request):
        jobs = ManagedJob.objects.filter(Q(client=request.user) | Q(created_by=request.user)).select_related("assigned_shop").order_by("-updated_at", "-created_at").distinct()
        return Response({"role": "client", "results": [self._job_row(job, role=CANONICAL_CLIENT_ROLE) for job in jobs]})


class ClientJobDetailView(BaseRoleDetailView):
    dashboard_role = "client"
    allowed_roles = (CANONICAL_CLIENT_ROLE,)

    def get(self, request, pk):
        job = get_object_or_404(
            ManagedJob.objects.select_related("assigned_shop"),
            Q(client=request.user) | Q(created_by=request.user),
            pk=pk,
        )
        return Response(
            {
                "role": "client",
                "job": self._job_row(job, role=CANONICAL_CLIENT_ROLE),
                "settlement": None,
            }
        )


class ClientPaymentListView(BaseRoleDetailView):
    dashboard_role = "client"
    allowed_roles = (CANONICAL_CLIENT_ROLE,)

    def get(self, request):
        jobs = ManagedJob.objects.filter(Q(client=request.user) | Q(created_by=request.user))
        payments = Payment.objects.filter(managed_job__in=jobs).select_related("managed_job").order_by("-created_at")
        return Response(
            {
                "role": "client",
                "results": [
                    {
                        "id": payment.id,
                        "reference": getattr(payment.managed_job, "managed_reference", ""),
                        "job_reference": getattr(payment.managed_job, "managed_reference", ""),
                        "payment_reference": payment.account_reference or f"Payment {payment.id}",
                        "amount": str(payment.amount) if payment.amount is not None else None,
                        "payment_status": payment.status,
                        "channel": payment.method,
                        "created_at": payment.created_at,
                    }
                    for payment in payments
                ],
            }
        )


class ManagerCapablePartnerQuoteView(BaseRoleDetailView):
    def has_dashboard_access(self, user) -> bool:
        return super().has_dashboard_access(user) or (
            has_capability(user, "can_manage_clients")
            or has_capability(user, "can_source_jobs")
        )


class PartnerQuoteListDetailView(ManagerCapablePartnerQuoteView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get_queryset(self, request):
        return QuoteRequest.objects.filter(
            Q(created_by=request.user) | Q(assigned_manager=request.user) | Q(managed_jobs__broker=request.user)
        ).select_related("shop", "on_behalf_of", "assigned_manager").distinct().order_by("-updated_at", "-created_at")

    def get(self, request, pk=None):
        if pk is not None:
            quote_request = get_object_or_404(self.get_queryset(request), pk=pk)
            latest_response = quote_request.quotes.exclude(status=Quote.PENDING).select_related("shop").order_by("-created_at", "-id").first()
            payload = self._quote_row(quote_request, request=request)
            payload["client_name"] = quote_request.customer_name or getattr(quote_request.on_behalf_of, "name", "") or "Client"
            payload["client_email"] = quote_request.customer_email or getattr(quote_request.on_behalf_of, "email", "")
            payload["client_phone"] = quote_request.customer_phone
            payload["on_behalf_of_user_id"] = quote_request.on_behalf_of_id
            payload["latest_response"] = QuoteResponseReadSerializer(latest_response, context={"request": request}).data if latest_response else None
            managed_job = quote_request.managed_jobs.select_related("assigned_shop").order_by("-id").first()
            if managed_job:
                payload["managed_job"] = self._job_row(managed_job, role=CANONICAL_PARTNER_ROLE)
            return Response({"role": "partner", "quote": payload})
        return Response({"role": "partner", "results": [self._quote_row(item, request=request) for item in self.get_queryset(request)]})


class PartnerQuoteSendToClientView(ManagerCapablePartnerQuoteView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get_queryset(self, request):
        return QuoteRequest.objects.filter(
            Q(created_by=request.user) | Q(assigned_manager=request.user) | Q(managed_jobs__broker=request.user)
        ).select_related("shop", "on_behalf_of", "assigned_manager").distinct()

    def post(self, request, pk):
        quote_request = get_object_or_404(self.get_queryset(request), pk=pk)
        request_snapshot = dict(quote_request.request_snapshot or {})
        pending_client = dict(request_snapshot.get("pending_client") or {})
        if quote_request.on_behalf_of_id is None and pending_client.get("client_user_id"):
            quote_request.on_behalf_of_id = pending_client["client_user_id"]
            quote_request.customer_name = pending_client.get("name") or quote_request.customer_name
            quote_request.customer_email = pending_client.get("email") or quote_request.customer_email
            quote_request.customer_phone = pending_client.get("phone") or quote_request.customer_phone
            quote_request.request_snapshot = request_snapshot
            quote_request.save(
                update_fields=["on_behalf_of", "customer_name", "customer_email", "customer_phone", "request_snapshot", "updated_at"]
            )
        if quote_request.on_behalf_of_id is None:
            return Response({"detail": "client_id is required for partner quote requests."}, status=400)
        latest_response = quote_request.quotes.order_by("-created_at", "-id").first()
        if latest_response is None or latest_response.total is None:
            return Response({"detail": "A production base quote is required before sending to the client."}, status=400)

        production_cost_inputs = (
            request.data.get("production_cost_inputs")
            or request_snapshot.get("production_cost_inputs")
            or dict(latest_response.response_snapshot or {}).get("production_cost_inputs")
        )
        quantity_pricing_snapshot = None
        if isinstance(production_cost_inputs, dict):
            gross_margin_type = "quantity_tier"
            try:
                canonical_pricing = calculate_client_price_with_waste_setup_and_quantity_tier(production_cost_inputs)
            except Exception as exc:
                return Response({"detail": str(exc)}, status=400)
            base_price = Decimal(str(canonical_pricing["production_cost"]))
            broker_client_price = Decimal(str(canonical_pricing["final_client_price"]))
            financial_split = calculate_financial_split(
                production_cost=base_price,
                broker_client_price=broker_client_price,
            )
            quantity_pricing_snapshot = {
                "raw_sheets": canonical_pricing["raw_sheets"],
                "fixed_waste_sheets": canonical_pricing["fixed_waste_sheets"],
                "variable_waste_sheets": canonical_pricing["variable_waste_sheets"],
                "billable_sheets": canonical_pricing["billable_sheets"],
                "setup_cost": str(canonical_pricing["setup_cost"]),
                "production_cost": str(canonical_pricing["production_cost"]),
                "volume_multiplier": str(canonical_pricing["volume_multiplier"]),
                "minimum_order_floor": str(canonical_pricing["minimum_order_floor"]),
                "final_client_price": str(canonical_pricing["final_client_price"]),
            }
        else:
            gross_margin_type = str(request.data.get("broker_margin_type") or "percent").strip().lower()
            default_broker_percent = Decimal("75.00")
            gross_margin_value = Decimal(str(request.data.get("broker_margin_value") or default_broker_percent))
            base_price = Decimal(str(latest_response.total))

            if gross_margin_type == "fixed":
                try:
                    validate_partner_markup_amount(base_price=base_price, markup_amount=gross_margin_value)
                except ValueError as exc:
                    return Response({"detail": str(exc)}, status=400)
                gross_margin_amount = gross_margin_value.quantize(Decimal("0.01"))
            else:
                rate = Decimal(str(request.data.get("broker_margin_value") or default_broker_percent)).quantize(Decimal("0.01"))
                try:
                    validate_partner_markup_amount(
                        base_price=base_price,
                        markup_amount=(base_price * rate / Decimal("100")).quantize(Decimal("0.01")),
                    )
                except ValueError as exc:
                    return Response({"detail": str(exc)}, status=400)
                gross_margin_amount = (base_price * rate / Decimal("100")).quantize(Decimal("0.01"))

            financial_split = calculate_financial_split(
                production_cost=base_price,
                broker_client_price=base_price + gross_margin_amount,
            )
        gross_margin_percent = ((financial_split["gross_margin"] / base_price) * Decimal("100")).quantize(Decimal("0.01")) if base_price > 0 else Decimal("0.00")

        response_snapshot = dict(latest_response.response_snapshot or {})
        response_snapshot["customer_pricing"] = {
            "production_cost": str(financial_split["production_cost"]),
            "gross_margin_type": gross_margin_type,
            "gross_margin_percent": str(gross_margin_percent),
            "gross_margin": str(financial_split["gross_margin"]),
            "broker_margin_amount": str(financial_split["gross_margin"]),
            "printer_side_fee": str(financial_split["printer_side_fee"]),
            "broker_margin_fee": str(financial_split["broker_margin_fee"]),
            "printy_fee": str(financial_split["printy_fee"]),
            "shop_payout": str(financial_split["shop_payout"]),
            "broker_payout": str(financial_split["broker_payout"]),
            "final_client_price": str(financial_split["client_total"]),
        }
        if quantity_pricing_snapshot:
            response_snapshot["quantity_pricing_snapshot"] = quantity_pricing_snapshot
        response_snapshot["pricing"] = {**dict(response_snapshot.get("pricing") or {}), "grand_total": str(financial_split["client_total"])}
        response_snapshot["totals"] = {**dict(response_snapshot.get("totals") or {}), "grand_total": str(financial_split["client_total"])}
        if latest_response.status == Quote.PENDING:
            latest_response = update_quote_response(
                response=latest_response,
                status=Quote.SENT,
                response_snapshot=response_snapshot,
                total=financial_split["client_total"],
                note=str(request.data.get("note") or latest_response.note or "Partner quote prepared in Printy."),
            )
        else:
            latest_response.response_snapshot = response_snapshot
            latest_response.sent_at = latest_response.sent_at or timezone.now()
        latest_response.expires_at = calculate_quote_expiry(sent_at=latest_response.sent_at)
        latest_response.sent_to_client_at = timezone.now()
        latest_response.sent_to_client_by = request.user
        latest_response.client_quote_status = "sent"
        latest_response.save(
            update_fields=[
                "response_snapshot",
                "sent_at",
                "expires_at",
                "sent_to_client_at",
                "sent_to_client_by",
                "client_quote_status",
                "updated_at",
            ]
        )
        if not getattr(latest_response, "financial_split", None):
            create_quote_financial_split(
                quote=latest_response,
                production_cost=financial_split["production_cost"],
                broker_client_price=financial_split["broker_client_price"],
                policy=financial_split["policy"],
            )

        request_snapshot["customer_pricing"] = response_snapshot["customer_pricing"]
        quote_request.request_snapshot = request_snapshot
        quote_request.save(update_fields=["request_snapshot", "updated_at"])
        client_recipient = quote_request.on_behalf_of or quote_request.created_by
        if client_recipient and getattr(client_recipient, "id", None) != request.user.id:
            notify_quote_event(
                recipient=client_recipient,
                notification_type=Notification.SHOP_QUOTE_SENT,
                message=f"Your quote request #{quote_request.id} has a new quote from Printy.",
                object_type="quote_request",
                object_id=quote_request.id,
                actor=request.user,
            )

        return Response(
            {
                "quote_request_id": quote_request.id,
                "quote_id": latest_response.id,
                "pricing": response_snapshot["customer_pricing"],
            }
        )


class PartnerQuoteAttachClientView(BaseRoleDetailView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get_queryset(self, request):
        return QuoteRequest.objects.filter(
            Q(created_by=request.user) | Q(assigned_manager=request.user) | Q(managed_jobs__broker=request.user)
        ).select_related("shop", "on_behalf_of", "assigned_manager").distinct()

    @transaction.atomic
    def patch(self, request, pk):
        quote_request = get_object_or_404(self.get_queryset(request), pk=pk)
        if quote_request.status != "draft":
            return Response({"detail": "Only draft partner quotes can attach a client."}, status=400)
        serializer = PartnerQuoteAttachClientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = _resolve_or_create_partner_client(
                partner_user=request.user,
                client_user=serializer.validated_data.get("client_user"),
                client_name=serializer.validated_data.get("client_name", ""),
                client_email=serializer.validated_data.get("client_email", ""),
                client_phone=serializer.validated_data.get("client_phone", ""),
                client_company=serializer.validated_data.get("client_company", ""),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

        request_snapshot = dict(quote_request.request_snapshot or {})
        request_snapshot["pending_client"] = {
            "client_user_id": payload["client_id"],
            "name": payload["name"],
            "email": payload["email"],
            "phone": payload["phone"],
            "company": payload["company"],
        }
        request_details = dict(request_snapshot.get("request_details") or {})
        request_details.update(
            {
                "customer_name": payload["name"],
                "customer_email": payload["email"],
                "customer_phone": payload["phone"],
                "client_company": payload["company"],
            }
        )
        request_snapshot["request_details"] = request_details
        quote_request.customer_name = payload["name"] or quote_request.customer_name
        quote_request.customer_email = payload["email"] or quote_request.customer_email
        quote_request.customer_phone = payload["phone"] or quote_request.customer_phone
        quote_request.request_snapshot = request_snapshot
        quote_request.save(update_fields=["customer_name", "customer_email", "customer_phone", "request_snapshot", "updated_at"])
        return Response(
            {
                "quote_request_id": quote_request.id,
                "client_id": payload["client_id"],
                "client_name": payload["name"],
                "client_email": payload["email"],
                "client_phone": payload["phone"],
                "client_company": payload["company"],
            }
        )


class PartnerAssignedRequestQuoteCreateView(ManagerCapablePartnerQuoteView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get_queryset(self, request):
        return QuoteRequest.objects.filter(
            Q(created_by=request.user) | Q(assigned_manager=request.user) | Q(managed_jobs__broker=request.user)
        ).select_related("shop", "on_behalf_of", "assigned_manager").distinct()

    def post(self, request, pk):
        quote_request = get_object_or_404(self.get_queryset(request), pk=pk)
        if quote_request.assigned_manager_id != request.user.id:
            raise PermissionDenied("You cannot respond to this quote request.")
        serializer = PartnerQuotePreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = respond_to_assigned_quote_request(
                partner_user=request.user,
                quote_request=quote_request,
                shop=serializer.validated_data["shop"],
                pricing_snapshot=serializer.validated_data["pricing_snapshot"],
                partner_markup=serializer.validated_data["partner_markup"],
                note=str(request.data.get("note") or "").strip(),
            )
        except ValueError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return Response(
            {
                "role": "partner",
                "quote_request_id": payload["quote_request"].id,
                "quote": QuoteResponseReadSerializer(payload["quote"], context={"request": request}).data,
                "partner_preview": payload["preview"],
            },
            status=201,
        )


class PartnerAssignedRequestShopOptionsView(ManagerCapablePartnerQuoteView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def has_dashboard_access(self, user) -> bool:
        return bool(getattr(user, "is_staff", False) or getattr(user, "is_superuser", False) or super().has_dashboard_access(user))

    def get_queryset(self, request):
        if getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False):
            return QuoteRequest.objects.select_related("shop", "on_behalf_of", "assigned_manager").distinct()
        return QuoteRequest.objects.filter(
            Q(created_by=request.user) | Q(assigned_manager=request.user) | Q(managed_jobs__broker=request.user)
        ).select_related("shop", "on_behalf_of", "assigned_manager").distinct()

    def post(self, request, pk):
        quote_request = get_object_or_404(self.get_queryset(request), pk=pk)
        if quote_request.assigned_manager_id != request.user.id and not (
            getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False)
        ):
            raise PermissionDenied("You cannot access production options for this quote request.")
        serializer = PartnerAssignedRequestShopOptionsSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        payload = build_partner_production_matches(
            self._assigned_request_match_payload(quote_request, serializer.validated_data)
        )
        return Response(PartnerProductionMatchResponseSerializer(payload).data)


class ManagerQuoteRequestPrefillView(PartnerAssignedRequestShopOptionsView):
    def get(self, request, pk):
        quote_request = get_object_or_404(self.get_queryset(request), pk=pk)
        if quote_request.assigned_manager_id != request.user.id and not (
            getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False)
        ):
            raise PermissionDenied("You cannot access this quote request.")
        payload = self._assigned_request_match_payload(quote_request)
        size = _dashboard_size(payload)
        lamination = payload.get("lamination")
        finishing = []
        if lamination and not is_empty_finishing(lamination):
            finishing.append({"type": "lamination", "slug": normalize_finishing_slug(lamination), "sides": "both"})
        return Response(
            {
                "quote_request_id": quote_request.id,
                "product_type": payload.get("product_type"),
                "product_variant": payload.get("product_subtype") or "standard",
                "quantity": _dashboard_int(payload.get("quantity")),
                "size": size,
                "paper": {
                    "gsm": _dashboard_int(payload.get("requested_gsm")),
                    "type": payload.get("requested_paper_category") or payload.get("paper_stock") or "",
                    "tier": payload.get("paper_tier") or "",
                },
                "print": {
                    "sides": _dashboard_sides(payload.get("print_sides")),
                    "color_mode": _dashboard_color_mode(payload.get("color_mode")),
                },
                "finishing": finishing,
                "turnaround": payload.get("urgency_type") or "standard",
                "client_notes": quote_request.notes or self._request_snapshot(quote_request).get("custom_brief") or "",
                "uploaded_artwork_url": self._request_snapshot_root(quote_request).get("uploaded_artwork_url") or "",
                "builder_payload": payload,
            }
        )


class ManagerQuoteRequestPricingPreviewView(PartnerAssignedRequestShopOptionsView):
    def post(self, request, pk):
        quote_request = get_object_or_404(self.get_queryset(request), pk=pk)
        if quote_request.assigned_manager_id != request.user.id and not (
            getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False)
        ):
            raise PermissionDenied("You cannot access production pricing for this quote request.")
        specs = request.data.get("specs") if isinstance(request.data.get("specs"), dict) else {}
        payload = self._assigned_request_match_payload(quote_request, specs)
        response = build_partner_production_matches(payload)
        rows = response.get("results") or []
        selected_shop_id = _dashboard_int(request.data.get("shop_id"))
        selected = None
        if selected_shop_id:
            selected = next((row for row in rows if row.get("shop_id") == selected_shop_id), None)
        selected = selected or next((row for row in rows if row.get("price_status") == "priced"), None)

        manager_breakdown = None
        if selected and selected.get("preview_snapshot"):
            production = production_breakdown_from_preview(selected["preview_snapshot"])
            production_cost = Decimal(production["production_cost"])
            markup_pct = Decimal(str(request.data.get("markup_pct") or "75")).quantize(Decimal("0.01"))
            markup_amount = (production_cost * markup_pct / Decimal("100")).quantize(Decimal("0.01"))
            try:
                split = calculate_financial_split(
                    production_cost=production_cost,
                    broker_client_price=production_cost + markup_amount,
                )
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=400)
            manager_breakdown = {
                **production,
                "markup_pct": str(markup_pct),
                "markup_amount": str(split["gross_margin"]),
                "broker_client_price": str(split["broker_client_price"]),
                "platform_fee": str(split["printy_fee"]),
                "client_total": str(split["client_total"]),
            }

        eligible_shops = []
        for row in rows:
            item = {
                "id": row.get("shop_id"),
                "shop_id": row.get("shop_id"),
                "name": row.get("shop_name"),
                "location": row.get("shop_location"),
                "eligible": row.get("price_status") == "priced",
                "production_cost": row.get("production_cost"),
                "ineligible_reason": "" if row.get("price_status") == "priced" else row.get("reason"),
            }
            preview = row.get("preview_snapshot")
            if preview:
                item.update(production_breakdown_from_preview(preview))
            eligible_shops.append(item)

        return Response(
            {
                "quote_request_id": quote_request.id,
                "selected_shop_id": selected.get("shop_id") if selected else None,
                "breakdown": manager_breakdown,
                "eligible_shops": eligible_shops,
                "missing_fields": response.get("missing_fields") or [],
            }
        )


class PrintShopJobBreakdownView(BaseRoleDetailView):
    dashboard_role = "production"
    allowed_roles = (CANONICAL_PRODUCTION_ROLE,)

    def get_queryset(self, request):
        return (
            ManagedJob.objects.filter(_production_shop_filter(request.user))
            .select_related("assigned_shop", "source_quote", "source_quote__shop", "source_quote_request")
            .distinct()
        )

    def get(self, request, job_id):
        job = get_object_or_404(self.get_queryset(request), pk=job_id)
        shop = job.assigned_shop or getattr(getattr(job, "source_quote", None), "shop", None)
        if shop is None or getattr(shop, "owner_id", None) != request.user.id:
            raise PermissionDenied("Not authorized to view this job.")
        if job.source_quote_request_id:
            payload = self._assigned_request_match_payload(job.source_quote_request)
        else:
            payload = job.operational_snapshot if isinstance(job.operational_snapshot, dict) else {}
        payload["shop_id"] = shop.id
        response = build_partner_production_matches(payload)
        row = next((item for item in response.get("results") or [] if item.get("shop_id") == shop.id), None)
        eligible = bool(row and row.get("price_status") == "priced")
        preview = row.get("preview_snapshot") if row else None
        production = production_breakdown_from_preview(preview or {})
        size = _dashboard_size(payload)
        lamination = payload.get("lamination")
        finishing = []
        if lamination and not is_empty_finishing(lamination):
            finishing.append({"type": "lamination", "slug": normalize_finishing_slug(lamination), "sides": "both"})
        return Response(
            {
                "job_id": job.id,
                "specs": {
                    "product_type": payload.get("product_type"),
                    "quantity": _dashboard_int(payload.get("quantity")),
                    "size": size,
                    "paper": {
                        "gsm": _dashboard_int(payload.get("requested_gsm")),
                        "type": payload.get("requested_paper_category") or payload.get("paper_stock") or "",
                    },
                    "print": {
                        "sides": _dashboard_sides(payload.get("print_sides")),
                        "color_mode": _dashboard_color_mode(payload.get("color_mode")),
                    },
                    "finishing": finishing,
                    "turnaround": payload.get("urgency_type") or "standard",
                },
                "imposition": {
                    "pieces_per_sheet": production.get("pieces_per_sheet"),
                    "sheets_needed": production.get("sheets_needed"),
                },
                "eligibility": {
                    "eligible": eligible,
                    "missing_capabilities": [] if eligible else ((row or {}).get("missing_requirements") or ["pricing"]),
                },
                "breakdown": {
                    "line_items": production.get("line_items") or [],
                    "production_cost": production.get("production_cost"),
                },
            }
        )


class PartnerJobListDetailView(BaseRoleDetailView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get_queryset(self, request):
        return ManagedJob.objects.filter(broker=request.user).select_related("client", "assigned_shop").order_by("-updated_at", "-created_at")

    def get(self, request, pk=None):
        if pk is not None:
            job = self.get_queryset(request).get(pk=pk)
            artwork_state = managed_job_artwork_state(managed_job=job)
            if artwork_state["artwork_missing"] and not artwork_state["artwork_reminder_sent"]:
                notify_missing_artwork(managed_job=job, actor=request.user, source="manager_requested_artwork")
                job.refresh_from_db()
            return Response(
                {
                    "role": "partner",
                    "job": self._job_row(job, role=CANONICAL_PARTNER_ROLE),
                    "settlement": get_financial_split_for_job(job),
                }
            )
        return Response({"role": "partner", "results": [self._job_row(job, role=CANONICAL_PARTNER_ROLE) for job in self.get_queryset(request)]})


class PartnerJobDispatchView(BaseRoleDetailView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get_queryset(self, request):
        return ManagedJob.objects.filter(broker=request.user).select_related(
            "client",
            "assigned_shop",
            "source_quote",
            "source_quote__shop",
            "source_quote__shop__owner",
        )

    def post(self, request, pk):
        job = get_object_or_404(self.get_queryset(request), pk=pk)
        if job.payment_status not in {"confirmed", "release_ready"} and job.status != "payment_confirmed":
            return Response(
                {
                    "error": "payment_required",
                    "detail": "Client payment must be confirmed before dispatch.",
                },
                status=400,
            )
        if job.dispatched_at is not None:
            return Response({"detail": "This job has already been dispatched."}, status=400)
        source_quote = job.source_quote
        if source_quote is None or getattr(source_quote, "shop_id", None) is None:
            return Response(
                {
                    "error": "no_shop_selected",
                    "detail": "Select a production shop before dispatch.",
                },
                status=400,
            )
        missing_specs = self._dispatch_missing_specs(job)
        if missing_specs:
            return Response(
                {
                    "error": "missing_specs",
                    "detail": "Required production specs must be confirmed before dispatch.",
                    "missing_fields": missing_specs,
                },
                status=400,
            )
        if not managed_job_has_artwork(managed_job=job):
            job.artwork_required = True
            job.save(update_fields=["artwork_required", "updated_at"])
            notify_missing_artwork(managed_job=job, actor=request.user, source="dispatch_attempt")
            return Response(
                {
                    "error": "artwork_required",
                    "detail": "Artwork required before dispatch. Client has been notified.",
                    "client_notified": True,
                },
                status=400,
            )
        try:
            require_artwork_confirmation_dispatch_ready(job)
        except ValidationError as exc:
            return Response(
                {
                    "error": "artwork_confirmation_required",
                    "detail": str(exc),
                    "artwork_confirmation": get_artwork_confirmation_payload(job),
                },
                status=400,
            )

        job.assigned_shop = source_quote.shop
        job.dispatched_at = timezone.now()
        job.dispatched_by = request.user
        if job.assignment_status == "unassigned":
            job.assignment_status = "assignment_pending"
        job.save(update_fields=["assigned_shop", "dispatched_at", "dispatched_by", "assignment_status", "updated_at"])
        assignment = create_assignment_for_managed_job(managed_job=job, quote=source_quote)
        production_recipient = getattr(source_quote.shop, "owner", None)
        if production_recipient and getattr(production_recipient, "id", None) != request.user.id:
            notify_quote_event(
                recipient=production_recipient,
                notification_type=Notification.JOB_STATUS_UPDATED,
                message=f"{job.managed_reference or 'Managed job'} has been dispatched to your production queue.",
                object_type="managed_job",
                object_id=job.id,
                actor=request.user,
            )
        return Response(
            {
                "job_id": job.id,
                "assignment_id": assignment.id,
                "dispatched": True,
                "dispatched_at": job.dispatched_at,
                "assignment_status": job.assignment_status,
                "shop_name": getattr(source_quote.shop, "name", "") or "Production Shop",
                "artwork_verified": True,
            }
        )


class PartnerClientListView(BaseRoleDetailView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get(self, request):
        return Response({"detail": "Partner client CRM is postponed for MVP."}, status=410)

    @transaction.atomic
    def post(self, request):
        return Response({"detail": "Partner client CRM is postponed for MVP."}, status=410)


class PartnerProductionShopListView(BaseRoleDetailView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get(self, request):
        shops = Shop.objects.filter(managed_jobs__broker=request.user).distinct().order_by("name")
        return Response({"role": "partner", "results": [{"id": shop.id, "name": shop.name, "slug": shop.slug} for shop in shops]})


class PartnerPaymentListView(BaseRoleDetailView):
    dashboard_role = "partner"
    allowed_roles = (CANONICAL_PARTNER_ROLE,)

    def get(self, request):
        return Response({"detail": "Job settlement splits are postponed for MVP."}, status=410)


class ProductionJobListDetailView(BaseRoleDetailView):
    dashboard_role = "production"
    allowed_roles = (CANONICAL_PRODUCTION_ROLE,)

    def get_queryset(self, request):
        return (
            ManagedJob.objects.filter(_production_shop_filter(request.user))
            .select_related("assigned_shop", "source_quote", "source_quote_request")
            .distinct()
            .order_by("-operational_priority_level", "-updated_at")
        )

    def get(self, request, pk=None):
        if pk is not None:
            job = self.get_queryset(request).get(pk=pk)
            return Response(
                {
                    "role": "production",
                    "job": self._job_row(job, role=CANONICAL_PRODUCTION_ROLE),
                    "settlement": None,
                }
            )
        return Response({"role": "production", "results": [self._job_row(job, role=CANONICAL_PRODUCTION_ROLE) for job in self.get_queryset(request)]})


class ProductionPricingListView(BaseRoleDetailView):
    dashboard_role = "production"
    allowed_roles = (CANONICAL_PRODUCTION_ROLE,)

    def get(self, request):
        pricing_rows = PrintingRate.objects.filter(shop__owner=request.user).select_related("shop").order_by("shop__name", "sheet_size")[:100]
        return Response(
            {
                "role": "production",
                "results": [
                    {
                        "id": row.id,
                        "shop_name": row.shop.name,
                        "sheet_size": row.sheet_size,
                        "color_mode": row.color_mode,
                        "single_price": str(row.single_price),
                        "double_price": str(row.double_price) if row.double_price is not None else None,
                    }
                    for row in pricing_rows
                ],
            }
        )


class ProductionPaperStockListView(BaseRoleDetailView):
    dashboard_role = "production"
    allowed_roles = (CANONICAL_PRODUCTION_ROLE,)

    def get(self, request):
        papers = Paper.objects.filter(shop__owner=request.user).select_related("shop").order_by("shop__name", "paper_type")[:100]
        return Response(
            {
                "role": "production",
                "results": [
                    {
                        "id": paper.id,
                        "shop_name": paper.shop.name,
                        "paper_type": getattr(paper, "paper_type", ""),
                        "name": getattr(paper, "display_name", "") or getattr(paper, "name", ""),
                        "gsm": paper.gsm,
                        "sheet_size": paper.sheet_size,
                        "is_active": paper.is_active,
                    }
                    for paper in papers
                ],
            }
        )


class ProductionFinishingListView(BaseRoleDetailView):
    dashboard_role = "production"
    allowed_roles = (CANONICAL_PRODUCTION_ROLE,)

    def get(self, request):
        finishings = FinishingRate.objects.filter(shop__owner=request.user).select_related("shop").order_by("shop__name", "name")[:100]
        return Response(
            {
                "role": "production",
                "results": [
                    {
                        "id": item.id,
                        "shop_name": item.shop.name,
                        "name": item.name,
                        "unit": item.charge_unit,
                        "price": str(item.price),
                        "is_active": item.is_active,
                    }
                    for item in finishings
                ],
            }
        )


class ProductionPaymentListView(BaseRoleDetailView):
    dashboard_role = "production"
    allowed_roles = (CANONICAL_PRODUCTION_ROLE,)

    def get(self, request):
        jobs = ManagedJob.objects.filter(_production_shop_filter(request.user)).distinct()
        return Response(
            {
                "role": "production",
                "results": [
                    {
                        "managed_job_id": job.id,
                        "reference": job.managed_reference,
                        "job_reference": job.managed_reference,
                        "quote_request_reference": _quote_request_reference(getattr(job, "source_quote_request", None)),
                        "quote_reference": _quote_reference(getattr(job, "source_quote", None)),
                        "shop_payout": str(split["shop_payout"]) if split and split.get("shop_payout") is not None else None,
                        "status": job.payment_status,
                        "source": split.get("source") if split else None,
                    }
                    for job in jobs
                    for split in [get_financial_split_for_job(job)]
                ],
            }
        )
