"""
DRF viewsets and API views.
"""
import math

from django.shortcuts import get_object_or_404

from common.geo import haversine_km
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingCategory, FinishingRate, Material, PrintingRate
from django.db.models import Avg, Count
from quotes.choices import QuoteStatus
from quotes.models import QuoteItem, QuoteRequest, QuoteShareLink
from quotes.quote_engine import recalculate_and_lock_quote_request
from quotes.services import build_preview_price_response, calculate_quote_item
from quotes.whatsapp_formatter import format_quote_for_whatsapp
from shops.models import FavoriteShop, Shop, ShopRating

from .filters import QuoteFilterSet
from .permissions import IsQuoteRequestBuyer, IsQuoteRequestSeller, IsShopOwner, IsStaffUser, PublicReadOnly
from .serializers import QuoteCalculatorInputSerializer
from catalog.models import ProductImage
from .serializers import (
    QuoteSharePublicSerializer,
    CatalogProductSerializer,
    CatalogProductWithShopSerializer,
    FavoriteShopCreateSerializer,
    FavoriteShopSerializer,
    FinishingCategorySerializer,
    FinishingRateSerializer,
    GalleryProductOptionsSerializer,
    MachineSerializer,
    MaterialSerializer,
    PaperSerializer,
    PrintingRateSerializer,
    ProductImageSerializer,
    ProductImageUploadSerializer,
    ProductListSerializer,
    ProductSerializer,
    ProductWriteSerializer,
    ProfileSerializer,
    PublicShopListSerializer,
    QuoteCreateSerializer,
    QuoteDetailSerializer,
    QuoteItemAddSerializer,
    QuoteItemWithBreakdownSerializer,
    QuoteItemReadSerializer,
    QuoteItemWriteSerializer,
    QuoteCalculatorInputSerializer,
    QuoteRequestCreateSerializer,
    QuoteRequestPatchSerializer,
    QuoteRequestReadSerializer,
    ShopRatingSerializer,
    ShopRatingSummarySerializer,
    ShopSerializer,
    TweakAndAddSerializer,
    TweakedItemReadSerializer,
)


# ---------------------------------------------------------------------------
# Public / Buyer
# ---------------------------------------------------------------------------


class PublicShopViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/public/shops/ — list active shops. GET /api/public/shops/{slug}/catalog/ — catalog."""

    permission_classes = [AllowAny]
    serializer_class = PublicShopListSerializer
    lookup_field = "slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        return Shop.objects.filter(is_active=True).exclude(
            slug__isnull=True
        ).exclude(slug="")

    def get_serializer_class(self):
        if self.action == "catalog":
            return CatalogProductSerializer
        return PublicShopListSerializer

    @action(detail=True, methods=["get"], url_path="catalog")
    def catalog(self, request, slug=None):
        """GET /api/public/shops/{slug}/catalog/ — only PUBLISHED products from pricing-ready shops."""
        shop = self.get_object()
        products = Product.objects.filter(
            shop=shop,
            is_active=True,
            status="PUBLISHED",
        ).prefetch_related(
            "finishing_options__finishing_rate",
            "images",
        )
        products_data = CatalogProductSerializer(products, many=True).data
        shop_data = PublicShopListSerializer(shop).data
        return Response({"shop": shop_data, "products": products_data})

    @action(detail=True, methods=["get"], url_path="rating-summary")
    def rating_summary(self, request, slug=None):
        """GET /api/public/shops/{slug}/rating-summary/ — average stars and count."""
        shop = self.get_object()
        agg = ShopRating.objects.filter(shop=shop).aggregate(
            average=Avg("stars"), count=Count("id")
        )
        data = {
            "average": round(float(agg["average"] or 0), 2),
            "count": agg["count"] or 0,
        }
        return Response(ShopRatingSummarySerializer(data).data)


class PublicAllProductsView(APIView):
    """GET /api/public/products/ — only PUBLISHED products from pricing-ready active shops."""

    permission_classes = [AllowAny]

    def get(self, request):
        products = (
            Product.objects.filter(
                shop__is_active=True,
                shop__pricing_ready=True,
                is_active=True,
                status="PUBLISHED",
            )
            .select_related("shop")
            .prefetch_related(
                "finishing_options__finishing_rate",
                "images",
            )
        )
        data = CatalogProductWithShopSerializer(products, many=True).data
        return Response({"products": data})


class ShopsNearbyView(APIView):
    """
    GET /api/shops/nearby/?lat=...&lng=...&radius=10
    Bounding box pre-filter, then Haversine distance. Sorted by distance ascending.
    Optionally filter by exact radius (km). Returns distance_km per shop.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        lat_s = request.query_params.get("lat")
        lng_s = request.query_params.get("lng")
        radius_s = request.query_params.get("radius", "10")

        if lat_s is None or lng_s is None:
            return Response({"results": []})

        try:
            lat = float(lat_s)
            lng = float(lng_s)
            radius_km = float(radius_s)
        except (ValueError, TypeError):
            return Response({"results": []})

        if radius_km <= 0 or radius_km > 500:
            return Response({"results": []})

        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return Response({"results": []})

        lat_delta = radius_km / 111.0
        lng_scale = max(0.01, math.cos(math.radians(lat)))
        lng_delta = radius_km / (111.0 * lng_scale)

        lat_min = lat - lat_delta
        lat_max = lat + lat_delta
        lng_min = lng - lng_delta
        lng_max = lng + lng_delta

        shops = (
            Shop.objects.filter(
                is_active=True,
                latitude__isnull=False,
                longitude__isnull=False,
                latitude__gte=lat_min,
                latitude__lte=lat_max,
                longitude__gte=lng_min,
                longitude__lte=lng_max,
            )
            .exclude(slug__isnull=True)
            .exclude(slug="")
        )

        # Compute Haversine distance, filter by exact radius, sort by distance
        results_with_distance = []
        for shop in shops:
            shop_lat = float(shop.latitude)
            shop_lng = float(shop.longitude)
            dist_km = haversine_km(lat, lng, shop_lat, shop_lng)
            if dist_km <= radius_km:
                results_with_distance.append((shop, round(dist_km, 2)))

        results_with_distance.sort(key=lambda x: x[1])

        data = []
        for shop, dist_km in results_with_distance:
            item = PublicShopListSerializer(shop).data
            item["distance_km"] = dist_km
            data.append(item)

        return Response({"results": data})


