"""
API URL configuration with DRF routers.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import account_admin_views, dashboard_views, payment_views, public_matching_views, quote_views, views, workflow_views
from .workflow_views import GuestQuoteRequestView
from .seo_views import (
    SEOProductDetailView,
    SEOProductsView,
    SEORoutesView,
)
from jobs.views import (
    JobFileApproveView,
    JobFileDownloadView,
    JobFilePrintReadyView,
    JobFileRejectView,
    JobFileRevisionView,
    JobAssignmentAcceptView,
    JobAssignmentCompletedView,
    JobAssignmentFinishingView,
    JobAssignmentInProductionView,
    JobAssignmentIssueView,
    JobAssignmentReadyView,
    JobAssignmentRejectView,
    JobStatusEventListView,
    ManagedJobArtworkConfirmationRequestView,
    ManagedJobArtworkConfirmationResponseView,
    ManagedJobArtworkUploadView,
    ManagedJobFileListView,
    ManagedJobListView,
    ManagedJobProofUploadView,
    ManagedJobReorderView,
    ManagerJobProofApprovalView,
    PublicManagedJobTrackingView,
    PublicJobView,
    ShopAssignmentListView,
)
from notifications.views import NotificationViewSet

# Public router (no auth required for read)
public_router = DefaultRouter()
public_router.register(r"public/shops", views.PublicShopViewSet, basename="public-shop")

# Quote marketplace — customer vs shop separation
quote_router = DefaultRouter()
quote_router.register(r"quote-requests", quote_views.CustomerQuoteRequestViewSet, basename="quote-request")
quote_router.register(r"quote-drafts", views.CalculatorDraftViewSet, basename="quote-draft")
quote_router.register(r"sent-quotes", quote_views.QuoteViewSet, basename="sent-quote")

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

notifications_router = DefaultRouter()
notifications_router.register(r"", NotificationViewSet, basename="notification")
client_messages_router = DefaultRouter()
client_messages_router.register(r"client/messages", quote_views.ClientMessageInboxViewSet, basename="client-message")
shop_messages_router = DefaultRouter()
shop_messages_router.register(r"shop/messages", quote_views.ShopMessageInboxViewSet, basename="shop-message")

urlpatterns = [
    path("setup-status/", workflow_views.SetupStatusCompatView.as_view(), name="setup-status-compat"),
    path("shops/<slug:shop_slug>/setup-status/", workflow_views.ShopSetupStatusCompatView.as_view(), name="shop-setup-status-compat"),
    path("calculator/config/", workflow_views.CalculatorConfigView.as_view(), name="calculator-config"),
    path("for-shops/rate-wizard/public-config/", workflow_views.ForShopsRateWizardPublicConfigView.as_view(), name="for-shops-rate-wizard-public-config"),
    path("for-shops/rate-wizard/public-preview/", workflow_views.ForShopsRateWizardPublicPreviewView.as_view(), name="for-shops-rate-wizard-public-preview"),
    path("for-shops/rate-card/public-config/", workflow_views.ForShopsMvpRateCardPublicConfigView.as_view(), name="for-shops-rate-card-public-config"),
    path("for-shops/rate-card/public-preview/", workflow_views.ForShopsMvpRateCardPublicPreviewView.as_view(), name="for-shops-rate-card-public-preview"),
    path("for-shops/rate-card/save/", workflow_views.ForShopsMvpRateCardSaveView.as_view(), name="for-shops-rate-card-save"),
    path("for-shops/rate-wizard/config/", workflow_views.ForShopsRateWizardConfigView.as_view(), name="for-shops-rate-wizard-config"),
    path("for-shops/rate-wizard/preview/", workflow_views.ForShopsRateWizardPreviewView.as_view(), name="for-shops-rate-wizard-preview"),
    path("for-shops/rate-wizard/save-step/", workflow_views.ForShopsRateWizardSaveStepView.as_view(), name="for-shops-rate-wizard-save-step"),
    path("for-shops/rate-wizard/complete/", workflow_views.ForShopsRateWizardCompleteView.as_view(), name="for-shops-rate-wizard-complete"),
    path("shops/rate-card/setup/", workflow_views.ShopMvpRateCardSetupView.as_view(), name="shop-rate-card-setup"),
    path("shops/rate-card/onboarding-complete/", workflow_views.ShopMvpRateCardCompleteView.as_view(), name="shop-rate-card-complete"),
    path("calculator/public-preview/", workflow_views.CalculatorConfigPreviewView.as_view(), name="calculator-public-preview"),
    path("public/print-managers/recommended/", workflow_views.RecommendedPrintManagerListView.as_view(), name="public-print-managers-recommended"),
    path("intake/recommended-managers/", workflow_views.RecommendedPrintManagerListView.as_view(), name="intake-recommended-managers"),
    path("intake/submit/", workflow_views.IntakeSubmitView.as_view(), name="intake-submit"),
    path("calculator/preview/", workflow_views.CalculatorPreviewView.as_view(), name="calculator-preview"),
    path("calculator/booklet-preview/", workflow_views.BookletCalculatorPreviewView.as_view(), name="calculator-booklet-preview"),
    path("calculator/large-format-preview/", workflow_views.LargeFormatCalculatorPreviewView.as_view(), name="calculator-large-format-preview"),
    path("calculator/guest-drafts/", workflow_views.GuestCalculatorDraftUpsertView.as_view(), name="calculator-guest-drafts"),
    path("calculator/drafts/claim/", workflow_views.GuestCalculatorDraftClaimView.as_view(), name="calculator-draft-claim"),
    path("calculator/artwork-upload/", workflow_views.GuestArtworkUploadView.as_view(), name="calculator-artwork-upload"),
    path("calculator/artwork-upload/<str:token>/", workflow_views.GuestArtworkUploadDetailView.as_view(), name="calculator-artwork-upload-detail"),
    path("calculator/artwork-upload/<str:token>/preview/", workflow_views.GuestArtworkUploadPreviewView.as_view(), name="calculator-artwork-upload-preview"),
    path("calculator/drafts/", workflow_views.CalculatorDraftListCreateView.as_view(), name="calculator-drafts"),
    path("calculator/drafts/<int:pk>/", workflow_views.CalculatorDraftDetailView.as_view(), name="calculator-draft-detail"),
    path("calculator/drafts/<int:pk>/send/", workflow_views.CalculatorDraftSendView.as_view(), name="calculator-draft-send"),
    path("calculator/drafts/<int:pk>/direct-shop/submit/", workflow_views.DirectShopDraftSubmitView.as_view(), name="calculator-draft-direct-shop-submit"),
    path("partner/quotes/", workflow_views.PartnerQuoteListView.as_view(), name="partner-quote-list"),
    path("partner/quotes/preview/", workflow_views.PartnerQuotePreviewView.as_view(), name="partner-quote-preview"),
    path("partner/production-matches/", workflow_views.PartnerProductionMatchView.as_view(), name="partner-production-matches"),
    path("partner/production-options/", workflow_views.ProductionOptionCreateView.as_view(), name="partner-production-options"),
    path("partner/quotes/create/", workflow_views.PartnerQuoteCreateView.as_view(), name="partner-quote-create"),
    path("dashboard/partner/quotes/create/", workflow_views.PartnerQuoteCreateView.as_view(), name="dashboard-partner-quote-create"),
    path("workflow/quote-requests/", workflow_views.QuoteRequestListView.as_view(), name="workflow-quote-request-list"),
    path("workflow/quote-requests/<int:pk>/", workflow_views.QuoteRequestDetailView.as_view(), name="workflow-quote-request-detail"),
    path("client/requests/<int:pk>/", workflow_views.ClientQuoteRequestDetailView.as_view(), name="client-quote-request-detail"),
    path("shop/requests/<int:pk>/", workflow_views.QuoteRequestDetailView.as_view(), name="shop-quote-request-detail"),
    path("client/responses/", workflow_views.ClientResponseListView.as_view(), name="client-response-list"),
    path("client/responses/<int:response_id>/accept/", workflow_views.ClientResponseAcceptView.as_view(), name="client-response-accept"),
    path("quotes/offline-claim/", dashboard_views.OfflineQuoteClaimView.as_view(), name="offline-quote-claim"),
    path("quotes/<int:quote_id>/accept/", payment_views.QuoteAcceptView.as_view(), name="quote-accept-payment"),
    path("payments/stk-push/", payment_views.PaymentInitiateSTKView.as_view(), name="payment-stk-push"),
    path("payments/mpesa-callback/", payment_views.MpesaCallbackView.as_view(), name="payment-mpesa-callback"),
    path("payments/mpesa/callback/", payment_views.MpesaCallbackView.as_view(), name="payment-mpesa-callback-legacy"),
    path("client/responses/<int:response_id>/reject/", workflow_views.ClientResponseRejectView.as_view(), name="client-response-reject"),
    path("client/responses/<int:response_id>/reply/", workflow_views.ClientResponseReplyView.as_view(), name="client-response-reply"),
    path("shop/responses/<int:response_id>/reply/", workflow_views.ShopResponseReplyView.as_view(), name="shop-response-reply"),
    path("quote-requests/<int:request_id>/responses/", workflow_views.QuoteResponseListCreateView.as_view(), name="quote-request-response-list-create"),
    path("workflow/quote-responses/<int:pk>/", workflow_views.QuoteResponseDetailView.as_view(), name="workflow-quote-response-detail"),
    path("dashboard/shop-home/", workflow_views.ShopHomeDashboardView.as_view(), name="dashboard-shop-home"),
    path("dashboard/counts/", dashboard_views.DashboardCountsView.as_view(), name="dashboard-counts"),
    path("dashboard/admin/", dashboard_views.AdminDashboardHomeView.as_view(), name="dashboard-admin-home"),
    path(
        "dashboard/admin/brokers/<int:user_id>/active/",
        account_admin_views.BrokerProfileActiveToggleView.as_view(),
        name="dashboard-admin-broker-active-toggle",
    ),
    path("dashboard/client-home/", dashboard_views.ClientDashboardHomeView.as_view(), name="dashboard-client-home"),
    path("dashboard/partner-home/", dashboard_views.PartnerDashboardHomeView.as_view(), name="dashboard-partner-home"),
    path("dashboard/production-home/", dashboard_views.ProductionDashboardHomeView.as_view(), name="dashboard-production-home"),
    path("dashboard/client/quotes/", dashboard_views.ClientQuoteListView.as_view(), name="dashboard-client-quotes"),
    path("dashboard/client/quotes/<int:pk>/", dashboard_views.ClientQuoteDetailView.as_view(), name="dashboard-client-quote-detail"),
    path("dashboard/client/jobs/", dashboard_views.ClientJobListView.as_view(), name="dashboard-client-jobs"),
    path("dashboard/client/jobs/<int:pk>/", dashboard_views.ClientJobDetailView.as_view(), name="dashboard-client-job-detail"),
    path("jobs/", dashboard_views.ClientJobListView.as_view(), name="client-jobs-compat"),
    path("dashboard/client/payments/", dashboard_views.ClientPaymentListView.as_view(), name="dashboard-client-payments"),
    path("dashboard/manager/requests/", dashboard_views.PartnerQuoteListDetailView.as_view(), name="dashboard-manager-requests"),
    path("dashboard/manager/quote-requests/<int:pk>/prefill/", dashboard_views.ManagerQuoteRequestPrefillView.as_view(), name="dashboard-manager-quote-request-prefill"),
    path("dashboard/manager/quote-requests/<int:pk>/preview-pricing/", dashboard_views.ManagerQuoteRequestPricingPreviewView.as_view(), name="dashboard-manager-quote-request-preview-pricing"),
    path("dashboard/manager/jobs/<int:job_id>/proof-approval/", ManagerJobProofApprovalView.as_view(), name="dashboard-manager-job-proof-approval"),
    path("dashboard/partner/quotes/", dashboard_views.PartnerQuoteListDetailView.as_view(), name="dashboard-partner-quotes"),
    path("dashboard/partner/quotes/<int:pk>/", dashboard_views.PartnerQuoteListDetailView.as_view(), name="dashboard-partner-quote-detail"),
    path("dashboard/partner/quotes/<int:pk>/attach-client/", dashboard_views.PartnerQuoteAttachClientView.as_view(), name="dashboard-partner-quote-attach-client"),
    path("dashboard/partner/quotes/<int:pk>/send-to-client/", dashboard_views.PartnerQuoteSendToClientView.as_view(), name="dashboard-partner-quote-send-to-client"),
    path("dashboard/partner/quotes/<int:pk>/shop-options/", dashboard_views.PartnerAssignedRequestShopOptionsView.as_view(), name="dashboard-partner-quote-shop-options"),
    path("dashboard/partner/quotes/<int:pk>/prepare/", dashboard_views.PartnerAssignedRequestQuoteCreateView.as_view(), name="dashboard-partner-quote-prepare"),
    path("dashboard/partner/market-rates/", dashboard_views.PartnerMarketRateListView.as_view(), name="dashboard-partner-market-rates"),
    path("dashboard/partner/profile/", dashboard_views.PartnerDashboardProfileView.as_view(), name="dashboard-partner-profile"),
    path("dashboard/partner/jobs/", dashboard_views.PartnerJobListDetailView.as_view(), name="dashboard-partner-jobs"),
    path("dashboard/partner/jobs/<int:pk>/", dashboard_views.PartnerJobListDetailView.as_view(), name="dashboard-partner-job-detail"),
    path("dashboard/partner/jobs/<int:pk>/dispatch/", payment_views.JobDispatchView.as_view(), name="dashboard-partner-job-dispatch"),
    path("dashboard/partner/clients/", dashboard_views.PartnerClientListView.as_view(), name="dashboard-partner-clients"),
    path("dashboard/partner/production-shops/", dashboard_views.PartnerProductionShopListView.as_view(), name="dashboard-partner-production-shops"),
    path("dashboard/partner/payments/", dashboard_views.PartnerPaymentListView.as_view(), name="dashboard-partner-payments"),
    path("dashboard/production/jobs/", dashboard_views.ProductionJobListDetailView.as_view(), name="dashboard-production-jobs"),
    path("dashboard/production/jobs/<int:pk>/", dashboard_views.ProductionJobListDetailView.as_view(), name="dashboard-production-job-detail"),
    path("dashboard/printshop/jobs/<int:job_id>/breakdown/", dashboard_views.PrintShopJobBreakdownView.as_view(), name="dashboard-printshop-job-breakdown"),
    path("dashboard/production/pricing/", dashboard_views.ProductionPricingListView.as_view(), name="dashboard-production-pricing"),
    path("dashboard/production/paper-stock/", dashboard_views.ProductionPaperStockListView.as_view(), name="dashboard-production-paper-stock"),
    path("dashboard/production/finishings/", dashboard_views.ProductionFinishingListView.as_view(), name="dashboard-production-finishings"),
    path("dashboard/production/payments/", dashboard_views.ProductionPaymentListView.as_view(), name="dashboard-production-payments"),
    path("dashboard/calculator/preview/", workflow_views.DashboardCalculatorPreviewView.as_view(), name="dashboard-calculator-preview"),
    path("shops/<slug:shop_slug>/dashboard-home/", workflow_views.ShopHomeDashboardView.as_view(), name="shop-dashboard-home"),
    path("quote-requests/guest-send/", GuestQuoteRequestView.as_view(), name="guest-quote-request-send"),
    # Production tracking (jobs, processes, dashboard)
    path("", include("production.urls")),
    path("", include(public_router.urls)),
    path("public/products/", views.PublicAllProductsView.as_view(), name="public-all-products"),
    path("public/match-shops/", public_matching_views.PublicMatchShopsView.as_view(), name="public-match-shops"),
    path("public/calculator/preview/", public_matching_views.PublicMatchShopsView.as_view(), name="public-calculator-preview"),
    path("public/match-shops/booklet/", public_matching_views.PublicMatchBookletShopsView.as_view(), name="public-match-booklet-shops"),
    # SEO (public, no auth — for sitemap and dynamic pages)
    path("seo/products/", SEOProductsView.as_view(), name="seo-products"),
    path("seo/products/<slug:slug>/", SEOProductDetailView.as_view(), name="seo-product-detail"),
    path("seo/routes/", SEORoutesView.as_view(), name="seo-routes"),
    path("", include(quote_router.urls)),
    path("", include(quotes_router.urls)),
    path("managed-jobs/", ManagedJobListView.as_view(), name="managed-job-list"),
    path("managed-jobs/<int:pk>/payments/", payment_views.ManagedJobPaymentsListView.as_view(), name="managed-job-payments"),
    path("managed-jobs/<int:pk>/settlement/", payment_views.ManagedJobSettlementView.as_view(), name="managed-job-settlement"),
    path("managed-jobs/<int:pk>/payouts/release/", payment_views.ManagedJobPayoutReleaseView.as_view(), name="managed-job-payout-release"),
    path("managed-jobs/<int:pk>/payments/mpesa/stk-push/", payment_views.ManagedJobMpesaSTKPushView.as_view(), name="managed-job-mpesa-stk-push"),
    path("managed-jobs/<int:pk>/payments/mpesa/query/", payment_views.ManagedJobMpesaQueryView.as_view(), name="managed-job-mpesa-query"),
    path("managed-jobs/<int:pk>/files/", ManagedJobFileListView.as_view(), name="managed-job-files"),
    path("managed-jobs/<int:pk>/artwork-confirmation/request/", ManagedJobArtworkConfirmationRequestView.as_view(), name="managed-job-artwork-confirmation-request"),
    path("managed-jobs/<int:pk>/artwork-confirmation/respond/", ManagedJobArtworkConfirmationResponseView.as_view(), name="managed-job-artwork-confirmation-respond"),
    path("managed-jobs/<int:pk>/files/artwork/", ManagedJobArtworkUploadView.as_view(), name="managed-job-artwork-upload"),
    path("managed-jobs/<int:pk>/files/proofs/", ManagedJobProofUploadView.as_view(), name="managed-job-proof-upload"),
    path("managed-jobs/<int:pk>/reorder/", ManagedJobReorderView.as_view(), name="managed-job-reorder"),
    path("managed-jobs/<int:pk>/events/", JobStatusEventListView.as_view(), name="managed-job-events"),
    path("job-files/<int:pk>/download/", JobFileDownloadView.as_view(), name="job-file-download"),
    path(
        "quote-request-attachments/<int:pk>/download/",
        quote_views.QuoteRequestAttachmentDownloadView.as_view(),
        name="quote-request-attachment-download",
    ),
    path("job-files/<int:pk>/approve/", JobFileApproveView.as_view(), name="job-file-approve"),
    path("job-files/<int:pk>/reject/", JobFileRejectView.as_view(), name="job-file-reject"),
    path("job-files/<int:pk>/request-revision/", JobFileRevisionView.as_view(), name="job-file-request-revision"),
    path("job-files/<int:pk>/mark-print-ready/", JobFilePrintReadyView.as_view(), name="job-file-print-ready"),
    path("shop/assignments/", ShopAssignmentListView.as_view(), name="shop-assignments"),
    path("shop/assignments/<int:pk>/accept/", JobAssignmentAcceptView.as_view(), name="shop-assignment-accept-compat"),
    path("shop/assignments/<int:pk>/complete/", JobAssignmentCompletedView.as_view(), name="shop-assignment-complete-compat"),
    path("job-assignments/<int:pk>/accept/", JobAssignmentAcceptView.as_view(), name="job-assignment-accept"),
    path("job-assignments/<int:pk>/reject/", JobAssignmentRejectView.as_view(), name="job-assignment-reject"),
    path("job-assignments/<int:pk>/mark-in-production/", JobAssignmentInProductionView.as_view(), name="job-assignment-in-production"),
    path("job-assignments/<int:pk>/mark-finishing/", JobAssignmentFinishingView.as_view(), name="job-assignment-finishing"),
    path("job-assignments/<int:pk>/mark-ready/", JobAssignmentReadyView.as_view(), name="job-assignment-ready"),
    path("job-assignments/<int:pk>/mark-completed/", JobAssignmentCompletedView.as_view(), name="job-assignment-completed"),
    path("job-assignments/<int:pk>/report-issue/", JobAssignmentIssueView.as_view(), name="job-assignment-report-issue"),
    path("shops/nearby/", views.ShopsNearbyView.as_view(), name="shops-nearby"),
    path("shops/<slug:shop_slug>/incoming-requests/", include(incoming_router.urls)),
    path("", include(seller_router.urls)),
    path("public/managed-jobs/track/<uuid:token>/", PublicManagedJobTrackingView.as_view(), name="public-managed-job-track"),
    path("managed-jobs/public/<uuid:token>/", PublicManagedJobTrackingView.as_view(), name="managed-job-public-track"),
    path("public/job/<str:token>/", PublicJobView.as_view(), name="public-job"),
    path("share/<str:token>/", views.QuoteSharePublicView.as_view(), name="quote-share-public"),
    # Profile (User as Profile)
    path("users/me/", views.UserMeCompatView.as_view(), name="user-me-compat"),
    path("profiles/me/", views.ProfileMeView.as_view(), name="profile-me"),
    path("profiles/me/avatar/", views.ProfileAvatarUploadView.as_view(), name="profile-avatar-upload"),
    path("profiles/", views.ProfileCreateView.as_view(), name="profile-create"),
    path("profiles/<int:pk>/", views.ProfileDetailView.as_view(), name="profile-detail"),
    path("me/notifications/", include(notifications_router.urls)),
    path("", include(client_messages_router.urls)),
    path("", include(shop_messages_router.urls)),
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
        "sent-quotes/<int:quote_pk>/attachments/",
        quote_views.QuoteAttachmentViewSet.as_view({"get": "list", "post": "create"}),
        name="shop-quote-attachments",
    ),
    path(
        "sent-quotes/<int:quote_pk>/attachments/<int:pk>/",
        quote_views.QuoteAttachmentViewSet.as_view({"get": "retrieve", "delete": "destroy"}),
        name="shop-quote-attachment-detail",
    ),
    # Nested: quote-draft items (same logic, under quote-drafts)
    path(
        "quote-drafts/<int:calculator_draft_pk>/items/",
        views.CalculatorDraftItemViewSet.as_view({"get": "list", "post": "create"}),
        name="quote-draft-items",
    ),
    path(
        "quote-drafts/<int:calculator_draft_pk>/items/<int:pk>/",
        views.CalculatorDraftItemViewSet.as_view(
            {"get": "retrieve", "patch": "partial_update", "put": "update", "delete": "destroy"}
        ),
        name="quote-draft-item-detail",
    ),
    path(
        "quote-drafts/<int:calculator_draft_pk>/items/<int:pk>/request-quote/",
        views.CalculatorDraftItemRequestQuoteView.as_view(),
        name="quote-draft-item-request-quote",
    ),
    path(
        "quote-drafts/<int:calculator_draft_pk>/items/<int:quote_item_pk>/attachments/",
        views.CalculatorDraftItemAttachmentViewSet.as_view({"get": "list", "post": "create"}),
        name="quote-draft-item-attachments",
    ),
    path(
        "quote-drafts/<int:calculator_draft_pk>/items/<int:quote_item_pk>/attachments/<int:pk>/",
        views.CalculatorDraftItemAttachmentViewSet.as_view({"get": "retrieve", "delete": "destroy"}),
        name="quote-draft-item-attachment-detail",
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
        "public/shops/<slug:slug>/quote-preview/",
        public_matching_views.PublicShopQuotePreviewView.as_view(),
        name="public-shop-quote-preview",
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
