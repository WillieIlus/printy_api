"""
Shops app URL configuration.
"""
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import views

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/me/", views.MeView.as_view(), name="me"),
    path("auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("sheet-sizes/", views.SheetSizeListView.as_view(), name="sheet-size-list"),
    path("shops/", views.ShopListView.as_view(), name="shop-list"),
    path("shops/create/", views.ShopCreateView.as_view(), name="shop-create"),
    # Slug first: canonical shop lookup by slug (e.g. /shops/acme-print/products/)
    path(
        "shops/<slug:slug>/",
        views.ShopDetailView.as_view(),
        name="shop-detail",
    ),
    path(
        "shops/<slug:shop_slug>/products/",
        views.ProductListView.as_view(),
        name="product-list",
    ),
    path(
        "shops/<slug:shop_slug>/products/<int:pk>/",
        views.ProductDetailView.as_view(),
        name="product-detail",
    ),
    path(
        "shops/<slug:shop_slug>/products/create/",
        views.ProductCreateView.as_view(),
        name="product-create",
    ),
    path(
        "shops/<slug:shop_slug>/papers/create/",
        views.PaperCreateView.as_view(),
        name="paper-create",
    ),
    # Fallback: shop by numeric ID (e.g. /shops/1/products/) when slug is numeric
    path(
        "shops/<int:shop_id>/",
        views.ShopDetailByIdView.as_view(),
        name="shop-detail-by-id",
    ),
    path(
        "shops/<int:shop_id>/products/",
        views.ProductListView.as_view(),
        name="product-list-by-id",
    ),
    path(
        "shops/<int:shop_id>/products/<int:pk>/",
        views.ProductDetailView.as_view(),
        name="product-detail-by-id",
    ),
    path(
        "shops/<int:shop_id>/products/create/",
        views.ProductCreateView.as_view(),
        name="product-create-by-id",
    ),
    path(
        "shops/<int:shop_id>/papers/create/",
        views.PaperCreateView.as_view(),
        name="paper-create-by-id",
    ),
    path(
        "quotes/",
        views.QuoteRequestListCreateView.as_view(),
        name="quote-list-create",
    ),
    path(
        "quotes/<int:pk>/",
        views.QuoteRequestDetailView.as_view(),
        name="quote-detail",
    ),
    path(
        "quotes/<int:quote_pk>/items/",
        views.QuoteItemCreateView.as_view(),
        name="quote-item-create",
    ),
    path(
        "quotes/<int:quote_pk>/items/<int:pk>/",
        views.QuoteItemDestroyView.as_view(),
        name="quote-item-destroy",
    ),
    path(
        "quotes/<int:pk>/submit/",
        views.QuoteSubmitView.as_view(),
        name="quote-submit",
    ),
    path(
        "quotes/<int:pk>/price/",
        views.QuotePriceView.as_view(),
        name="quote-price",
    ),
]
