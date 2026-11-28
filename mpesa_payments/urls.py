"""M-Pesa routes.

These match the canonical callback in docs/env_vars.md:
    https://api.printy.ke/api/payments/mpesa/callback/

The URL actually registered with Daraja carries MPESA_CALLBACK_SECRET as a
final path segment (callback/<secret>/, result/<secret>/), appended by
payments.callback_security.signed_callback_url. The bare routes stay so a
``?token=<secret>`` URL also works and so unauthenticated hits get a 403.
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
    path("callback/<str:callback_token>/", MpesaCallbackView.as_view(), name="callback-signed"),
    # ResultURL / QueueTimeOutURL for Transaction Status Query notifications.
    path("result/", MpesaTransactionStatusView.as_view(), name="transaction-status"),
    path(
        "result/<str:callback_token>/",
        MpesaTransactionStatusView.as_view(),
        name="transaction-status-signed",
    ),
    path("transactions/", MpesaPaymentListView.as_view(), name="transactions"),
    path("<int:pk>/", MpesaPaymentDetailView.as_view(), name="detail"),
    path("<int:pk>/query/", MpesaPaymentQueryView.as_view(), name="query"),
]
