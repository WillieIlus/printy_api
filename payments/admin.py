from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone
import logging

from payments.models import MpesaSTKRequest, Payment
from payments.services import ACTIVE_PAYMENT_STATUSES, handle_stk_callback, mark_payment_paid
from quotes.choices import QuoteOfferStatus


logger = logging.getLogger(__name__)


def _sandbox_mpesa_enabled() -> bool:
    return str(getattr(settings, "MPESA_ENV", "") or getattr(settings, "MPESA_ENVIRONMENT", "")).lower() == "sandbox"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["id", "amount", "currency", "method", "provider", "status", "created_at"]
    list_filter = ["method", "provider", "status"]
    search_fields = ["account_reference", "checkout_request_id", "merchant_request_id", "mpesa_receipt_number"]
    actions = ["simulate_sandbox_payment_confirmation"]

    def _get_or_create_sandbox_stk_request(self, payment):
        stk_request = payment.mpesa_stk_requests.exclude(
            status=MpesaSTKRequest.STATUS_SUCCESS,
        ).order_by("-requested_at", "-id").first()
        if stk_request is None:
            stk_request = MpesaSTKRequest.objects.create(
                payment=payment,
                phone_number=payment.payer_phone or "",
                amount=payment.amount,
                account_reference=payment.account_reference,
                status=MpesaSTKRequest.STATUS_SENT,
            )

        changed = []
        if not stk_request.checkout_request_id:
            stk_request.checkout_request_id = f"TEST-ADMIN-CR-{stk_request.id}"
            changed.append("checkout_request_id")
        if not stk_request.merchant_request_id:
            stk_request.merchant_request_id = f"TEST-ADMIN-MR-{stk_request.id}"
            changed.append("merchant_request_id")
        if stk_request.status != MpesaSTKRequest.STATUS_SENT:
            stk_request.status = MpesaSTKRequest.STATUS_SENT
            changed.append("status")
        if changed:
            changed.append("updated_at")
            stk_request.save(update_fields=changed)
        return stk_request

    def _sandbox_success_callback(self, *, payment, stk_request, request):
        confirmed_at = timezone.now()
        admin_label = getattr(request.user, "email", "") or getattr(request.user, "username", "") or str(request.user)
        return {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": stk_request.checkout_request_id,
                    "MerchantRequestID": stk_request.merchant_request_id,
                    "ResultCode": 0,
                    "ResultDesc": f"Sandbox simulation by admin user {admin_label} at {confirmed_at.isoformat()}",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": str(payment.expected_amount or payment.amount)},
                            {"Name": "MpesaReceiptNumber", "Value": f"SANDBOX-{payment.id}"},
                        ]
                    },
                }
            },
            "SandboxSimulation": {
                "admin_user": admin_label,
                "admin_user_id": request.user.id,
                "simulated_at": confirmed_at.isoformat(),
            },
        }

    @admin.action(description="Simulate sandbox payment confirmation")
    def simulate_sandbox_payment_confirmation(self, request, queryset):
        if not _sandbox_mpesa_enabled():
            self.message_user(
                request,
                "Sandbox payment confirmation can only run when MPESA_ENV is 'sandbox'.",
                level=messages.ERROR,
            )
            return

        confirmed = 0
        refused = 0
        repaired = 0
        errored = 0
        for payment in queryset.select_related("quote", "payer"):
            try:
                if payment.status == Payment.STATUS_FAILED:
                    refused += 1
                    self.message_user(
                        request,
                        f"Payment #{payment.id} was not simulated because it is already {payment.status}.",
                        level=messages.ERROR,
                    )
                    continue
                if payment.status not in {*ACTIVE_PAYMENT_STATUSES, Payment.STATUS_PAID}:
                    refused += 1
                    self.message_user(
                        request,
                        f"Payment #{payment.id} was not simulated because status '{payment.status}' is not pending, processing, or paid.",
                        level=messages.ERROR,
                    )
                    continue
                if payment.quote is None or payment.quote.status != QuoteOfferStatus.ACCEPTED:
                    refused += 1
                    self.message_user(
                        request,
                        f"Payment #{payment.id} was not simulated because its quote is not payable.",
                        level=messages.ERROR,
                    )
                    continue

                if payment.status == Payment.STATUS_PAID:
                    had_managed_job = bool(payment.managed_job_id) and payment.quote.managed_jobs.filter(
                        pk=payment.managed_job_id,
                    ).exists()
                    mark_payment_paid(payment)
                    payment.refresh_from_db(fields=["managed_job"])
                    if not had_managed_job and payment.managed_job_id:
                        repaired += 1
                        self.log_change(
                            request,
                            payment,
                            f"Sandbox repair by admin user {request.user} at {timezone.now().isoformat()}",
                        )
                    else:
                        self.message_user(request, f"Payment {payment.id} already fully processed.", level=messages.INFO)
                    continue

                stk_request = self._get_or_create_sandbox_stk_request(payment)
                callback_payload = self._sandbox_success_callback(payment=payment, stk_request=stk_request, request=request)
                handle_stk_callback(callback_payload=callback_payload)
            except ValidationError as exc:
                refused += 1
                self.message_user(request, f"Payment #{payment.id} simulation failed: {'; '.join(exc.messages)}", level=messages.ERROR)
                continue
            except Exception as exc:
                errored += 1
                logger.exception("Unexpected error processing sandbox payment simulation for payment_id=%s", payment.id)
                self.message_user(request, f"Error processing payment {payment.id}: {exc}", level=messages.ERROR)
                continue

            confirmed += 1
            self.log_change(
                request,
                payment,
                f"Sandbox simulation by admin user {request.user} at {timezone.now().isoformat()}",
            )

        if confirmed:
            self.message_user(
                request,
                f"Sandbox payment confirmation simulated for {confirmed} payment(s).",
                level=messages.SUCCESS,
            )
        if refused:
            self.message_user(request, f"{refused} payment(s) were not simulated.", level=messages.WARNING)
        if repaired:
            self.message_user(
                request,
                f"Repaired {repaired} already-paid payment(s) by creating missing ManagedJob.",
                level=messages.SUCCESS,
            )
        if errored:
            self.message_user(request, f"{errored} payment(s) hit unexpected errors.", level=messages.ERROR)
        if confirmed == 0 and refused == 0 and repaired == 0:
            self.message_user(
                request,
                "No payments were processed. Selection may be empty or all payments are already complete.",
                level=messages.INFO,
            )


