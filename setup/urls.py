from django.urls import path

from . import views

urlpatterns = [
    path("status/", views.SetupStatusView.as_view(), name="setup-status"),
    path("shops/<slug:slug>/status/", views.ShopSetupStatusView.as_view(), name="shop-setup-status"),
]
