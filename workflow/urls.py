"""URLs for the workflow module (mounted under /api/workflow/)."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import WorkflowJobViewSet, WorkflowManagerViewSet, WorkflowPrinterViewSet

router = DefaultRouter()
router.register(r"jobs", WorkflowJobViewSet, basename="workflow-job")
router.register(r"managers", WorkflowManagerViewSet, basename="workflow-manager")
router.register(r"printers", WorkflowPrinterViewSet, basename="workflow-printer")

urlpatterns = [
    path("", include(router.urls)),
]