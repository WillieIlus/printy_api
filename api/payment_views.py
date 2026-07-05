import logging
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.roles import ActorRole, get_actor_role
from jobs.models import ManagedJob, ManagedJobPayout
from jobs.services.dispatch import dispatch_job_to_shop
from jobs.payout_services import release_managed_job_payouts
from accounts.serializers import get_or_create_profile
from payments.models import MpesaSTKRequest, Payment, PaymentPhoneConsent
from payments.serializers import PaymentSerializer
from payments.services import handle_stk_callback, initiate_stk_push
from quotes.models import Quote
from quotes.acceptance import accept_quote_for_payment
from shops.models import Shop


logger = logging.getLogger(__name__)

MPESA_PROFILE_PHONE_CONSENT_TEXT = (
    "Use this number for this M-Pesa payment. You can also save it to your "
    "Printy account for future order updates, receipts, and faster checkout."
)


def _validation_detail(exc) -> str:
    messages = getattr(exc, "messages", None)
    if messages:
        return "; ".join(str(message) for message in messages)
    return str(exc)


def _is_platform_actor(user) -> bool:
    role = get_actor_role(user)
    return role in {ActorRole.BROKER, ActorRole.MANAGER, ActorRole.ADMIN}


def _is_admin_user(user) -> bool:
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    return get_actor_role(user) == ActorRole.ADMIN


def _can_access_managed_job_payments(user, managed_job: ManagedJob) -> bool:
    user_id = getattr(user, "id", None)
    if _is_admin_user(user):
        return True
    if managed_job.client_id == user_id:
        return True
    if managed_job.broker_id == user_id:
        return True
    assigned_shop = getattr(managed_job, "assigned_shop", None)
    return bool(assigned_shop and getattr(assigned_shop, "owner_id", None) == user_id)


def _is_managed_job_broker(user, managed_job: ManagedJob) -> bool:
    return bool(managed_job.broker_id and managed_job.broker_id == getattr(user, "id", None))


def _can_trigger_managed_job_payment(user, managed_job: ManagedJob) -> bool:
    return managed_job.client_id == getattr(user, "id", None)


def _managed_job_payments(managed_job: ManagedJob):
    if hasattr(managed_job, "canonical_payments"):
        return managed_job.canonical_payments.all()
    source_quote = getattr(managed_job, "source_quote", None)
    if source_quote is not None and hasattr(source_quote, "payments"):
        return source_quote.payments.all()
    return Payment.objects.none()


def _serialize_payment(payment: Payment, request) -> dict:
    payload = dict(PaymentSerializer(payment, context={"request": request}).data or {})
    payload.update(
        {
            "id": payment.id,
            "amount": str(payment.amount) if payment.amount is not None else None,
            "expected_amount": str(payment.expected_amount) if payment.expected_amount is not None else None,
            "status": payment.status,
            "mpesa_receipt_number": payment.mpesa_receipt_number,
            "checkout_request_id": payment.checkout_request_id,
            "merchant_request_id": payment.merchant_request_id,
            "created_at": payment.created_at,
            "account_reference": payment.account_reference,
        }
    )
    return payload


def _settlement_status(managed_job: ManagedJob) -> str:
    if getattr(managed_job, "payout_hold", False):
        return "payout_on_hold"
    payment_status = str(getattr(managed_job, "payment_status", "") or "").lower()
    if payment_status == "release_ready":
        return "manual_payout_pending"
    if payment_status == "released":
        return "manual_payout_pending"
    if payment_status not in {"confirmed", "paid", "completed"}:
        return "pending_payment"
    if getattr(managed_job, "completed_at", None) is not None or getattr(managed_job, "status", "") == "completed":
        return "manual_payout_pending"
    return "pending_completion"


def _settlement_status_label(status_value: str) -> str:
    return {
        "pending_payment": "Pending payment",
        "pending_completion": "Pending completion",
        "manual_payout_pending": "Manual payout pending",
        "payout_on_hold": "Payout on hold",
        "paid": "Paid",
    }.get(status_value, str(status_value or "pending").replace("_", " ").title())


