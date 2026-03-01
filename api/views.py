"""
DRF viewsets and API views.
"""
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView

from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate, Material, PrintingRate
from django.db.models import Avg, Count
from quotes.choices import QuoteStatus
from quotes.models import QuoteItem, QuoteRequest
from quotes.quote_engine import recalculate_and_lock_quote_request
from quotes.services import build_preview_price_response, calculate_quote_item
from shops.models import FavoriteShop, Shop, ShopRating

from .permissions import IsQuoteRequestBuyer, IsQuoteRequestSeller, IsShopOwner, PublicReadOnly
from .serializers import (
    CatalogProductSerializer,
    CatalogProductWithShopSerializer,
    FavoriteShopCreateSerializer,
    FavoriteShopSerializer,
    FinishingRateSerializer,
    MachineSerializer,
    MaterialSerializer,
    PaperSerializer,
    PrintingRateSerializer,
    ProductSerializer,
    ProfileSerializer,
    PublicShopListSerializer,
    QuoteItemReadSerializer,
    QuoteItemWriteSerializer,
    QuoteRequestCreateSerializer,
    QuoteRequestPatchSerializer,
    QuoteRequestReadSerializer,
    ShopRatingSerializer,
    ShopRatingSummarySerializer,
    ShopSerializer,
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
        """GET /api/public/shops/{slug}/catalog/ — shop + products with finishing options."""
        shop = self.get_object()
        products = Product.objects.filter(shop=shop, is_active=True).prefetch_related(
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
    """GET /api/public/products/ — all products from all active shops (for gallery)."""

    permission_classes = [AllowAny]

    def get(self, request):
        products = (
            Product.objects.filter(shop__is_active=True, is_active=True)
            .select_related("shop")
            .prefetch_related(
                "finishing_options__finishing_rate",
                "images",
            )
        )
        data = CatalogProductWithShopSerializer(products, many=True).data
        return Response({"products": data})


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
    """Mixin to ensure shop belongs to current user. Uses shop_slug from URL."""

    def _get_shop(self):
        slug = self.kwargs.get("shop_slug")
        shop = Shop.objects.filter(slug=slug).first() if slug else None
        if not shop or shop.owner_id != self.request.user.id:
            from rest_framework.exceptions import PermissionDenied, NotFound
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
    """CRUD /api/shops/{shop_slug}/finishing-rates/"""

    serializer_class = FinishingRateSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        shop = self._get_shop()
        return FinishingRate.objects.filter(shop=shop)

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

    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated]

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