# ---------------------------------------------------------------------------
# Profile (User as Profile - frontend compatibility)
# ---------------------------------------------------------------------------


class ProfileMeView(APIView):
    """GET/PATCH /api/profiles/me/ — current user as Profile-like structure."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = ProfileSerializer(request.user)
        return Response(serializer.data)

    def patch(self, request):
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProfileSerializer(request.user).data)


class ProfileCreateView(APIView):
    """POST /api/profiles/ — create profile (returns current user as profile)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Quote requests (buyer)
# ---------------------------------------------------------------------------


class QuoteRequestViewSet(viewsets.ModelViewSet):
    """
    Buyer: POST /api/quote-requests/ (create draft)
    Buyer: GET /api/quote-requests/ (list own), GET /api/quote-requests/{id}/ (read own)
    Buyer: POST /api/quote-requests/{id}/submit/ (status -> SUBMITTED)
    Seller: POST /api/quote-requests/{id}/price/ (seller calculates, status -> PRICED)
    Seller: POST /api/quote-requests/{id}/send/ (status -> SENT)
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        from django.db.models import Q
        user = self.request.user
        # Both buyer (created_by) and seller (shop owner) can see quote requests
        return QuoteRequest.objects.filter(
            Q(created_by=user) | Q(shop__owner=user)
        )

    def get_serializer_class(self):
        if self.action == "create":
            return QuoteRequestCreateSerializer
        if self.action in ("update", "partial_update"):
            return QuoteRequestPatchSerializer
        return QuoteRequestReadSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            QuoteRequestReadSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        serializer.save()

    def update(self, request, *args, **kwargs):
        qr = self.get_object()
        if qr.created_by_id != request.user.id:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        if qr.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Only draft quote requests can be updated."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        """Buyer: submit draft (status -> SUBMITTED)."""
        qr = self.get_object()
        if qr.created_by_id != request.user.id:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        if qr.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Only draft quote requests can be submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qr.status = QuoteStatus.SUBMITTED
        qr.save(update_fields=["status", "updated_at"])
        return Response(QuoteRequestReadSerializer(qr).data)

    @action(detail=True, methods=["post"], url_path="price")
    def price(self, request, pk=None):
        """Seller: calculate & lock prices (status -> PRICED)."""
        qr = self.get_object()
        if qr.shop.owner_id != request.user.id:
            return Response({"detail": "Not your shop."}, status=status.HTTP_403_FORBIDDEN)
        if qr.status != QuoteStatus.SUBMITTED:
            return Response(
                {"detail": "Only submitted quote requests can be priced."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        recalculate_and_lock_quote_request(qr)
        return Response(QuoteRequestReadSerializer(qr).data)

    @action(detail=True, methods=["post"], url_path="send")
    def send(self, request, pk=None):
        """Seller: mark as sent (status -> SENT)."""
        qr = self.get_object()
        if qr.shop.owner_id != request.user.id:
            return Response({"detail": "Not your shop."}, status=status.HTTP_403_FORBIDDEN)
        if qr.status != QuoteStatus.PRICED:
            return Response(
                {"detail": "Only priced quote requests can be sent."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qr.status = QuoteStatus.SENT
        qr.save(update_fields=["status", "updated_at"])
        return Response(QuoteRequestReadSerializer(qr).data)


class QuoteDraftViewSet(viewsets.ViewSet):
    """
    GET /api/quote-drafts/active/?shop=<slug> — get or create one active draft per (user, shop).
    GET /api/quote-drafts/{id}/ — retrieve draft (owner only).
    POST /api/quote-drafts/{id}/preview-price/ — preview price for typing reveal.
    POST /api/quote-drafts/{id}/request-quote/ — submit draft (status -> SUBMITTED).
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        """GET /api/quote-drafts/ — list user's draft quotes."""
        drafts = QuoteRequest.objects.filter(
            created_by=request.user, status=QuoteStatus.DRAFT
        ).select_related("shop").order_by("-created_at")
        return Response(QuoteRequestReadSerializer(drafts, many=True).data)

    @action(detail=False, methods=["get"], url_path="active")
    def active(self, request):
        shop_slug = request.query_params.get("shop")
        if not shop_slug:
            return Response(
                {"detail": "Query parameter 'shop' (shop slug) is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        shop = get_object_or_404(Shop, slug=shop_slug, is_active=True)
        user = request.user
        # Get or create ONE active draft per (user, shop). Mark any legacy duplicates REJECTED.
        drafts = list(
            QuoteRequest.objects.filter(
                shop=shop, created_by=user, status=QuoteStatus.DRAFT
            ).order_by("-created_at")
        )
        if not drafts:
            draft = QuoteRequest.objects.create(
                shop=shop, created_by=user, status=QuoteStatus.DRAFT
            )
        else:
            draft = drafts[0]
            # Mark older drafts REJECTED so only one active draft exists
            for older in drafts[1:]:
                older.status = QuoteStatus.REJECTED
                older.save(update_fields=["status", "updated_at"])
        return Response(QuoteRequestReadSerializer(draft).data)

    def retrieve(self, request, pk=None):
        """GET /api/quote-drafts/{id}/ — buyer retrieves own draft."""
        draft = get_object_or_404(QuoteRequest.objects.select_related("shop"), pk=pk)
        if draft.created_by_id != request.user.id:
            return Response({"detail": "Not your quote."}, status=status.HTTP_403_FORBIDDEN)
        if draft.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Not a draft."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(QuoteRequestReadSerializer(draft).data)

    @action(detail=True, methods=["post"], url_path="preview-price")
    def preview_price(self, request, pk=None):
        """POST /api/quote-drafts/{id}/preview-price/ — preview price for typing reveal."""
        draft = get_object_or_404(QuoteRequest.objects.select_related("shop"), pk=pk)
        if draft.created_by_id != request.user.id:
            return Response({"detail": "Not your quote."}, status=status.HTTP_403_FORBIDDEN)
        if draft.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Only draft quotes can be previewed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = build_preview_price_response(draft)
        return Response(data)

    @action(detail=True, methods=["post"], url_path="request-quote")
    def request_quote(self, request, pk=None):
        """POST /api/quote-drafts/{id}/request-quote/ — submit draft (status -> SUBMITTED)."""
        draft = get_object_or_404(QuoteRequest.objects.select_related("shop"), pk=pk)
        if draft.created_by_id != request.user.id:
            return Response({"detail": "Not your quote."}, status=status.HTTP_403_FORBIDDEN)
        if draft.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Only draft quotes can be submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        draft.status = QuoteStatus.SUBMITTED
        draft.save(update_fields=["status", "updated_at"])
        return Response(QuoteRequestReadSerializer(draft).data)


# ---------------------------------------------------------------------------
# Quote calculator (staff-only, live preview)
# ---------------------------------------------------------------------------


class QuoteCalculatorView(APIView):
    """
    POST /api/calculator/quote-item/
    Returns pricing JSON without saving. Staff-only.
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request):
        serializer = QuoteCalculatorInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        from services.quote_calculator import calculate_quote_item

        result = calculate_quote_item(
            product_id=data["product_id"],
            quantity=data["quantity"],
            width_mm=data.get("width_mm"),
            height_mm=data.get("height_mm"),
            paper_id=data.get("paper_id"),
            grammage=data.get("grammage"),
            paper_type=data.get("paper_type") or None,
            sheet_size=data.get("sheet_size") or None,
            finishing_ids=data.get("finishing_ids") or [],
            machine_id=data.get("machine_id"),
            sides=data.get("sides", "SIMPLEX"),
            color_mode=data.get("color_mode", "COLOR"),
            overhead_percent=data.get("overhead_percent"),
            margin_percent=data.get("margin_percent"),
        )
        return Response(result.to_dict())


# ---------------------------------------------------------------------------
# Public quote share (GET /api/share/<token>/)
# ---------------------------------------------------------------------------


class QuoteSharePublicView(APIView):
    """
    GET /api/share/<token>/ — public quote summary (no private shop settings).
    No auth required. Token must be valid and not expired.
    """

    permission_classes = [AllowAny]

    def get(self, request, token):
        from django.utils import timezone

        link = get_object_or_404(QuoteShareLink, token=token)
        if link.expires_at and timezone.now() > link.expires_at:
            return Response(
                {"detail": "This share link has expired."},
                status=status.HTTP_410_GONE,
            )
        quote = link.quote
        quote = QuoteRequest.objects.filter(pk=quote.pk).select_related(
            "shop"
        ).prefetch_related(
            "items__product",
            "items__paper",
            "items__material",
            "items__finishings__finishing_rate",
        ).first()
        serializer = QuoteSharePublicSerializer(quote)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Staff quoting API (/api/quotes/) — staff-only, full control
# ---------------------------------------------------------------------------


class QuoteViewSet(viewsets.ModelViewSet):
    """
    Staff-only quoting API.
    POST /api/quotes/ — create quote draft
    GET /api/quotes/ — list quotes (filterable: status, date range, created_by, product)
    GET /api/quotes/{id}/ — detail with items + pricing breakdown
    POST /api/quotes/{id}/send/ — mark SENT, lock pricing, store whatsapp_message + sent_at
    """

    permission_classes = [IsAuthenticated, IsStaffUser]
    filterset_class = QuoteFilterSet
    filter_backends = [DjangoFilterBackend]

    def get_queryset(self):
        return QuoteRequest.objects.select_related("shop", "created_by").prefetch_related(
            "items__product",
            "items__paper",
            "items__material",
            "items__machine",
            "items__finishings__finishing_rate",
        ).order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "create":
            return QuoteCreateSerializer
        return QuoteDetailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            QuoteDetailSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        serializer.save()

    @action(detail=True, methods=["post"], url_path="whatsapp-preview")
    def whatsapp_preview(self, request, pk=None):
        """POST /api/quotes/{id}/whatsapp-preview/ — returns { message }."""
        quote = self.get_object()
        shop = quote.shop
        message = format_quote_for_whatsapp(
            quote,
            company_name=shop.name or "",
            company_phone=shop.phone_number or "",
            turnaround="2-3 business days",
            payment_terms=None,
        )
        return Response({"message": message})

    @action(detail=True, methods=["post"], url_path="share")
    def share(self, request, pk=None):
        """
        POST /api/quotes/{id}/share/ — create share link, return { share_url, whatsapp_text }.
        Optional body: { expires_at: "2025-12-31T23:59:59Z" }.
        """
        import secrets
        from django.conf import settings

        quote = self.get_object()
        expires_at = None
        if isinstance(request.data, dict) and request.data.get("expires_at"):
            try:
                from django.utils.dateparse import parse_datetime
                expires_at = parse_datetime(request.data["expires_at"])
            except (TypeError, ValueError):
                pass

        token = secrets.token_urlsafe(32)
        frontend_url = getattr(settings, "FRONTEND_URL", "https://printy.ke").rstrip("/")
        share_url = f"{frontend_url}/share/{token}"

        QuoteShareLink.objects.create(
            quote=quote,
            token=token,
            expires_at=expires_at,
            created_by=request.user,
        )

        shop = quote.shop
        whatsapp_text = format_quote_for_whatsapp(
            quote,
            company_name=shop.name or "",
            company_phone=shop.phone_number or "",
            turnaround="2-3 business days",
            payment_terms=None,
            share_url=share_url,
        )

        return Response({
            "share_url": share_url,
            "whatsapp_text": whatsapp_text,
        })

    @action(detail=True, methods=["post"], url_path="send")
    def send(self, request, pk=None):
        """Mark quote as SENT, lock pricing on all items, generate and store whatsapp_message + sent_at."""
        from django.db import transaction
        from django.utils import timezone

        from quotes.pricing_service import compute_and_store_pricing

        quote = self.get_object()

        if quote.status not in (QuoteStatus.DRAFT, QuoteStatus.PRICED):
            return Response(
                {"detail": "Only DRAFT or PRICED quotes can be sent."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        shop = quote.shop
        with transaction.atomic():
            for item in quote.items.all():
                compute_and_store_pricing(item)
                item.pricing_locked_at = now
                item.save(update_fields=["pricing_locked_at", "updated_at"])
            total = sum(
                (item.line_total or 0) for item in quote.items.all()
            )
            quote.total = total
            quote.status = QuoteStatus.SENT
            quote.whatsapp_message = format_quote_for_whatsapp(
                quote,
                company_name=shop.name or "",
                company_phone=shop.phone_number or "",
                turnaround="2-3 business days",
                payment_terms=None,
            )
            quote.sent_at = now
            quote.pricing_locked_at = now
            quote.save(update_fields=["total", "status", "whatsapp_message", "sent_at", "pricing_locked_at", "updated_at"])

        return Response(QuoteDetailSerializer(quote).data)


class QuoteItemViewSet(viewsets.ModelViewSet):
    """
    Staff: nested items under /api/quotes/{id}/items/
    POST — add item (computes and stores pricing snapshot)
    PATCH/PUT — update item (recomputes pricing)
    """

    permission_classes = [IsAuthenticated, IsStaffUser]

    def get_queryset(self):
        quote_pk = self.kwargs.get("quote_pk")
        return QuoteItem.objects.filter(quote_request_id=quote_pk).select_related(
            "product", "paper", "material", "machine"
        ).prefetch_related("finishings__finishing_rate")

    def get_quote(self):
        return get_object_or_404(QuoteRequest, pk=self.kwargs["quote_pk"])

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return QuoteItemAddSerializer
        return QuoteItemWithBreakdownSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["quote_request"] = self.get_quote()
        if self.action in ("update", "partial_update") and self.kwargs.get("pk"):
            item = QuoteItem.objects.filter(
                quote_request_id=self.kwargs["quote_pk"],
                pk=self.kwargs["pk"],
            ).first()
            if item:
                ctx["quote_item"] = item
        return ctx

    def create(self, request, *args, **kwargs):
        quote = self.get_quote()
        if quote.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Items can only be added to DRAFT quotes."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            QuoteItemWithBreakdownSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        serializer.save()

    def update(self, request, *args, **kwargs):
        quote = self.get_quote()
        if quote.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Items can only be updated in DRAFT quotes."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        quote = self.get_quote()
        if quote.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Items can only be updated in DRAFT quotes."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        quote = self.get_quote()
        if quote.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Items can only be removed from DRAFT quotes."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)


class MeFavoritesViewSet(viewsets.ViewSet):
    """
    GET /api/me/favorites/ — list user's favorite shops.
    POST /api/me/favorites/ — add favorite (body: {"shop": <id>}).
    DELETE /api/me/favorites/{shop_id}/ — remove favorite.
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        favorites = FavoriteShop.objects.filter(user=request.user).select_related("shop")
        serializer = FavoriteShopSerializer(favorites, many=True)
        return Response(serializer.data)

    def create(self, request):
        serializer = FavoriteShopCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        shop = serializer.validated_data["shop"]
        fav, created = FavoriteShop.objects.get_or_create(
            user=request.user, shop=shop, defaults={}
        )
        return Response(
            FavoriteShopSerializer(fav).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def destroy(self, request, shop_id=None):
        fav = FavoriteShop.objects.filter(
            user=request.user, shop_id=shop_id
        ).first()
        if not fav:
            return Response(
                {"detail": "Favorite not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        fav.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ShopRateView(APIView):
    """
    POST /api/shops/{shop_id}/rate/ — rate a shop (buyer).
    Only allowed if user has at least one QuoteRequest for that shop with status in [SENT, ACCEPTED].
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, shop_id):
        shop = get_object_or_404(Shop, pk=shop_id, is_active=True)
        user = request.user
        has_eligible_quote = QuoteRequest.objects.filter(
            shop=shop,
            created_by=user,
            status__in=[QuoteStatus.SENT, QuoteStatus.ACCEPTED],
        ).exists()
        if not has_eligible_quote:
            return Response(
                {
                    "detail": "You can only rate a shop after receiving a quote (SENT or ACCEPTED)."
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ShopRatingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rating, _ = ShopRating.objects.update_or_create(
            user=user,
            shop=shop,
            defaults={
                "stars": serializer.validated_data["stars"],
                "comment": serializer.validated_data.get("comment", ""),
            },
        )
        return Response(
            {"stars": rating.stars, "comment": rating.comment, "created_at": rating.created_at},
            status=status.HTTP_200_OK,
        )


class QuoteRequestItemViewSet(viewsets.ModelViewSet):
    """
    POST /api/quote-requests/{id}/items/ — add item (DRAFT only)
    GET /api/quote-requests/{id}/items/ — list items
    PATCH /api/quote-requests/{id}/items/{item_id}/ — edit (DRAFT only)
    DELETE /api/quote-requests/{id}/items/{item_id}/ — remove (DRAFT only)
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        quote_request_pk = self.kwargs.get("quote_request_pk")
        return QuoteItem.objects.filter(quote_request_id=quote_request_pk)

    def get_quote_request(self):
        return get_object_or_404(QuoteRequest, pk=self.kwargs["quote_request_pk"])

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return QuoteItemWriteSerializer
        return QuoteItemReadSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["quote_request"] = self.get_quote_request()
        if self.action in ("update", "partial_update") and self.get_object():
            ctx["quote_item"] = self.get_object()
        return ctx

    def check_buyer_and_draft(self):
        qr = self.get_quote_request()
        if qr.created_by_id != self.request.user.id:
            return False, Response({"detail": "Not your quote request."}, status.HTTP_403_FORBIDDEN)
        if qr.status != QuoteStatus.DRAFT:
            return False, Response(
                {"detail": "Items can only be modified when quote is in DRAFT."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return True, None

    def create(self, request, *args, **kwargs):
        ok, err = self.check_buyer_and_draft()
        if not ok:
            return err
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            QuoteItemReadSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        serializer.save()

    def update(self, request, *args, **kwargs):
        ok, err = self.check_buyer_and_draft()
        if not ok:
            return err
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        ok, err = self.check_buyer_and_draft()
        if not ok:
            return err
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        ok, err = self.check_buyer_and_draft()
        if not ok:
            return err
        return super().destroy(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        qr = self.get_quote_request()
        # Owner (buyer) or shop owner (seller) can view
        if qr.created_by_id != request.user.id and qr.shop.owner_id != request.user.id:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        qr = self.get_quote_request()
        if qr.created_by_id != request.user.id and qr.shop.owner_id != request.user.id:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        return super().retrieve(request, *args, **kwargs)


class QuoteDraftItemViewSet(viewsets.ModelViewSet):
    """
    POST /api/quote-drafts/{id}/items/ — add item (DRAFT only, owner only)
    GET /api/quote-drafts/{id}/items/ — list items
    PATCH /api/quote-drafts/{id}/items/{item_id}/ — edit (DRAFT only)
    DELETE /api/quote-drafts/{id}/items/{item_id}/ — remove (DRAFT only)
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        quote_pk = self.kwargs.get("quote_draft_pk")
        return QuoteItem.objects.filter(quote_request_id=quote_pk)

    def get_quote_request(self):
        return get_object_or_404(QuoteRequest, pk=self.kwargs["quote_draft_pk"])

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return QuoteItemWriteSerializer
        return QuoteItemReadSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["quote_request"] = self.get_quote_request()
        if self.action in ("update", "partial_update") and self.get_object():
            ctx["quote_item"] = self.get_object()
        return ctx

    def check_buyer_and_draft(self):
        qr = self.get_quote_request()
        if qr.created_by_id != self.request.user.id:
            return False, Response({"detail": "Not your quote request."}, status.HTTP_403_FORBIDDEN)
        if qr.status != QuoteStatus.DRAFT:
            return False, Response(
                {"detail": "Items can only be modified when quote is in DRAFT."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return True, None

    def create(self, request, *args, **kwargs):
        ok, err = self.check_buyer_and_draft()
        if not ok:
            return err
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            QuoteItemReadSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_create(self, serializer):
        serializer.save()

    def update(self, request, *args, **kwargs):
        ok, err = self.check_buyer_and_draft()
        if not ok:
            return err
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        ok, err = self.check_buyer_and_draft()
        if not ok:
            return err
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        ok, err = self.check_buyer_and_draft()
        if not ok:
            return err
        return super().destroy(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        qr = self.get_quote_request()
        if qr.created_by_id != request.user.id:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        qr = self.get_quote_request()
        if qr.created_by_id != request.user.id:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        return super().retrieve(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Finishing categories (global, read-only for authenticated users)
# ---------------------------------------------------------------------------


class FinishingCategoryViewSet(viewsets.ModelViewSet):
    """CRUD /api/finishing-categories/ — manage finishing categories."""

    serializer_class = FinishingCategorySerializer
    permission_classes = [IsAuthenticated]
    queryset = FinishingCategory.objects.all()
    lookup_field = "slug"


# ---------------------------------------------------------------------------
# Seller — shop-scoped resources
# ---------------------------------------------------------------------------


class ShopViewSet(viewsets.ModelViewSet):
    """CRUD /api/shops/ — manage own shop. Lookup by slug."""

    serializer_class = ShopSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        return Shop.objects.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class ShopScopedMixin:
    """Mixin to ensure shop belongs to current user. Uses shop_slug or shop_id from URL."""

    def _get_shop(self):
        from rest_framework.exceptions import PermissionDenied, NotFound

        shop = None
        shop_id = self.kwargs.get("shop_id")
        shop_slug = self.kwargs.get("shop_slug")

        if shop_id is not None:
            shop = Shop.objects.filter(pk=shop_id).first()
        elif shop_slug:
            shop = Shop.objects.filter(slug=shop_slug).first()

        if not shop or shop.owner_id != self.request.user.id:
            if not shop:
                raise NotFound("Shop not found.")
            raise PermissionDenied("Not your shop.")
        return shop

    def list(self, request, *args, **kwargs):
        self._get_shop()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        self._get_shop()
        return super().retrieve(request, *args, **kwargs)


class ShopMachineViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """CRUD /api/shops/{shop_id}/machines/"""

    serializer_class = MachineSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        shop = self._get_shop()
        return Machine.objects.filter(shop=shop)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["shop"] = self._get_shop()
        return ctx

    def perform_create(self, serializer):
        shop = self._get_shop()
        serializer.save(shop=shop)

    def update(self, request, *args, **kwargs):
        self._get_shop()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._get_shop()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._get_shop()
        return super().destroy(request, *args, **kwargs)


class ShopPaperViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """CRUD /api/shops/{shop_slug}/papers/"""

    serializer_class = PaperSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        shop = self._get_shop()
        return Paper.objects.filter(shop=shop)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["shop"] = self._get_shop()
        return ctx

    def perform_create(self, serializer):
        serializer.save(shop=self._get_shop())

    def update(self, request, *args, **kwargs):
        self._get_shop()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._get_shop()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._get_shop()
        return super().destroy(request, *args, **kwargs)


class MachinePrintingRateViewSet(viewsets.ModelViewSet):
    """CRUD /api/machines/{machine_id}/printing-rates/"""

    serializer_class = PrintingRateSerializer
    permission_classes = [IsAuthenticated]

    def _get_machine(self):
        machine = Machine.objects.filter(pk=self.kwargs["machine_id"]).select_related("shop").first()
        if not machine or machine.shop.owner_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied, NotFound
            if not machine:
                raise NotFound("Machine not found.")
            raise PermissionDenied("Not your shop.")
        return machine

    def get_queryset(self):
        return PrintingRate.objects.filter(machine_id=self.kwargs["machine_id"])

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["machine"] = Machine.objects.get(pk=self.kwargs["machine_id"])
        return ctx

    def list(self, request, *args, **kwargs):
        self._get_machine()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        self._get_machine()
        return super().retrieve(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(machine=self._get_machine())

    def update(self, request, *args, **kwargs):
        self._get_machine()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._get_machine()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._get_machine()
        return super().destroy(request, *args, **kwargs)


class ShopFinishingRateViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """CRUD /api/shops/{shop_slug}/finishing-rates/?category=<slug>"""

    serializer_class = FinishingRateSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        shop = self._get_shop()
        qs = FinishingRate.objects.filter(shop=shop).select_related("category")
        category_slug = self.request.query_params.get("category")
        if category_slug:
            qs = qs.filter(category__slug=category_slug)
        return qs

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["shop"] = self._get_shop()
        return ctx

    def perform_create(self, serializer):
        serializer.save(shop=self._get_shop())

    def update(self, request, *args, **kwargs):
        self._get_shop()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._get_shop()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._get_shop()
        return super().destroy(request, *args, **kwargs)


class ShopMaterialViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """CRUD /api/shops/{shop_slug}/materials/"""

    serializer_class = MaterialSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        shop = self._get_shop()
        return Material.objects.filter(shop=shop)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["shop"] = self._get_shop()
        return ctx

    def perform_create(self, serializer):
        serializer.save(shop=self._get_shop())

    def update(self, request, *args, **kwargs):
        self._get_shop()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._get_shop()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._get_shop()
        return super().destroy(request, *args, **kwargs)


class ShopProductViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """CRUD /api/shops/{shop_slug}/products/"""

    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return ProductWriteSerializer
        if self.action == "list":
            return ProductListSerializer
        return ProductSerializer

    def get_queryset(self):
        shop = self._get_shop()
        return Product.objects.filter(shop=shop).prefetch_related(
            "finishing_options__finishing_rate"
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["shop"] = self._get_shop()
        return ctx

    def perform_create(self, serializer):
        serializer.save(shop=self._get_shop())

    def update(self, request, *args, **kwargs):
        self._get_shop()
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._get_shop()
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._get_shop()
        return super().destroy(request, *args, **kwargs)


class ShopProductImageViewSet(viewsets.ModelViewSet):
    """CRUD /api/shops/{shop_slug}/products/{product_pk}/images/"""

    permission_classes = [IsAuthenticated]

    def _get_shop(self):
        from rest_framework.exceptions import PermissionDenied, NotFound
        shop_slug = self.kwargs.get("shop_slug")
        shop = Shop.objects.filter(slug=shop_slug).first()
        if not shop or shop.owner_id != self.request.user.id:
            if not shop:
                raise NotFound("Shop not found.")
            raise PermissionDenied("Not your shop.")
        return shop

    def _get_product(self):
        from rest_framework.exceptions import NotFound
        shop = self._get_shop()
        product = Product.objects.filter(pk=self.kwargs["product_pk"], shop=shop).first()
        if not product:
            raise NotFound("Product not found.")
        return product

    def get_queryset(self):
        product = self._get_product()
        return ProductImage.objects.filter(product=product)

    def get_serializer_class(self):
        if self.action in ("create",):
            return ProductImageUploadSerializer
        return ProductImageSerializer

    def perform_create(self, serializer):
        product = self._get_product()
        serializer.save(product=product)


# ---------------------------------------------------------------------------
# Tweak-and-Add: Gallery → Tweak → Quote
# ---------------------------------------------------------------------------


class TweakAndAddView(APIView):
    """
    POST /api/quote-drafts/{draft_id}/tweak-and-add/

    Creates a tweaked product instance (QuoteItem) with chosen options,
    computes server-side pricing, stores the breakdown snapshot,
    and adds it to the quote draft.

    The original product template is never modified.

    Example request:
    {
        "product": 5,
        "quantity": 200,
        "paper": 9,
        "sides": "DUPLEX",
        "color_mode": "COLOR",
        "machine": 1,
        "finishings": [{"finishing_rate": 1}, {"finishing_rate": 3}],
        "special_instructions": "Rush order"
    }
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, draft_id):
        draft = get_object_or_404(
            QuoteRequest.objects.select_related("shop"),
            pk=draft_id,
        )
        if draft.created_by_id != request.user.id:
            return Response({"detail": "Not your quote."}, status=status.HTTP_403_FORBIDDEN)
        if draft.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Items can only be added to DRAFT quotes."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = TweakAndAddSerializer(
            data=request.data,
            context={"request": request, "quote_request": draft, "shop": draft.shop},
        )
        serializer.is_valid(raise_exception=True)
        item = serializer.save()

        read_serializer = TweakedItemReadSerializer(
            item,
            context={"request": request},
        )
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)


class TweakedItemUpdateView(APIView):
    """
    PATCH /api/tweaked-items/{item_id}/

    Update a tweaked item's options and recompute pricing.
    Creates the update in-place (simpler approach) since the item
    is already a per-user customized instance, not a shared template.
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request, item_id):
        item = get_object_or_404(
            QuoteItem.objects.select_related("quote_request__shop", "product", "paper", "material", "machine"),
            pk=item_id,
        )
        if item.quote_request.created_by_id != request.user.id:
            return Response({"detail": "Not your quote item."}, status=status.HTTP_403_FORBIDDEN)
        if item.quote_request.status != QuoteStatus.DRAFT:
            return Response({"detail": "Cannot modify locked items."}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction
        from quotes.pricing_service import compute_and_store_pricing

        updatable_fields = ["quantity", "sides", "color_mode", "special_instructions", "has_artwork"]
        fk_fields = {"paper": Paper, "material": Material, "machine": Machine}

        with transaction.atomic():
            for field in updatable_fields:
                if field in request.data:
                    setattr(item, field, request.data[field])
            for field, model in fk_fields.items():
                if field in request.data:
                    val = request.data[field]
                    setattr(item, f"{field}_id", val)
            if "chosen_width_mm" in request.data:
                item.chosen_width_mm = request.data["chosen_width_mm"]
            if "chosen_height_mm" in request.data:
                item.chosen_height_mm = request.data["chosen_height_mm"]

            item.save()

            if "finishings" in request.data:
                item.finishings.all().delete()
                for fin in request.data["finishings"]:
                    fr_id = fin.get("finishing_rate") if isinstance(fin, dict) else fin
                    QuoteItemFinishing.objects.create(
                        quote_item=item,
                        finishing_rate_id=fr_id,
                        price_override=fin.get("price_override") if isinstance(fin, dict) else None,
                    )

            compute_and_store_pricing(item)

        item.refresh_from_db()
        serializer = TweakedItemReadSerializer(item, context={"request": request})
        return Response(serializer.data)


class GalleryProductDetailView(APIView):
    """
    GET /api/public/products/{pk}/options/

    Returns a product template with all available tweaking options
    (papers, machines, materials, finishings for the product's shop).
    No login required.
    """

    permission_classes = [AllowAny]

    def get(self, request, pk):
        product = get_object_or_404(
            Product.objects.select_related("shop")
                .prefetch_related("finishing_options__finishing_rate", "images"),
            pk=pk,
            is_active=True,
        )
        serializer = GalleryProductOptionsSerializer(product)
        return Response(serializer.data)
