"""M-Pesa routes.

These match the canonical callback in docs/env_vars.md:
    https://api.printy.ke/api/payments/mpesa/callback/
"""

from django.urls import path

from .views import (
    MpesaCallbackView,
    MpesaPaymentDetailView,
    MpesaPaymentListView,
    MpesaPaymentQueryView,
    MpesaStkPushView,
    MpesaTransactionStatusView,
)

app_name = "mpesa_payments"

urlpatterns = [
    path("stk-push/", MpesaStkPushView.as_view(), name="stk-push"),
    path("callback/", MpesaCallbackView.as_view(), name="callback"),
    # ResultURL / QueueTimeOutURL for Transaction Status Query notifications.
    path("result/", MpesaTransactionStatusView.as_view(), name="transaction-status"),
    path("transactions/", MpesaPaymentListView.as_view(), name="transactions"),
    path("<int:pk>/", MpesaPaymentDetailView.as_view(), name="detail"),
    path("<int:pk>/query/", MpesaPaymentQueryView.as_view(), name="query"),
]
