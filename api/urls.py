"""
API URL configuration with DRF routers.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import public_matching_views, quote_views, views, workflow_views
from .analytics_views import AnalyticsEventIngestView
from .admin_views import (
    AnalyticsDashboardSummaryView,
    AnalyticsErrorAnalyticsView,
    AnalyticsFunnelView,
    AnalyticsLocationBreakdownView,
    AnalyticsTimeSeriesView,
    AnalyticsTopMetricsView,
)
from .seo_views import (
    SEOLocationDetailView,
    SEOLocationProductView,
    SEOLocationProductsView,
    SEOLocationsView,
    SEOProductDetailView,
    SEOProductsView,
    SEORoutesView,
)
from gallery.views import (
    GalleryCategoryViewSet,
    GalleryProductViewSet,
    ProductGalleryView,
)
from jobs.views import JobClaimViewSet, JobRequestViewSet, PublicJobView
from notifications.views import NotificationViewSet
from subscriptions import views as subscriptions_views

# Public router (no auth required for read)
public_router = DefaultRouter()
public_router.register(r"public/shops", views.PublicShopViewSet, basename="public-shop")

# Quote marketplace — customer vs shop separation
quote_router = DefaultRouter()
quote_router.register(r"quote-requests", quote_views.CustomerQuoteRequestViewSet, basename="quote-request")
quote_router.register(r"quote-drafts", views.QuoteDraftViewSet, basename="quote-draft")
quote_router.register(r"quote-draft-files", views.QuoteDraftFileViewSet, basename="quote-draft-file")
quote_router.register(r"sent-quotes", quote_views.ShopQuoteViewSet, basename="sent-quote")

# Staff quoting API
quotes_router = DefaultRouter()
quotes_router.register(r"quotes", views.QuoteViewSet, basename="quote")

# Shop incoming quote requests (nested under shop)
incoming_router = DefaultRouter()
incoming_router.register(r"", quote_views.IncomingRequestViewSet, basename="incoming-request")

# Seller router (shop-scoped)
# Shops are registered at root; nested resources use custom paths
seller_router = DefaultRouter()
seller_router.register(r"shops", views.ShopViewSet, basename="shop")

finishing_category_router = DefaultRouter()
finishing_category_router.register(r"finishing-categories", views.FinishingCategoryViewSet, basename="finishing-category")

job_requests_router = DefaultRouter()
job_requests_router.register(r"job-requests", JobRequestViewSet, basename="job-request")
job_claims_router = DefaultRouter()
job_claims_router.register(r"job-claims", JobClaimViewSet, basename="job-claim")

notifications_router = DefaultRouter()
notifications_router.register(r"", NotificationViewSet, basename="notification")

urlpatterns = [
    path("setup-status/", workflow_views.SetupStatusCompatView.as_view(), name="setup-status-compat"),
    path("shops/<slug:shop_slug>/setup-status/", workflow_views.ShopSetupStatusCompatView.as_view(), name="shop-setup-status-compat"),
    path("calculator/preview/", workflow_views.CalculatorPreviewView.as_view(), name="calculator-preview"),
    path("calculator/booklet-preview/", workflow_views.BookletCalculatorPreviewView.as_view(), name="calculator-booklet-preview"),
    path("calculator/large-format-preview/", workflow_views.LargeFormatCalculatorPreviewView.as_view(), name="calculator-large-format-preview"),
    path("calculator/drafts/", workflow_views.QuoteDraftListCreateView.as_view(), name="calculator-drafts"),
    path("calculator/drafts/<int:pk>/", workflow_views.QuoteDraftDetailView.as_view(), name="calculator-draft-detail"),
    path("calculator/drafts/<int:pk>/send/", workflow_views.QuoteDraftSendView.as_view(), name="calculator-draft-send"),
    path("workflow/quote-requests/", workflow_views.QuoteRequestListView.as_view(), name="workflow-quote-request-list"),
    path("workflow/quote-requests/<int:pk>/", workflow_views.QuoteRequestDetailView.as_view(), name="workflow-quote-request-detail"),
    path("quote-requests/<int:request_id>/responses/", workflow_views.QuoteResponseListCreateView.as_view(), name="quote-request-response-list-create"),
    path("workflow/quote-responses/<int:pk>/", workflow_views.QuoteResponseDetailView.as_view(), name="workflow-quote-response-detail"),
    path("dashboard/shop-home/", workflow_views.ShopHomeDashboardView.as_view(), name="dashboard-shop-home"),
    path("analytics/events/", AnalyticsEventIngestView.as_view(), name="analytics-events"),
    path("admin/analytics/summary/", AnalyticsDashboardSummaryView.as_view(), name="admin-analytics-summary"),
    path("admin/analytics/timeseries/", AnalyticsTimeSeriesView.as_view(), name="admin-analytics-timeseries"),
    path("admin/analytics/top-metrics/", AnalyticsTopMetricsView.as_view(), name="admin-analytics-top-metrics"),
    path("admin/analytics/funnel/", AnalyticsFunnelView.as_view(), name="admin-analytics-funnel"),
    path("admin/analytics/locations/", AnalyticsLocationBreakdownView.as_view(), name="admin-analytics-locations"),
    path("admin/analytics/errors/", AnalyticsErrorAnalyticsView.as_view(), name="admin-analytics-errors"),
    path("products/gallery/", ProductGalleryView.as_view(), name="products-gallery"),
    # Production tracking (jobs, processes, dashboard)
    path("", include("production.urls")),
    # Demo calculator (public, no auth)
    path("", include("demo.urls")),
    path("", include(public_router.urls)),
    path("", include(finishing_category_router.urls)),
    path("public/products/", views.PublicAllProductsView.as_view(), name="public-all-products"),
    path("public/match-shops/", public_matching_views.PublicMatchShopsView.as_view(), name="public-match-shops"),
    # SEO (public, no auth — for sitemap and dynamic pages)
    path("seo/locations/", SEOLocationsView.as_view(), name="seo-locations"),
    path("seo/locations/<slug:slug>/", SEOLocationDetailView.as_view(), name="seo-location-detail"),
    path("seo/locations/<slug:slug>/products/", SEOLocationProductsView.as_view(), name="seo-location-products"),
    path("seo/products/", SEOProductsView.as_view(), name="seo-products"),
    path("seo/products/<slug:slug>/", SEOProductDetailView.as_view(), name="seo-product-detail"),
    path("seo/locations/<slug:location_slug>/products/<slug:product_slug>/", SEOLocationProductView.as_view(), name="seo-location-product"),
    path("seo/routes/", SEORoutesView.as_view(), name="seo-routes"),
    path("", include(quote_router.urls)),
    path("", include(quotes_router.urls)),
    path("", include(job_requests_router.urls)),
    path("", include(job_claims_router.urls)),
    path("shops/nearby/", views.ShopsNearbyView.as_view(), name="shops-nearby"),
    path("shops/<slug:shop_slug>/incoming-requests/", include(incoming_router.urls)),
    path("", include(seller_router.urls)),
    path("public/job/<str:token>/", PublicJobView.as_view(), name="public-job"),
    path("share/<str:token>/", views.QuoteSharePublicView.as_view(), name="quote-share-public"),
    # Subscription & payments
    path(
        "subscription/plans/",
        subscriptions_views.SubscriptionPlanViewSet.as_view({"get": "list"}),
        name="subscription-plans",
    ),
    path(
        "shops/<slug:shop_slug>/subscription/",
        subscriptions_views.ShopSubscriptionView.as_view(),
        name="shop-subscription",
    ),
    path(
        "shops/<slug:shop_slug>/payments/mpesa/stk-push/",
        subscriptions_views.MpesaStkPushView.as_view(),
        name="mpesa-stk-push",
    ),
    path(
        "payments/mpesa/callback/",
        subscriptions_views.MpesaCallbackView.as_view(),
        name="mpesa-callback",
    ),
    # Profile (User as Profile)
    path("users/me/", views.UserMeCompatView.as_view(), name="user-me-compat"),
    path("profiles/me/", views.ProfileMeView.as_view(), name="profile-me"),
    path("profiles/me/avatar/", views.ProfileAvatarUploadView.as_view(), name="profile-avatar-upload"),
    path("profiles/", views.ProfileCreateView.as_view(), name="profile-create"),
    path("profiles/<int:pk>/", views.ProfileDetailView.as_view(), name="profile-detail"),
    path(
        "profiles/<int:profile_id>/social-links/",
        views.ProfileSocialLinkListCreateView.as_view(),
        name="profile-social-links",
    ),
    path("social-links/<int:pk>/", views.SocialLinkDetailView.as_view(), name="social-link-detail"),
    # Me (buyer) — favorites
    path(
        "me/favorites/",
        views.MeFavoritesViewSet.as_view({"get": "list", "post": "create"}),
        name="me-favorites",
    ),
    
    # Shop rating (buyer) — requires eligible QuoteRequest
    path("me/notifications/", include(notifications_router.urls)),
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
    path(
        "quote-requests/<int:quote_request_pk>/attachments/",
        quote_views.QuoteRequestAttachmentViewSet.as_view({"get": "list", "post": "create"}),
        name="quote-request-attachments",
    ),
    path(
        "quote-requests/<int:quote_request_pk>/attachments/<int:pk>/",
        quote_views.QuoteRequestAttachmentViewSet.as_view({"get": "retrieve", "delete": "destroy"}),
        name="quote-request-attachment-detail",
    ),
    path(
        "sent-quotes/<int:shop_quote_pk>/attachments/",
        quote_views.ShopQuoteAttachmentViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-quote-attachments",
    ),
    path(
        "sent-quotes/<int:shop_quote_pk>/attachments/<int:pk>/",
        quote_views.ShopQuoteAttachmentViewSet.as_view({"get": "retrieve", "delete": "destroy"}),
        name="shop-quote-attachment-detail",
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
    # Tweak-and-Add: Gallery → Tweak → Quote (creates tweaked instance with pricing)
    path(
        "quote-drafts/<int:draft_id>/tweak-and-add/",
        views.TweakAndAddView.as_view(),
        name="quote-draft-tweak-and-add",
    ),
    # Update a tweaked item (recompute pricing)
    path(
        "tweaked-items/<int:item_id>/",
        views.TweakedItemUpdateView.as_view(),
        name="tweaked-item-update",
    ),
    # Gallery product with full tweaking options (public, no auth)
    path(
        "public/shops/<slug:slug>/custom-options/",
        views.ShopCustomOptionsView.as_view(),
        name="public-shop-custom-options",
    ),
    path(
        "public/shops/<slug:slug>/calculator-preview/",
        public_matching_views.PublicShopCalculatorPreviewView.as_view(),
        name="public-shop-calculator-preview",
    ),
    path(
        "public/products/<int:pk>/options/",
        views.GalleryProductDetailView.as_view(),
        name="gallery-product-options",
    ),
    # Quote calculator (staff-only, live preview)
    path(
        "calculator/quote-item/",
        views.QuoteCalculatorView.as_view(),
        name="calculator-quote-item",
    ),
    # Staff: nested quote items
    path(
        "quotes/<int:quote_pk>/items/",
        views.QuoteItemViewSet.as_view({"get": "list", "post": "create"}),
        name="quote-items",
    ),
    path(
        "quotes/<int:quote_pk>/items/<int:pk>/",
        views.QuoteItemViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}
        ),
        name="quote-item-detail",
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
        "shops/<slug:shop_slug>/papers/<int:pk>/adjust/",
        views.ShopPaperViewSet.as_view({"post": "adjust"}),
        name="shop-paper-adjust",
    ),
    path(
        "shops/<slug:shop_slug>/hours/",
        views.ShopOpeningHoursViewSet.as_view({"get": "list"}),
        name="shop-hours",
    ),
    path(
        "shops/<slug:shop_slug>/hours/bulk/",
        views.ShopOpeningHoursBulkView.as_view(),
        name="shop-hours-bulk",
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
        "shops/<slug:shop_slug>/pricing/discounts/",
        views.ShopVolumeDiscountViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-pricing-discounts",
    ),
    path(
        "shops/<slug:shop_slug>/pricing/discounts/<int:pk>/",
        views.ShopVolumeDiscountViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="shop-pricing-discount-detail",
    ),
    # Gallery: products/categories + products (shop-scoped, slug lookup)
    path(
        "shops/<slug:shop_slug>/products/categories/",
        GalleryCategoryViewSet.as_view({"get": "list", "post": "create"}),
        name="gallery-categories",
    ),
    path(
        "shops/<slug:shop_slug>/products/categories/<slug:slug>/",
        GalleryCategoryViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="gallery-category-detail",
    ),
    path(
        "shops/<slug:shop_slug>/gallery/products/",
        GalleryProductViewSet.as_view({"get": "list", "post": "create"}),
        name="gallery-products",
    ),
    path(
        "shops/<slug:shop_slug>/gallery/products/<slug:slug>/",
        GalleryProductViewSet.as_view(
            {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
        ),
        name="gallery-product-detail",
    ),
    path(
        "shops/<slug:shop_slug>/gallery/products/<slug:slug>/calculate-price/",
        GalleryProductViewSet.as_view({"post": "calculate_price"}),
        name="gallery-product-calculate-price",
    ),
    path(
        "shops/<slug:shop_slug>/rate-card/",
        views.ShopRateCardView.as_view(),
        name="shop-rate-card",
    ),
    path(
        "shops/<slug:shop_slug>/rate-card-for-calculator/",
        views.ShopRateCardForCalculatorView.as_view(),
        name="shop-rate-card-for-calculator",
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
