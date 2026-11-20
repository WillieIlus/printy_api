"""Stage 1D admin operations: verification, custody, disputes, reporting."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User, UserProfile
from api.admin_audit import last_admin_audit, record_admin_audit
from api.permissions import IsSuperUser
from jobs.audit_services import record_job_status_event
from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobExceptionStatus,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
)
from jobs.models import ManagedJob, ManagedJobPayout
from jobs.payout_services import release_managed_job_payouts
from payments.models import Payment
from quotes.models import Quote, QuoteRequest
from quotes.quote_projection import CANONICAL_STATUSES, canonical_status_for_quote, canonical_status_for_request
from services.pricing.mvp_rate_card import RateCardNotUsable, complete_shop_rate_card_setup
from setup.services import build_rate_card_setup_status
from shops.models import Shop


MANAGER_ROLES = [User.Role.PARTNER, User.Role.BROKER]
ADMIN_PERMISSIONS = [IsAuthenticated, IsSuperUser]
HOLD_PAYMENT_STATUSES = {
    ManagedJobPaymentStatus.CONFIRMED,
    ManagedJobPaymentStatus.RELEASE_READY,
    ManagedJobPaymentStatus.PAYOUT_ON_HOLD,
}


def _money(value) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except Exception:
        return None


def _manager_status(profile: UserProfile) -> str:
    if profile.manager_verified:
        return "verified"
    if not profile.broker_profile_active:
        return "rejected"
    return "pending"


def _manager_row(profile: UserProfile) -> dict:
    user = profile.user
    audit = last_admin_audit(obj=profile)
    return {
        "id": profile.id,
        "user_id": user.id,
        "name": getattr(user, "name", "") or user.email,
        "email": user.email,
        "phone": profile.phone,
        "city": profile.city,
        "speciality": profile.manager_speciality,
        "status": _manager_status(profile),
        "manager_verified": profile.manager_verified,
        "manager_verified_at": profile.manager_verified_at,
        "broker_profile_active": profile.broker_profile_active,
        "last_decision": audit,
    }


def _printer_status(shop: Shop) -> str:
    if not shop.is_active:
        return "rejected"
    setup = getattr(shop, "rate_card_setup", None)
    confirmed = bool(getattr(setup, "is_confirmed", False))
    if shop.is_public and confirmed:
        return "verified"
    return "pending"


def _printer_row(shop: Shop) -> dict:
    setup_status = build_rate_card_setup_status(shop)
    audit = last_admin_audit(obj=shop)
    return {
        "id": shop.id,
        "name": shop.name,
        "slug": shop.slug,
        "owner_id": shop.owner_id,
        "owner_email": getattr(shop.owner, "email", ""),
        "city": shop.city,
        "is_active": shop.is_active,
        "is_public": shop.is_public,
        "is_confirmed": setup_status["is_confirmed"],
        "status": _printer_status(shop),
        "next_step": setup_status["next_step"],
        "missing_steps": setup_status["missing_steps"],
        "setup_status": setup_status,
        "last_decision": audit,
    }


class AdminManagerVerificationListView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    def get(self, request):
        requested = str(request.query_params.get("status") or "all").lower()
        profiles = UserProfile.objects.select_related("user").filter(
            user__role__in=MANAGER_ROLES,
            user__is_active=True,
            is_system_account=False,
        ).order_by("user__email")
        if requested == "pending":
            profiles = profiles.filter(manager_verified=False, broker_profile_active=True)
        elif requested == "verified":
            profiles = profiles.filter(manager_verified=True)
        elif requested == "rejected":
            profiles = profiles.filter(manager_verified=False, broker_profile_active=False)
        rows = [_manager_row(profile) for profile in profiles]
        return Response({"count": len(rows), "results": rows})


class AdminManagerVerificationDecisionView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    @transaction.atomic
    def post(self, request, user_id):
        profile = get_object_or_404(
            UserProfile.objects.select_for_update().select_related("user"),
            user_id=user_id,
            user__role__in=MANAGER_ROLES,
        )
        decision = str(request.data.get("decision") or "").lower()
        reason = str(request.data.get("reason") or "").strip()
        if decision not in {"approve", "reject"}:
            return Response(
                {"detail": "decision must be approve or reject.", "code": "invalid_decision"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if decision == "reject" and not reason:
            return Response(
                {"detail": "A rejection reason is required.", "code": "reason_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if decision == "approve":
            profile.manager_verified = True
            profile.manager_verified_at = timezone.now()
            profile.broker_profile_active = True
        else:
            profile.manager_verified = False
            profile.manager_verified_at = None
            profile.broker_profile_active = False
        profile.save(update_fields=["manager_verified", "manager_verified_at", "broker_profile_active", "updated_at"])
        record_admin_audit(
            user=request.user,
            obj=profile,
            action=f"manager_{decision}",
            reason=reason,
        )
        return Response(_manager_row(profile))


class AdminPrinterVerificationListView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    def get(self, request):
        requested = str(request.query_params.get("status") or "all").lower()
        shops = Shop.objects.select_related("owner").prefetch_related("rate_card_setup").order_by("name")
        if requested in {"pending", "verified", "rejected"}:
            ids = [shop.id for shop in shops if _printer_status(shop) == requested]
            shops = shops.filter(id__in=ids)
        rows = [_printer_row(shop) for shop in shops]
        return Response({"count": len(rows), "results": rows})


class AdminPrinterVerificationDecisionView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    @transaction.atomic
    def post(self, request, shop_id):
        shop = get_object_or_404(
            Shop.objects.select_for_update().select_related("owner"),
            pk=shop_id,
        )
        decision = str(request.data.get("decision") or "").lower()
        reason = str(request.data.get("reason") or "").strip()
        if decision not in {"approve", "reject"}:
            return Response(
                {"detail": "decision must be approve or reject.", "code": "invalid_decision"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if decision == "reject" and not reason:
            return Response(
                {"detail": "A rejection reason is required.", "code": "reason_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if decision == "approve":
            try:
                complete_shop_rate_card_setup(shop)
            except RateCardNotUsable as exc:
                return Response(exc.payload, status=status.HTTP_400_BAD_REQUEST)
            shop.refresh_from_db()
            shop.is_active = True
            shop.is_public = True
            shop.save(update_fields=["is_active", "is_public", "updated_at"])
        else:
            shop.is_active = False
            shop.is_public = False
            shop.save(update_fields=["is_active", "is_public", "updated_at"])
        record_admin_audit(
            user=request.user,
            obj=shop,
            action=f"printer_{decision}",
            reason=reason,
        )
        shop.refresh_from_db()
        return Response(_printer_row(shop))


def _custody_status(job: ManagedJob) -> str:
    if str(job.payment_status) == ManagedJobPaymentStatus.RELEASED:
        return "released"
    if str(job.payment_status) in HOLD_PAYMENT_STATUSES:
        return "held"
    return "awaiting"


def _job_amounts(job: ManagedJob) -> dict:
    split = getattr(getattr(job, "source_quote", None), "financial_split", None)
    assignment = (
        job.assignments.filter(reassigned_from__isnull=True)
        .exclude(status__in=[JobAssignmentStatus.REJECTED, JobAssignmentStatus.CANCELLED])
        .first()
    )
    return {
        "client_total": _money(getattr(job, "client_total", None) or getattr(split, "client_total", None)),
        "manager_payout": _money(getattr(job, "broker_payout", None) or getattr(split, "broker_payout", None)),
        "shop_payout": _money(getattr(assignment, "shop_payout", None) or getattr(split, "shop_payout", None)),
        "printy_fee": _money(getattr(job, "printy_fee", None) or getattr(split, "printy_fee", None)),
    }


def _payouts(job: ManagedJob) -> list[dict]:
    return [
        {
            "id": payout.id,
            "recipient_role": payout.recipient_role,
            "amount": _money(payout.amount),
            "currency": payout.currency,
            "status": payout.status,
            "released_at": payout.released_at,
        }
        for payout in job.payouts.all()
    ]


def _custody_row(job: ManagedJob) -> dict:
    return {
        "job_id": job.id,
        "code": job.managed_reference,
        "status": job.status,
        "payment_status": job.payment_status,
        "custody_status": _custody_status(job),
        "payout_hold": bool(job.payout_hold),
        "amounts": _job_amounts(job),
        "payouts": _payouts(job),
        "can_release": (
            str(job.status) == ManagedJobStatus.COMPLETED
            and str(job.payment_status) != ManagedJobPaymentStatus.RELEASED
            and not job.payout_hold
        ),
        "updated_at": job.updated_at,
    }


class AdminCustodyListView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    def get(self, request):
        jobs = (
            ManagedJob.objects.select_related("source_quote", "assigned_shop", "broker")
            .prefetch_related("assignments", "payouts")
            .filter(
                payment_status__in=[
                    ManagedJobPaymentStatus.CONFIRMED,
                    ManagedJobPaymentStatus.RELEASE_READY,
                    ManagedJobPaymentStatus.PAYOUT_ON_HOLD,
                    ManagedJobPaymentStatus.RELEASED,
                ]
            )
            .order_by("-updated_at")
        )
        rows = [_custody_row(job) for job in jobs]
        return Response({"count": len(rows), "results": rows})


class AdminCustodyReleaseView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    @transaction.atomic
    def post(self, request, job_id):
        job = get_object_or_404(
            ManagedJob.objects.select_for_update().select_related("source_quote", "assigned_shop", "broker"),
            pk=job_id,
        )
        if str(job.status) != ManagedJobStatus.COMPLETED:
            return Response(
                {"detail": "Custody can only be released for a completed job.", "code": "not_completed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if job.payout_hold:
            return Response(
                {"detail": "Payout is on hold for this job.", "code": "payout_hold"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            result = release_managed_job_payouts(managed_job=job, released_by=request.user)
        except Exception as exc:
            if hasattr(exc, "messages"):
                detail = "; ".join(exc.messages)
            else:
                detail = str(exc)
            return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)
        record_admin_audit(
            user=request.user,
            obj=job,
            action="custody_release",
            reason=str(request.data.get("reason") or ""),
            metadata={"payout_ids": [payout.id for payout in result["payouts"]]},
        )
        return Response(
            {
                "job_id": job.id,
                "released": len(result["payouts"]),
                "payouts": [
                    {
                        "id": payout.id,
                        "recipient_role": payout.recipient_role,
                        "amount": _money(payout.amount),
                        "status": payout.status,
                    }
                    for payout in result["payouts"]
                ],
                "reconciliation": result.get("reconciliation"),
                "custody_status": "released",
            }
        )


def _dispute_queryset():
    return ManagedJob.objects.filter(
        Q(dispute_open=True)
        | Q(exception_status=ManagedJobExceptionStatus.DISPUTE_OPEN)
        | Q(status=ManagedJobStatus.DISPUTED)
    ).select_related("source_quote", "assigned_shop", "broker", "client")


def _dispute_row(job: ManagedJob) -> dict:
    note_event = (
        job.events.filter(event_type="issue_raised").order_by("-created_at", "-id").first()
    )
    metadata = note_event.metadata if note_event is not None and isinstance(note_event.metadata, dict) else {}
    return {
        "job_id": job.id,
        "code": job.managed_reference,
        "title": job.title,
        "status": job.status,
        "payment_status": job.payment_status,
        "exception_status": job.exception_status,
        "dispute_open": bool(job.dispute_open),
        "disputed_at": job.disputed_at,
        "payout_hold": bool(job.payout_hold),
        "amounts": _job_amounts(job),
        "reason": metadata.get("note", ""),
        "client": getattr(getattr(job, "client", None), "name", "") or "",
        "manager": getattr(getattr(job, "broker", None), "name", "") or "",
        "shop": getattr(getattr(job, "assigned_shop", None), "name", "") or "",
        "updated_at": job.updated_at,
    }


class AdminDisputeListView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    def get(self, request):
        rows = [_dispute_row(job) for job in _dispute_queryset().order_by("-disputed_at", "-updated_at")]
        return Response({"count": len(rows), "results": rows})


class AdminDisputeResolveView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    @transaction.atomic
    def post(self, request, job_id):
        job = get_object_or_404(
            ManagedJob.objects.select_for_update().select_related(
                "source_quote", "assigned_shop", "broker", "client"
            ),
            pk=job_id,
        )
        if not (
            job.dispute_open
            or str(job.exception_status) == ManagedJobExceptionStatus.DISPUTE_OPEN
            or str(job.status) == ManagedJobStatus.DISPUTED
        ):
            return Response(
                {"detail": "This job is not in dispute.", "code": "not_disputed"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resolution = str(request.data.get("resolution") or "").lower()
        reason = str(request.data.get("reason") or "").strip()
        if resolution not in {"release", "dismiss", "refund"}:
            return Response(
                {"detail": "resolution must be release, dismiss or refund.", "code": "invalid_resolution"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if resolution in {"release", "refund"} and not reason:
            return Response(
                {"detail": "A resolution reason is required.", "code": "reason_required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if resolution == "refund" and str(job.payment_status) == ManagedJobPaymentStatus.RELEASED:
            return Response(
                {"detail": "Payouts have already been released; record a refund through ops.", "code": "already_released"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        released = None
        if resolution == "release":
            job.payout_hold = False
            if str(job.status) == ManagedJobStatus.DISPUTED:
                job.status = (
                    ManagedJobStatus.COMPLETED
                    if getattr(job, "delivered_at", None)
                    else ManagedJobStatus.READY
                )
            job.exception_status = ManagedJobExceptionStatus.CLEAR
            job.dispute_open = False
            job.ops_review_required = False
            job.save(update_fields=["status", "exception_status", "dispute_open", "ops_review_required", "payout_hold", "updated_at"])
            result = release_managed_job_payouts(managed_job=job, released_by=request.user)
            released = [
                {"id": payout.id, "recipient_role": payout.recipient_role, "amount": _money(payout.amount)}
                for payout in result["payouts"]
            ]
        elif resolution == "refund":
            job.status = ManagedJobStatus.CANCELLED
            job.payment_status = ManagedJobPaymentStatus.REFUNDED
            job.exception_status = ManagedJobExceptionStatus.CLEAR
            job.dispute_open = False
            job.ops_review_required = False
            job.payout_hold = False
            job.save(update_fields=["status", "payment_status", "exception_status", "dispute_open", "ops_review_required", "payout_hold", "updated_at"])
        else:
            if str(job.status) == ManagedJobStatus.DISPUTED:
                job.status = (
                    ManagedJobStatus.COMPLETED
                    if getattr(job, "delivered_at", None)
                    else ManagedJobStatus.READY
                )
            job.exception_status = ManagedJobExceptionStatus.CLEAR
            job.dispute_open = False
            job.ops_review_required = False
            job.payout_hold = False
            job.save(update_fields=["status", "exception_status", "dispute_open", "ops_review_required", "payout_hold", "updated_at"])

        record_job_status_event(
            managed_job=job,
            event_type="dispute_resolved",
            actor=request.user,
            summary=f"Dispute resolved: {resolution}",
            metadata={"resolution": resolution, "reason": reason, "released": released or []},
        )
        record_admin_audit(
            user=request.user,
            obj=job,
            action=f"dispute_{resolution}",
            reason=reason,
            metadata={"released_payout_ids": [row["id"] for row in (released or [])]},
        )
        return Response(
            {
                "job_id": job.id,
                "resolution": resolution,
                "status": job.status,
                "payment_status": job.payment_status,
                "released": released or [],
            }
        )


class AdminReportingView(APIView):
    permission_classes = ADMIN_PERMISSIONS

    def get(self, request):
        manager_profiles = UserProfile.objects.filter(
            user__role__in=MANAGER_ROLES,
            user__is_active=True,
            is_system_account=False,
        )
        shops = Shop.objects.all()
        jobs = ManagedJob.objects.all()
        quote_requests = QuoteRequest.objects.all()
        quotes = Quote.objects.all()
        payouts = ManagedJobPayout.objects.all()
        payments = Payment.objects.all()

        quote_status_counts = {label: 0 for label in CANONICAL_STATUSES}
        for quote in quotes.only("status", "client_quote_status", "expires_at"):
            quote_status_counts[canonical_status_for_quote(quote)] += 1
        request_status_counts = {label: 0 for label in CANONICAL_STATUSES}
        for quote_request in quote_requests.only("status"):
            request_status_counts[canonical_status_for_request(quote_request)] += 1

        held_jobs = [job for job in jobs if _custody_status(job) == "held"]
        released_jobs = [job for job in jobs if _custody_status(job) == "released"]
        held_amounts = {
            "client_total": _money(sum((Decimal(_job_amounts(job)["client_total"] or "0") for job in held_jobs), Decimal("0.00"))),
            "shop_payout": _money(sum((Decimal(_job_amounts(job)["shop_payout"] or "0") for job in held_jobs), Decimal("0.00"))),
            "manager_payout": _money(sum((Decimal(_job_amounts(job)["manager_payout"] or "0") for job in held_jobs), Decimal("0.00"))),
        }
        released_amounts = {
            "client_total": _money(sum((Decimal(_job_amounts(job)["client_total"] or "0") for job in released_jobs), Decimal("0.00"))),
            "shop_payout": _money(sum((Decimal(_job_amounts(job)["shop_payout"] or "0") for job in released_jobs), Decimal("0.00"))),
            "manager_payout": _money(sum((Decimal(_job_amounts(job)["manager_payout"] or "0") for job in released_jobs), Decimal("0.00"))),
        }

        return Response(
            {
                "generated_at": timezone.now().isoformat(),
                "managers": {
                    "total": manager_profiles.count(),
                    "pending": manager_profiles.filter(manager_verified=False, broker_profile_active=True).count(),
                    "verified": manager_profiles.filter(manager_verified=True).count(),
                    "rejected": manager_profiles.filter(manager_verified=False, broker_profile_active=False).count(),
                },
                "printers": {
                    "total": shops.count(),
                    "pending": sum(1 for shop in shops if _printer_status(shop) == "pending"),
                    "verified": sum(1 for shop in shops if _printer_status(shop) == "verified"),
                    "rejected": sum(1 for shop in shops if _printer_status(shop) == "rejected"),
                },
                "jobs": {
                    "total": jobs.count(),
                    "active": jobs.exclude(status__in=[ManagedJobStatus.COMPLETED, ManagedJobStatus.CANCELLED]).count(),
                    "completed": jobs.filter(status=ManagedJobStatus.COMPLETED).count(),
                    "disputed": _dispute_queryset().count(),
                },
                "quotes": {
                    "requests": {key: request_status_counts[key] for key in CANONICAL_STATUSES},
                    "offers": {key: quote_status_counts[key] for key in CANONICAL_STATUSES},
                },
                "custody": {
                    "held_jobs": len(held_jobs),
                    "released_jobs": len(released_jobs),
                    "held_amounts": held_amounts,
                    "released_amounts": released_amounts,
                },
                "payouts": {
                    "total": payouts.count(),
                    "released": payouts.filter(status=ManagedJobPayout.STATUS_RELEASED).count(),
                    "failed": payouts.filter(status=ManagedJobPayout.STATUS_FAILED).count(),
                    "released_amount": _money(payouts.filter(status=ManagedJobPayout.STATUS_RELEASED).aggregate(total=Sum("amount"))["total"]),
                },
                "payments": {
                    "total": payments.count(),
                    "paid": payments.filter(status=Payment.STATUS_PAID).count(),
                    "pending": payments.filter(status__in=[Payment.STATUS_PENDING, Payment.STATUS_PROCESSING]).count(),
                    "paid_amount": _money(payments.filter(status=Payment.STATUS_PAID).aggregate(total=Sum("amount"))["total"]),
                },
            }
        )


__all__ = [
    "AdminManagerVerificationListView",
    "AdminManagerVerificationDecisionView",
    "AdminPrinterVerificationListView",
    "AdminPrinterVerificationDecisionView",
    "AdminCustodyListView",
    "AdminCustodyReleaseView",
    "AdminDisputeListView",
    "AdminDisputeResolveView",
    "AdminReportingView",
]
