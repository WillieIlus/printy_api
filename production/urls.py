from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ProductionOrderViewSet

router = DefaultRouter()
router.register(r"production-orders", ProductionOrderViewSet, basename="production-order")

urlpatterns = [path("", include(router.urls))]
