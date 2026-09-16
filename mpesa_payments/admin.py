"""Admin for M-Pesa payments.

Read-mostly on purpose: state transitions have guards, so staff should change
status through the provided actions rather than by editing the field directly.
"""

from django.contrib import admin
from django.utils import timezone

from .models import MpesaAccessToken, MpesaCallbackLog, MpesaPayment, MpesaPaymentStatus
from .services import query_stk_status


@admin.register(MpesaPayment)
class MpesaPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id", "account_reference", "phone_number", "amount", "paid_amount",
        "status", "reconciliation_status", "mpesa_receipt_number", "initiated_at",
    )
    list_filter = ("status", "reconciliation_status", "initiated_at")
    search_fields = ("phone_number", "account_reference", "mpesa_receipt_number", "checkout_request_id")
    readonly_fields = (
        "initiated_at", "stk_pushed_at", "confirmed_at", "updated_at",
        "checkout_request_id", "merchant_request_id", "mpesa_receipt_number",
        "paid_amount", "raw_callback", "result_code", "result_desc", "customer_message",
    )
    date_hierarchy = "initiated_at"
    actions = ["query_daraja", "flag_for_review"]

    @admin.action(description="Query Daraja for pending payments")
    def query_daraja(self, request, queryset):
        checked = 0
        for payment in queryset.filter(status=MpesaPaymentStatus.PENDING):
            try:
                query_stk_status(payment)
                checked += 1
            except Exception:  # noqa: BLE001
                self.message_user(request, f"Could not query payment {payment.id}.")
        self.message_user(request, f"Queried {checked} payment(s).")

    @admin.action(description="Flag for manual review")
    def flag_for_review(self, request, queryset):
        for payment in queryset:
            payment.mark_needs_review("Flagged by staff for manual review.")
        self.message_user(request, "Flagged for review.")


@admin.register(MpesaCallbackLog)
class MpesaCallbackLogAdmin(admin.ModelAdmin):
    list_display = ("id", "checkout_request_id", "result_code", "processed", "duplicate", "received_at")
    list_filter = ("processed", "duplicate")
    search_fields = ("checkout_request_id", "merchant_request_id")
    readonly_fields = ("checkout_request_id", "merchant_request_id", "result_code", "result_desc", "payload", "received_at")
    date_hierarchy = "received_at"

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(MpesaAccessToken)
class MpesaAccessTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "expires_at", "created_at")

    def has_add_permission(self, request) -> bool:
        return False