@admin.register(MpesaSTKRequest)
class MpesaSTKRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "payment", "phone_number", "amount", "status", "requested_at"]
    list_filter = ["status"]
    search_fields = ["account_reference", "checkout_request_id", "merchant_request_id", "phone_number"]
    actions = ["simulate_sandbox_stk_success"]

    def _sandbox_success_callback(self, *, stk_request, request):
        confirmed_at = timezone.now()
        admin_label = getattr(request.user, "email", "") or getattr(request.user, "username", "") or str(request.user)
        payment = stk_request.payment
        return {
            "Body": {
                "stkCallback": {
                    "CheckoutRequestID": stk_request.checkout_request_id,
                    "MerchantRequestID": stk_request.merchant_request_id,
                    "ResultCode": 0,
                    "ResultDesc": f"Sandbox STK simulation by admin user {admin_label} at {confirmed_at.isoformat()}",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": str(payment.expected_amount or payment.amount)},
                            {"Name": "MpesaReceiptNumber", "Value": f"SANDBOX-STK-{stk_request.id}"},
                        ]
                    },
                }
            },
            "SandboxSimulation": {
                "admin_user": admin_label,
                "admin_user_id": request.user.id,
                "simulated_at": confirmed_at.isoformat(),
            },
        }

    @admin.action(description="Simulate sandbox STK success")
    def simulate_sandbox_stk_success(self, request, queryset):
        if not _sandbox_mpesa_enabled():
            self.message_user(
                request,
                "Sandbox STK success can only run when MPESA_ENV is 'sandbox'.",
                level=messages.ERROR,
            )
            return

        confirmed = 0
        refused = 0
        for stk_request in queryset.select_related("payment", "payment__quote"):
            payment = stk_request.payment
            if payment.quote is None or payment.quote.status != QuoteOfferStatus.ACCEPTED:
                refused += 1
                self.message_user(
                    request,
                    f"STK request #{stk_request.id} was not simulated because its payment quote is not payable.",
                    level=messages.ERROR,
                )
                continue
            changed = []
            if not stk_request.checkout_request_id:
                stk_request.checkout_request_id = f"TEST-ADMIN-CR-{stk_request.id}"
                changed.append("checkout_request_id")
            if not stk_request.merchant_request_id:
                stk_request.merchant_request_id = f"TEST-ADMIN-MR-{stk_request.id}"
                changed.append("merchant_request_id")
            if stk_request.status != MpesaSTKRequest.STATUS_SENT:
                stk_request.status = MpesaSTKRequest.STATUS_SENT
                changed.append("status")
            if changed:
                changed.append("updated_at")
                stk_request.save(update_fields=changed)

            try:
                handle_stk_callback(callback_payload=self._sandbox_success_callback(stk_request=stk_request, request=request))
            except ValidationError as exc:
                refused += 1
                self.message_user(request, f"STK request #{stk_request.id} simulation failed: {'; '.join(exc.messages)}", level=messages.ERROR)
                continue

            confirmed += 1
            self.log_change(
                request,
                stk_request,
                f"Sandbox STK simulation by admin user {request.user} at {timezone.now().isoformat()}",
            )

        if confirmed:
            self.message_user(request, f"Sandbox STK success simulated for {confirmed} request(s).", level=messages.SUCCESS)
        if refused:
            self.message_user(request, f"{refused} STK request(s) were not simulated.", level=messages.WARNING)
        if not confirmed and not refused:
            self.message_user(request, "No STK requests were selected for sandbox success simulation.", level=messages.WARNING)
