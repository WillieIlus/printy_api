"""URL configuration for shops app."""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ShopViewSet, MachineViewSet, PaperViewSet,
    PrintingRateViewSet, FinishingRateViewSet, MaterialViewSet,
    ProductViewSet, ProductFinishingOptionViewSet,
)

router = DefaultRouter()
router.register(r'', ShopViewSet, basename='shop')

urlpatterns = [
    path('', include(router.urls)),
    path(
        '<int:shop_pk>/machines/',
        MachineViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='shop-machines',
    ),
    path(
        '<int:shop_pk>/machines/<int:pk>/',
        MachineViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='shop-machine-detail',
    ),
    path(
        '<int:shop_pk>/papers/',
        PaperViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='shop-papers',
    ),
    path(
        '<int:shop_pk>/papers/<int:pk>/',
        PaperViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='shop-paper-detail',
    ),
    path(
        '<int:shop_pk>/printing-rates/',
        PrintingRateViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='shop-printing-rates',
    ),
    path(
        '<int:shop_pk>/printing-rates/<int:pk>/',
        PrintingRateViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='shop-printing-rate-detail',
    ),
    path(
        '<int:shop_pk>/finishing-rates/',
        FinishingRateViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='shop-finishing-rates',
    ),
    path(
        '<int:shop_pk>/finishing-rates/<int:pk>/',
        FinishingRateViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='shop-finishing-rate-detail',
    ),
    path(
        '<int:shop_pk>/materials/',
        MaterialViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='shop-materials',
    ),
    path(
        '<int:shop_pk>/materials/<int:pk>/',
        MaterialViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='shop-material-detail',
    ),
    path(
        '<int:shop_pk>/products/',
        ProductViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='shop-products',
    ),
    path(
        '<int:shop_pk>/products/<int:pk>/',
        ProductViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='shop-product-detail',
    ),
    path(
        '<int:shop_pk>/products/<int:product_pk>/finishing-options/',
        ProductFinishingOptionViewSet.as_view({'get': 'list', 'post': 'create'}),
        name='shop-product-finishing-options',
    ),
    path(
        '<int:shop_pk>/products/<int:product_pk>/finishing-options/<int:pk>/',
        ProductFinishingOptionViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}),
        name='shop-product-finishing-option-detail',
    ),
]
