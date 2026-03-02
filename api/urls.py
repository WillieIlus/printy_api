"""
API URL configuration with DRF routers.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views

# Public router (no auth required for read)
public_router = DefaultRouter()
public_router.register(r"public/shops", views.PublicShopViewSet, basename="public-shop")

# Quote requests (buyer + seller actions)
quote_router = DefaultRouter()
quote_router.register(r"quote-requests", views.QuoteRequestViewSet, basename="quote-request")
quote_router.register(r"quote-drafts", views.QuoteDraftViewSet, basename="quote-draft")

# Seller router (shop-scoped)
# Shops are registered at root; nested resources use custom paths
seller_router = DefaultRouter()
seller_router.register(r"shops", views.ShopViewSet, basename="shop")

finishing_category_router = DefaultRouter()
finishing_category_router.register(r"finishing-categories", views.FinishingCategoryViewSet, basename="finishing-category")

urlpatterns = [
    path("", include(public_router.urls)),
    path("", include(finishing_category_router.urls)),
    path("public/products/", views.PublicAllProductsView.as_view(), name="public-all-products"),
    path("", include(quote_router.urls)),
    path("", include(seller_router.urls)),
    # Profile (User as Profile)
    path("profiles/me/", views.ProfileMeView.as_view(), name="profile-me"),
    path("profiles/", views.ProfileCreateView.as_view(), name="profile-create"),
    # Me (buyer) — favorites
    path(
        "me/favorites/",
        views.MeFavoritesViewSet.as_view({"get": "list", "post": "create"}),
        name="me-favorites",
    ),
    path(
        "me/favorites/<int:shop_id>/",
        views.MeFavoritesViewSet.as_view({"delete": "destroy"}),
        name="me-favorite-detail",
    ),
    # Shop rating (buyer) — requires eligible QuoteRequest
    path(
        "shops/<int:shop_id>/rate/",
        views.ShopRateView.as_view(),
        name="shop-rate",
    ),
    # Nested: quote-request items
    path(
        "quote-requests/<int:quote_request_pk>/items/",
        views.QuoteRequestItemViewSet.as_view({"get": "list", "post": "create"}),
        name="quote-request-items",
    ),
    path(
        "quote-requests/<int:quote_request_pk>/items/<int:pk>/",
        views.QuoteRequestItemViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}
        ),
        name="quote-request-item-detail",
    ),
    # Nested: quote-draft items (same logic, under quote-drafts)
    path(
        "quote-drafts/<int:quote_draft_pk>/items/",
        views.QuoteDraftItemViewSet.as_view({"get": "list", "post": "create"}),
        name="quote-draft-items",
    ),
    path(
        "quote-drafts/<int:quote_draft_pk>/items/<int:pk>/",
        views.QuoteDraftItemViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}
        ),
        name="quote-draft-item-detail",
    ),
    # Seller nested: shop machines, papers, finishing-rates, materials, products
    # Support both shop_id (e.g. /shops/1/products/) and shop_slug (e.g. /shops/my-shop/products/)
    path(
        "shops/<int:shop_id>/machines/",
        views.ShopMachineViewSet.as_view(
            {"get": "list", "post": "create"}
        ),
        name="shop-machines-by-id",
    ),
    path(
        "shops/<int:shop_id>/machines/<int:pk>/",
        views.ShopMachineViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-machine-detail-by-id",
    ),
    path(
        "shops/<int:shop_id>/papers/",
        views.ShopPaperViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-papers-by-id",
    ),
    path(
        "shops/<int:shop_id>/papers/<int:pk>/",
        views.ShopPaperViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-paper-detail-by-id",
    ),
    path(
        "shops/<int:shop_id>/finishing-rates/",
        views.ShopFinishingRateViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-finishing-rates-by-id",
    ),
    path(
        "shops/<int:shop_id>/finishing-rates/<int:pk>/",
        views.ShopFinishingRateViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-finishing-rate-detail-by-id",
    ),
    path(
        "shops/<int:shop_id>/materials/",
        views.ShopMaterialViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-materials-by-id",
    ),
    path(
        "shops/<int:shop_id>/materials/<int:pk>/",
        views.ShopMaterialViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-material-detail-by-id",
    ),
    path(
        "shops/<int:shop_id>/products/",
        views.ShopProductViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-products-by-id",
    ),
    path(
        "shops/<int:shop_id>/products/<int:pk>/",
        views.ShopProductViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-product-detail-by-id",
    ),
    path(
        "shops/<slug:shop_slug>/machines/",
        views.ShopMachineViewSet.as_view(
            {"get": "list", "post": "create"}
        ),
        name="shop-machines",
    ),
    path(
        "shops/<slug:shop_slug>/machines/<int:pk>/",
        views.ShopMachineViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-machine-detail",
    ),
    path(
        "shops/<slug:shop_slug>/papers/",
        views.ShopPaperViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-papers",
    ),
    path(
        "shops/<slug:shop_slug>/papers/<int:pk>/",
        views.ShopPaperViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-paper-detail",
    ),
    path(
        "shops/<slug:shop_slug>/finishing-rates/",
        views.ShopFinishingRateViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-finishing-rates",
    ),
    path(
        "shops/<slug:shop_slug>/finishing-rates/<int:pk>/",
        views.ShopFinishingRateViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-finishing-rate-detail",
    ),
    path(
        "shops/<slug:shop_slug>/materials/",
        views.ShopMaterialViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-materials",
    ),
    path(
        "shops/<slug:shop_slug>/materials/<int:pk>/",
        views.ShopMaterialViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-material-detail",
    ),
    path(
        "shops/<slug:shop_slug>/products/",
        views.ShopProductViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-products",
    ),
    path(
        "shops/<slug:shop_slug>/products/<int:pk>/",
        views.ShopProductViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-product-detail",
    ),
    # Product images (shop-scoped)
    path(
        "shops/<slug:shop_slug>/products/<int:product_pk>/images/",
        views.ShopProductImageViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-product-images",
    ),
    path(
        "shops/<slug:shop_slug>/products/<int:product_pk>/images/<int:pk>/",
        views.ShopProductImageViewSet.as_view(
            {"get": "retrieve", "delete": "destroy", "patch": "partial_update"}
        ),
        name="shop-product-image-detail",
    ),
    # Printing rates (machine-scoped)
    path(
        "machines/<int:machine_id>/printing-rates/",
        views.MachinePrintingRateViewSet.as_view({"get": "list", "post": "create"}),
        name="machine-printing-rates",
    ),
    path(
        "machines/<int:machine_id>/printing-rates/<int:pk>/",
        views.MachinePrintingRateViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="machine-printing-rate-detail",
    ),
]
