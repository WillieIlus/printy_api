from django.urls import path

from . import views

urlpatterns = [
    path("status/", views.SetupStatusView.as_view(), name="setup-status"),
]
