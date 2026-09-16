"""URLs to include under the existing /api/ namespace."""

from django.urls import path

from .views import (
    ClientCalculatorConfigView,
    ClientCalculatorDraftView,
    ClientCalculatorPreviewView,
)


app_name = "client_calculator"

urlpatterns = [
    path("config/", ClientCalculatorConfigView.as_view(), name="config"),
    path("preview/", ClientCalculatorPreviewView.as_view(), name="preview"),
    path("drafts/", ClientCalculatorDraftView.as_view(), name="draft-create"),
]