def _role_settlement_status(managed_job: ManagedJob, recipient_role: str, fallback_status: str) -> str:
    if managed_job.payouts.filter(
        recipient_role=recipient_role,
        status=ManagedJobPayout.STATUS_RELEASED,
        released_at__isnull=False,
    ).exists():
        return "paid"
    return fallback_status


def _settlement_payload(managed_job: ManagedJob, settlement_status: str) -> dict:
    return {
        "managed_job_id": managed_job.id,
        "status": settlement_status,
        "payout_status": settlement_status,
        "payout_status_label": _settlement_status_label(settlement_status),
        "disbursement": "manual",
        "disbursed": settlement_status == "paid",
        "payout_disbursed": settlement_status == "paid",
    }


def _source_split(managed_job: ManagedJob):
    return getattr(getattr(managed_job, "source_quote", None), "financial_split", None)


def _serialize_managed_job_settlement(managed_job: ManagedJob, user) -> dict:
    base_status = _settlement_status(managed_job)
    role = get_actor_role(user)
    split = _source_split(managed_job)
    if _is_managed_job_broker(user, managed_job):
        settlement_status = _role_settlement_status(managed_job, ManagedJobPayout.RECIPIENT_ROLE_MANAGER, base_status)
        broker_payout = (
            getattr(managed_job, "broker_payout", None)
            or getattr(split, "broker_payout", None)
        )
        return {
            **_settlement_payload(managed_job, settlement_status),
            "role": "manager",
            "expected_manager_payout": str(broker_payout) if broker_payout is not None else None,
            "broker_payout": str(broker_payout) if broker_payout is not None else None,
            "message": (
                "Expected payout. This amount has been disbursed by manual Printy release."
                if settlement_status == "paid"
                else "Expected payout. This amount is not yet disbursed."
            ),
        }
    if role == ActorRole.SHOP:
        settlement_status = _role_settlement_status(managed_job, ManagedJobPayout.RECIPIENT_ROLE_SHOP, base_status)
        assignment = managed_job.assignments.filter(reassigned_from__isnull=True).first()
        shop_payout = getattr(assignment, "shop_payout", None) or getattr(split, "shop_payout", None)
        return {
            **_settlement_payload(managed_job, settlement_status),
            "role": "shop",
            "expected_production_payout": str(shop_payout) if shop_payout is not None else None,
            "shop_payout": str(shop_payout) if shop_payout is not None else None,
            "message": (
                "Expected production payout. This amount has been disbursed by manual Printy release."
                if settlement_status == "paid"
                else "Expected production payout. This amount is not yet disbursed."
            ),
        }
    if role == ActorRole.CLIENT:
        return {
            "managed_job_id": managed_job.id,
            "status": "not_available",
            "role": "client",
        }
    payout_roles = set(managed_job.payouts.values_list("recipient_role", flat=True))
    released_roles = set(
        managed_job.payouts.filter(
            status=ManagedJobPayout.STATUS_RELEASED,
            released_at__isnull=False,
        ).values_list("recipient_role", flat=True)
    )
    settlement_status = "paid" if payout_roles and payout_roles.issubset(released_roles) else base_status
    broker_payout = (
        getattr(managed_job, "broker_payout", None)
        or getattr(split, "broker_payout", None)
    )
    return {
        **_settlement_payload(managed_job, settlement_status),
        "role": "ops",
        "expected_manager_payout": str(broker_payout) if broker_payout is not None else None,
        "broker_payout": str(broker_payout) if broker_payout is not None else None,
    }


