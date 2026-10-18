from django.urls import path

from clients.views import (
    ClientLinkAcknowledgeView,
    ClientLinkArtworkView,
    ClientLinkOtpRequestView,
    ClientLinkOtpVerifyView,
    ClientLinkPaymentView,
    ClientLinkView,
    ManagerCreateClientLinkView,
    ManagerCreateClientRequestView,
    ManagerRevokeClientLinkView,
)

urlpatterns = [
    # Manager (active role: partner).
    path("manager/quote-requests/", ManagerCreateClientRequestView.as_view(), name="client_manager_create_request"),
    path("manager/quote-requests/<int:pk>/link/", ManagerCreateClientLinkView.as_view(), name="client_manager_create_link"),
    path("manager/links/<int:pk>/revoke/", ManagerRevokeClientLinkView.as_view(), name="client_manager_revoke_link"),
    # Public (signed link, rate-limited).
    path("links/<str:token>/", ClientLinkView.as_view(), name="client_link_view"),
    path("links/<str:token>/otp/request/", ClientLinkOtpRequestView.as_view(), name="client_link_otp_request"),
    path("links/<str:token>/otp/verify/", ClientLinkOtpVerifyView.as_view(), name="client_link_otp_verify"),
    path("links/<str:token>/artwork/", ClientLinkArtworkView.as_view(), name="client_link_artwork"),
    path("links/<str:token>/acknowledge/", ClientLinkAcknowledgeView.as_view(), name="client_link_acknowledge"),
    path("links/<str:token>/payment/", ClientLinkPaymentView.as_view(), name="client_link_payment"),
]
