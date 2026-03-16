"""
Production tracking API URLs.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register(r"jobs", views.JobViewSet, basename="production-job")
router.register(r"job-processes", views.JobProcessViewSet, basename="production-job-process")
router.register(r"customers", views.CustomerViewSet, basename="production-customer")
router.register(r"products", views.ProductionProductViewSet, basename="production-product")
router.register(r"materials", views.ProductionMaterialViewSet, basename="production-material")
router.register(r"processes", views.ProcessViewSet, basename="production-process")
router.register(r"operators", views.OperatorViewSet, basename="production-operator")
router.register(r"pricing-methods", views.PricingMethodViewSet, basename="production-pricing-method")
router.register(r"wastage-stages", views.WastageStageViewSet, basename="production-wastage-stage")
router.register(r"price-cards", views.PriceCardViewSet, basename="production-price-card")
router.register(r"dashboard", views.DashboardViewSet, basename="production-dashboard")

urlpatterns = [
    path("", include(router.urls)),
]