def _can_use_payment(user, payment: Payment) -> bool:
    if _is_platform_actor(user):
        return True
    return bool(payment.payer_id == getattr(user, "id", None))


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _client_ip(request) -> str | None:
    forwarded = str(request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return forwarded or request.META.get("REMOTE_ADDR") or None


def _record_payment_phone_consent(
    *,
    request,
    payment: Payment,
    stk_request: MpesaSTKRequest,
    phone: str,
    source: str,
) -> None:
    if not _truthy(request.data.get("save_phone_to_profile")):
        return

    profile = get_or_create_profile(request.user)
    if profile.phone != phone:
        profile.phone = phone
        profile.save(update_fields=["phone", "updated_at"])

    PaymentPhoneConsent.objects.create(
        user=request.user,
        payment=payment,
        stk_request=stk_request,
        phone_number=phone,
        source=source,
        consent_text=MPESA_PROFILE_PHONE_CONSENT_TEXT,
        ip_address=_client_ip(request),
        user_agent=str(request.META.get("HTTP_USER_AGENT") or "")[:500],
    )


def _remember_payment_phone(payment: Payment, phone: str) -> None:
    if payment.payer_phone == phone:
        return
    payment.payer_phone = phone
    payment.save(update_fields=["payer_phone", "updated_at"])


class QuoteAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, quote_id):
        quote = get_object_or_404(
            Quote.objects.select_related("quote_request", "shop", "production_option"),
            pk=quote_id,
        )
        try:
            quote, payment = accept_quote_for_payment(
                quote=quote,
                accepted_by=request.user,
                payer_phone=str(request.data.get("phone_number") or ""),
            )
        except ValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=400)
        return Response(
            {
                "quote_id": quote.id,
                "status": quote.status,
                "accepted_at": quote.accepted_at,
                "payment": PaymentSerializer(payment, context={"request": request}).data,
            },
            status=200,
        )


class PaymentInitiateSTKView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        payment_id = request.data.get("payment_id")
        quote_id = request.data.get("quote_id")
        phone = str(request.data.get("phone_number") or request.data.get("phone") or "").strip()
        if not phone:
            return Response({"detail": "phone_number is required."}, status=400)
        if payment_id:
            payment = get_object_or_404(Payment.objects.select_related("payer", "quote"), pk=payment_id)
        elif quote_id:
            quote = get_object_or_404(Quote, pk=quote_id)
            try:
                _quote, payment = accept_quote_for_payment(quote=quote, accepted_by=request.user, payer_phone=phone)
            except ValidationError as exc:
                return Response({"detail": "; ".join(exc.messages)}, status=400)
        else:
            return Response({"detail": "payment_id or quote_id is required."}, status=400)
        if not _can_use_payment(request.user, payment):
            raise PermissionDenied("Only the payer or platform staff can initiate this payment.")
        try:
            _remember_payment_phone(payment, phone)
            stk_request = initiate_stk_push(payment=payment, phone_number=phone)
            _record_payment_phone_consent(
                request=request,
                payment=payment,
                stk_request=stk_request,
                phone=phone,
                source=PaymentPhoneConsent.SOURCE_QUOTE_PAYMENT,
            )
        except NotImplementedError as exc:
            return Response({"detail": str(exc)}, status=501)
        except ValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=400)
        payment.refresh_from_db()
        return Response(
            {
                "payment_id": payment.id,
                "status": payment.status,
                "checkout_request_id": stk_request.checkout_request_id,
                "merchant_request_id": stk_request.merchant_request_id,
            },
            status=201,
        )


class ManagedJobPaymentInitiateSTKView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("source_quote", "source_quote__quote_request"),
            pk=pk,
        )
        if managed_job.source_quote_id is None:
            return Response({"detail": "Managed job has no source quote for canonical payment."}, status=400)
        phone = str(request.data.get("phone_number") or request.data.get("phone") or "").strip()
        if not phone:
            return Response({"detail": "phone_number is required."}, status=400)
        try:
            quote, payment = accept_quote_for_payment(
                quote=managed_job.source_quote,
                accepted_by=request.user,
                payer_phone=phone,
            )
            _remember_payment_phone(payment, phone)
            stk_request = initiate_stk_push(payment=payment, phone_number=phone)
            _record_payment_phone_consent(
                request=request,
                payment=payment,
                stk_request=stk_request,
                phone=phone,
                source=PaymentPhoneConsent.SOURCE_MANAGED_JOB_PAYMENT,
            )
        except NotImplementedError as exc:
            return Response({"detail": str(exc)}, status=501)
        except ValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=400)
        return Response(
            {
                "payment_id": payment.id,
                "quote_id": quote.id,
                "status": payment.status,
                "checkout_request_id": stk_request.checkout_request_id,
                "merchant_request_id": stk_request.merchant_request_id,
            },
            status=201,
        )


class ManagedJobPaymentsListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            managed_job = get_object_or_404(
                ManagedJob.objects.select_related(
                    "assigned_shop",
                    "client",
                    "broker",
                    "source_quote",
                    "source_quote__financial_split",
                ),
                pk=pk,
            )
            if not _can_access_managed_job_payments(request.user, managed_job):
                return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)
            payments = _managed_job_payments(managed_job).select_related("payer", "quote").order_by("-created_at", "-id")
            return Response([_serialize_payment(payment, request) for payment in payments], status=status.HTTP_200_OK)
        except Http404:
            raise
        except Exception:
            logger.exception("Managed job payments list failed managed_job_id=%s user_id=%s", pk, getattr(request.user, "id", None))
            return Response({"detail": "Unable to load managed job payments."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManagedJobSettlementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            managed_job = get_object_or_404(
                ManagedJob.objects.select_related("assigned_shop", "client", "broker", "source_quote"),
                pk=pk,
            )
            if not _can_access_managed_job_payments(request.user, managed_job):
                return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)
            return Response(_serialize_managed_job_settlement(managed_job, request.user), status=status.HTTP_200_OK)
        except Http404:
            raise
        except Exception:
            logger.exception("Managed job settlement failed managed_job_id=%s user_id=%s", pk, getattr(request.user, "id", None))
            return Response({"detail": "Unable to load managed job settlement."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManagedJobPayoutReleaseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not _is_admin_user(request.user):
            return Response({"detail": "Only Printy admin staff can release payouts."}, status=status.HTTP_403_FORBIDDEN)
        managed_job = get_object_or_404(
            ManagedJob.objects.select_related("assigned_shop", "broker", "source_quote"),
            pk=pk,
        )
        try:
            result = release_managed_job_payouts(managed_job=managed_job, released_by=request.user)
        except ValidationError as exc:
            return Response({"detail": _validation_detail(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payouts = [
            {
                "id": payout.id,
                "recipient_role": payout.recipient_role,
                "amount": str(payout.amount),
                "currency": payout.currency,
                "status": payout.status,
                "released_at": payout.released_at,
            }
            for payout in result["payouts"]
        ]
        return Response(
            {
                "managed_job_id": managed_job.id,
                "payout_status": "paid",
                "idempotent": not result["created_or_updated"],
                "payouts": payouts,
            },
            status=status.HTTP_200_OK,
        )


class ManagedJobMpesaSTKPushView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            managed_job = get_object_or_404(
                ManagedJob.objects.select_related("assigned_shop", "client", "broker", "source_quote"),
                pk=pk,
            )
            if not _can_trigger_managed_job_payment(request.user, managed_job):
                return Response({"detail": "Only the client who owns this job can initiate payment."}, status=status.HTTP_403_FORBIDDEN)
            phone = str(request.data.get("phone_number") or request.data.get("phone") or "").strip()
            if not phone:
                return Response({"detail": "phone_number is required."}, status=status.HTTP_400_BAD_REQUEST)
            payment = (
                _managed_job_payments(managed_job)
                .filter(status__in=[Payment.STATUS_PENDING, Payment.STATUS_PROCESSING])
                .order_by("-created_at", "-id")
                .first()
            )
            if payment is None:
                return Response({"detail": "No pending or processing payment found for this managed job."}, status=status.HTTP_400_BAD_REQUEST)
            if request.data.get("amount") not in (None, ""):
                try:
                    requested_amount = Decimal(str(request.data.get("amount")))
                except (InvalidOperation, TypeError):
                    return Response({"detail": "amount must be a valid decimal."}, status=status.HTTP_400_BAD_REQUEST)
                expected_amount = Decimal(str(payment.expected_amount or payment.amount))
                if requested_amount != expected_amount:
                    return Response({"detail": "amount must match the pending payment amount."}, status=status.HTTP_400_BAD_REQUEST)
            _remember_payment_phone(payment, phone)
            stk_request = initiate_stk_push(payment=payment, phone_number=phone)
            _record_payment_phone_consent(
                request=request,
                payment=payment,
                stk_request=stk_request,
                phone=phone,
                source=PaymentPhoneConsent.SOURCE_MANAGED_JOB_PAYMENT,
            )
            payment.refresh_from_db()
            return Response(
                {
                    "payment_id": payment.id,
                    "status": payment.status,
                    "stk_status": stk_request.status,
                    "checkout_request_id": stk_request.checkout_request_id,
                    "merchant_request_id": stk_request.merchant_request_id,
                    "response_code": stk_request.response_code,
                    "response_description": stk_request.response_description,
                    "customer_message": stk_request.customer_message,
                },
                status=status.HTTP_201_CREATED,
            )
        except Http404:
            raise
        except NotImplementedError as exc:
            logger.exception("Managed job STK push is not configured managed_job_id=%s", pk)
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except ValidationError as exc:
            logger.exception("Managed job STK push validation failed managed_job_id=%s", pk)
            return Response({"detail": _validation_detail(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Managed job STK push failed managed_job_id=%s user_id=%s", pk, getattr(request.user, "id", None))
            return Response({"detail": "Unable to initiate M-Pesa STK push."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManagedJobMpesaQueryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        try:
            managed_job = get_object_or_404(
                ManagedJob.objects.select_related("assigned_shop", "client", "broker", "source_quote"),
                pk=pk,
            )
            if not _can_trigger_managed_job_payment(request.user, managed_job):
                return Response({"detail": "Only the client who owns this job can query payment status."}, status=status.HTTP_403_FORBIDDEN)
            checkout_request_id = str(request.data.get("checkout_request_id") or "").strip()
            if not checkout_request_id:
                return Response({"detail": "checkout_request_id is required."}, status=status.HTTP_400_BAD_REQUEST)
            payment_ids = list(_managed_job_payments(managed_job).values_list("id", flat=True))
            stk_request = (
                MpesaSTKRequest.objects.select_related("payment")
                .filter(payment_id__in=payment_ids, checkout_request_id=checkout_request_id)
                .order_by("-requested_at", "-id")
                .first()
            )
            if stk_request is None:
                return Response({"detail": "No M-Pesa STK request found for this managed job and checkout_request_id."}, status=status.HTTP_404_NOT_FOUND)
            payment = stk_request.payment
            return Response(
                {
                    "stk_status": stk_request.status,
                    "payment_status": payment.status,
                    "mpesa_receipt_number": payment.mpesa_receipt_number,
                    "checkout_request_id": stk_request.checkout_request_id,
                },
                status=status.HTTP_200_OK,
            )
        except Http404:
            raise
        except Exception:
            logger.exception("Managed job M-Pesa query failed managed_job_id=%s user_id=%s", pk, getattr(request.user, "id", None))
            return Response({"detail": "Unable to query M-Pesa payment status."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MpesaCallbackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            payload = handle_stk_callback(callback_payload=request.data)
        except ValidationError as exc:
            return Response({"status": "failed", "detail": "; ".join(exc.messages)}, status=400)
        return Response(payload, status=200)


class JobDispatchView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        managed_job = get_object_or_404(ManagedJob.objects.select_related("source_quote", "assigned_shop"), pk=pk)
        role = get_actor_role(request.user)
        if role not in {ActorRole.BROKER, ActorRole.MANAGER, ActorRole.ADMIN} and not _is_managed_job_broker(request.user, managed_job):
            raise PermissionDenied("Only manager, broker, or admin users may dispatch jobs.")
        shop = None
        if request.data.get("shop_id"):
            shop = get_object_or_404(Shop, pk=request.data.get("shop_id"))
        try:
            assignment = dispatch_job_to_shop(
                managed_job=managed_job,
                dispatched_by=request.user,
                shop=shop,
                notes=str(request.data.get("notes") or ""),
            )
        except ValidationError as exc:
            return Response({"detail": "; ".join(exc.messages)}, status=400)
        return Response(
            {
                "assignment_id": assignment.id,
                "managed_job_id": assignment.managed_job_id,
                "shop_id": assignment.assigned_shop_id,
                "shop_payout": str(assignment.shop_payout) if assignment.shop_payout is not None else None,
            },
            status=201,
        )
