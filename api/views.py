"""
DRF viewsets and API views.
"""
import math
import os
import uuid

from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.core.files.storage import default_storage

from common.geo import haversine_km
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.parsers import FormParser, MultiPartParser
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.serializers import ModelSerializer

from accounts.models import UserProfile, UserSocialLink
from accounts.serializers import UserSerializer, UserSocialLinkSerializer
from accounts.services.roles import promote_to_shop_owner
from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingCategory, FinishingRate, Material, PrintingRate, VolumeDiscount
from django.db.models import Avg, Count
from quotes.choices import QuoteStatus
from quotes.models import QuoteDraftFile, QuoteItem, QuoteItemFinishing, QuoteRequest, QuoteShareLink, ShopQuote
from quotes.services_match_shops import find_shops_for_spec
from quotes.services import build_preview_price_response, calculate_quote_item
from quotes.draft_files import (
    build_dashboard_quote_file_payload,
    build_quote_draft_file_payload,
    ensure_quote_draft_file,
    ensure_quote_draft_file_for_request,
    sync_quote_request_from_file,
)
from quotes.draft_pdf import render_dashboard_quote_file_pdf, render_quote_draft_file_pdf, render_quote_draft_pdf
from quotes.whatsapp_formatter import format_quote_for_whatsapp
from quotes.summary_service import get_quote_draft_file_summary_payload
from shops.models import FavoriteShop, OpeningHours, Shop, ShopRating
from shops.services import can_manage_shop

from .filters import QuoteFilterSet
from .permissions import (
    IsQuoteRequestBuyer,
    IsQuoteRequestItemBuyer,
    IsQuoteRequestSeller,
    IsShopOwner,
    IsStaffUser,
    PublicReadOnly,
)
from .serializers import QuoteCalculatorInputSerializer
from catalog.models import ProductImage
from .serializers import (
    MatchShopsInputSerializer,
    MatchShopsResponseSerializer,
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
    OpeningHoursBulkSerializer,
    OpeningHoursSerializer,
    ShopSerializer,
    TweakAndAddSerializer,
    TweakedItemReadSerializer,
    VolumeDiscountSerializer,
)
from .quote_serializers import (
    QuoteRequestCustomerDetailSerializer,
    QuoteRequestCustomerListSerializer,
    QuoteRequestShopDetailSerializer,
    QuoteRequestShopListSerializer,
    ShopQuoteDetailSerializer,
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
        return Shop.objects.filter(is_active=True, is_public=True).select_related(
            "owner__profile"
        ).exclude(
            slug__isnull=True
        ).exclude(slug="").prefetch_related("opening_hours", "owner__profile__social_links")

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
        ).select_related("category").prefetch_related(
            "finishing_options__finishing_rate",
            "images",
        )
        products_data = CatalogProductSerializer(
            products, many=True, context={"request": request, "shop": shop}
        ).data
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


class ShopRateCardView(APIView):
    """
    GET /api/shops/{slug}/rate-card/ — public rate card for a shop.
    Returns printing rates, paper prices, and finishing services for buyer display.
    No auth required.
    """

    permission_classes = [AllowAny]

    def get(self, request, shop_slug):
        shop = get_object_or_404(Shop, slug=shop_slug, is_active=True)

        # Printing: from PrintingRate via Machine (shop's machines). Default rates first.
        printing = []
        for rate in (
            PrintingRate.objects.filter(machine__shop=shop, is_active=True)
            .select_related("machine")
            .order_by("-is_default", "sheet_size", "color_mode")[:50]
        ):
            duplex_breakdown = rate.get_duplex_price_breakdown()
            printing.append({
                "sheet_size": rate.sheet_size,
                "color_mode": rate.get_color_mode_display() or rate.color_mode,
                "price_per_side": str(rate.single_price),
                "price_double_sided": str(duplex_breakdown["total_per_sheet"]),
                "duplex_surcharge": str(rate.duplex_surcharge),
                "duplex_surcharge_enabled": rate.duplex_surcharge_enabled,
                "duplex_surcharge_min_gsm": rate.duplex_surcharge_min_gsm,
                "duplex_override_used": duplex_breakdown["duplex_override_used"],
                "is_default": rate.is_default,
            })
        # Dedupe by sheet_size+color_mode (keep first)
        seen = set()
        printing_deduped = []
        for p in printing:
            key = (p["sheet_size"], p["color_mode"])
            if key not in seen:
                seen.add(key)
                printing_deduped.append(p)

        # Paper: computed per-sheet price = paper + printing (per sheet size)
        # Buyers see single/double; owners see breakdown (paper + printing)
        from decimal import Decimal
        from pricing.choices import ColorMode

        is_owner = request.user.is_authenticated and getattr(shop, "owner_id", None) == request.user.id

        # Build printing lookup by sheet_size (prefer default rate, then COLOR)
        printing_by_sheet = {}
        for rate in PrintingRate.objects.filter(
            machine__shop=shop, is_active=True
        ).select_related("machine").order_by("-is_default", "sheet_size", "color_mode"):
            key = rate.sheet_size
            if key not in printing_by_sheet:
                printing_by_sheet[key] = rate

        paper = []
        for p in Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0).order_by("sheet_size", "gsm", "paper_type")[:50]:
            pr = printing_by_sheet.get(p.sheet_size)
            paper_sell = Decimal(str(p.selling_price))
            if pr:
                single = paper_sell + pr.single_price
                double = paper_sell + pr.get_price_for_sides("DUPLEX", paper=p)
            else:
                single = paper_sell
                double = paper_sell

            row = {
                "gsm": p.gsm,
                "paper_type": p.get_paper_type_display() or p.paper_type,
                "sheet_size": p.sheet_size,
                "single_price": str(single),
                "double_price": str(double),
            }
            if is_owner and pr:
                row["price_per_sheet"] = str(p.selling_price)
                row["printing_single"] = str(pr.single_price)
                row["printing_double"] = str(pr.get_price_for_sides("DUPLEX", paper=p))
            paper.append(row)

        # Finishing: from FinishingRate
        finishing = []
        for f in FinishingRate.objects.filter(shop=shop, is_active=True).select_related("category").order_by("name")[:50]:
            finishing.append({
                "id": f.id,
                "name": f.name,
                "category": f.category.name if f.category else "",
                "price": str(f.price),
                "charge_by": f.display_unit_label or f.get_charge_unit_display() or f.charge_unit,
                "is_default": False,
            })

        return Response({
            "printing": printing_deduped,
            "paper": paper,
            "finishing": finishing,
            "is_owner": is_owner,
        })


class ShopRateCardForCalculatorView(APIView):
    """
    GET /api/shops/{slug}/rate-card-for-calculator/ — demo-compatible rate card.
    Returns templates, papers, printing_rates, finishing_rates, materials for landing calculator.
    No auth required. Use when demo/rate-card is unavailable (e.g. demo app not deployed).
    """

    permission_classes = [AllowAny]

    def get(self, request, shop_slug):
        shop = get_object_or_404(Shop, slug=shop_slug, is_active=True)

        # Templates from shop's PUBLISHED products
        products = Product.objects.filter(
            shop=shop,
            is_active=True,
            status="PUBLISHED",
        ).prefetch_related("finishing_options__finishing_rate", "impositions").select_related("category")[:50]

        templates = []
        for p in products:
            sheet_size = p.default_sheet_size or "SRA3"
            imp = p.impositions.filter(sheet_size=sheet_size, is_default=True).first()
            if not imp:
                imp = p.impositions.filter(sheet_size=sheet_size).first()
            copies = imp.copies_per_sheet if imp else max(1, p.get_copies_per_sheet(sheet_size))

            finishing_opts = [
                {
                    "finishing_rate": opt.finishing_rate_id,
                    "is_default": opt.is_default,
                    "price_adjustment": str(opt.price_adjustment) if opt.price_adjustment else None,
                }
                for opt in p.finishing_options.select_related("finishing_rate").all()
                if opt.finishing_rate.is_active
            ]

            templates.append({
                "id": p.id,
                "name": p.name,
                "description": p.description or "",
                "category": p.category.slug if p.category else "general",
                "pricing_mode": p.pricing_mode,
                "default_finished_width_mm": p.default_finished_width_mm or 0,
                "default_finished_height_mm": p.default_finished_height_mm or 0,
                "default_sides": p.default_sides,
                "min_quantity": p.min_quantity or 100,
                "default_sheet_size": sheet_size,
                "copies_per_sheet": copies,
                "min_gsm": p.min_gsm,
                "max_gsm": p.max_gsm,
                "finishing_options": finishing_opts,
                "badge": "Popular" if getattr(p, "is_popular", False) else None,
            })

        # Papers
        papers = []
        for p in Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0).order_by("sheet_size", "gsm")[:50]:
            pt = p.paper_type
            if hasattr(pt, "value"):
                pt = pt.value
            papers.append({
                "id": p.id,
                "sheet_size": p.sheet_size,
                "gsm": p.gsm,
                "paper_type": str(pt) if pt else "UNCOATED",
                "selling_price": str(p.selling_price),
                "is_active": p.is_active,
            })

        # Printing rates (dedupe by sheet_size+color_mode)
        printing_rates = []
        seen_pr = set()
        for r in PrintingRate.objects.filter(
            machine__shop=shop, is_active=True
        ).order_by("-is_default", "sheet_size", "color_mode")[:50]:
            key = (r.sheet_size, r.color_mode)
            if key not in seen_pr:
                seen_pr.add(key)
                duplex_breakdown = r.get_duplex_price_breakdown()
                printing_rates.append({
                    "id": r.id,
                    "sheet_size": r.sheet_size,
                    "color_mode": r.color_mode,
                    "single_price": str(r.single_price),
                    "double_price": str(duplex_breakdown["total_per_sheet"]) if duplex_breakdown["total_per_sheet"] is not None else None,
                    "duplex_override_price": str(r.double_price) if r.double_price is not None else None,
                    "duplex_surcharge": str(r.duplex_surcharge),
                    "duplex_surcharge_enabled": r.duplex_surcharge_enabled,
                    "duplex_surcharge_min_gsm": r.duplex_surcharge_min_gsm,
                    "is_active": r.is_active,
                })

        # Finishing rates
        finishing_rates = []
        for f in FinishingRate.objects.filter(shop=shop, is_active=True).order_by("name")[:50]:
            finishing_rates.append({
                "id": f.id,
                "name": f.name,
                "charge_unit": f.charge_unit,
                "price": str(f.price),
                "setup_fee": str(f.setup_fee) if f.setup_fee else None,
                "min_qty": f.min_qty,
                "is_active": f.is_active,
            })

        # Materials (large format)
        materials = []
        for m in Material.objects.filter(shop=shop, is_active=True).order_by("material_type")[:20]:
            materials.append({
                "id": m.id,
                "material_type": m.material_type or "Unknown",
                "unit": m.unit or "SQM",
                "selling_price": str(m.selling_price),
                "is_active": m.is_active,
            })

        return Response({
            "templates": templates,
            "papers": papers,
            "printing_rates": printing_rates,
            "finishing_rates": finishing_rates,
            "materials": materials,
        })


class PublicAllProductsView(APIView):
    """GET /api/public/products/ — only PUBLISHED products from pricing-ready active shops."""

    permission_classes = [AllowAny]

    def get(self, request):
        products = (
            Product.objects.filter(
                shop__is_active=True,
                shop__is_public=True,
                shop__public_match_ready=True,
                is_active=True,
                status="PUBLISHED",
            )
            .select_related("shop", "category")
            .prefetch_related(
                "finishing_options__finishing_rate",
                "images",
            )
        )
        data = CatalogProductWithShopSerializer(products, many=True, context={"request": request}).data
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

    def _get_profile(self, user):
        profile, _ = UserProfile.objects.get_or_create(user=user)
        return profile

    def get(self, request):
        serializer = ProfileSerializer(self._get_profile(request.user))
        return Response(serializer.data)

    def patch(self, request):
        profile = self._get_profile(request.user)
        serializer = ProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProfileSerializer(profile).data)


class ProfileAvatarUploadView(APIView):
    """POST /api/profiles/me/avatar/ - upload the signed-in user's avatar image."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        uploaded_file = request.FILES.get("avatar")

        if not uploaded_file:
            return Response(
                {"avatar": ["Choose an image to upload."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content_type = getattr(uploaded_file, "content_type", "") or ""
        if content_type and not content_type.startswith("image/"):
            return Response(
                {"avatar": ["Avatar uploads must be image files."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        extension = os.path.splitext(uploaded_file.name or "")[1].lower()
        if extension not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            extension = ".jpg"

        saved_path = default_storage.save(
            f"avatars/{request.user.id}/{uuid.uuid4().hex}{extension}",
            uploaded_file,
        )
        profile.avatar = default_storage.url(saved_path)
        profile.save(update_fields=["avatar", "updated_at"])

        return Response(ProfileSerializer(profile).data, status=status.HTTP_200_OK)


class ProfileCreateView(APIView):
    """POST /api/profiles/ — create profile (returns current user as profile)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        serializer = ProfileSerializer(profile)
        return Response(
            serializer.data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ProfileDetailView(generics.RetrieveAPIView):
    """GET /api/profiles/{id}/ â€” fetch your own profile."""

    permission_classes = [IsAuthenticated]
    serializer_class = ProfileSerializer

    def get_queryset(self):
        return UserProfile.objects.filter(user=self.request.user).prefetch_related("social_links")


class ProfileSocialLinkListCreateView(generics.ListCreateAPIView):
    """GET/POST /api/profiles/{id}/social-links/ â€” manage your profile links."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserSocialLinkSerializer

    def get_profile(self):
        return get_object_or_404(
            UserProfile.objects.filter(user=self.request.user),
            pk=self.kwargs["profile_id"],
        )

    def get_queryset(self):
        return self.get_profile().social_links.all()

    def perform_create(self, serializer):
        serializer.save(profile=self.get_profile())


class SocialLinkDetailView(generics.DestroyAPIView):
    """DELETE /api/social-links/{id}/ â€” remove a social link from your profile."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserSocialLinkSerializer

    def get_queryset(self):
        return UserSocialLink.objects.filter(profile__user=self.request.user)


class UserMeCompatView(generics.RetrieveUpdateAPIView):
    """GET/PATCH /api/users/me/ â€” frontend-compatible alias for current user."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


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
        if self.action == "list":
            return QuoteRequestCustomerListSerializer  # Works for both buyer and seller list
        return QuoteRequestReadSerializer  # Detail; get_serializer overrides per-object

    def get_serializer(self, *args, **kwargs):
        if self.action == "retrieve" and args and hasattr(args[0], "created_by_id"):
            obj = args[0]
            if obj.created_by_id == self.request.user.id:
                kwargs.setdefault("context", self.get_serializer_context())
                return QuoteRequestCustomerDetailSerializer(*args, **kwargs)
            kwargs.setdefault("context", self.get_serializer_context())
            return QuoteRequestShopDetailSerializer(*args, **kwargs)
        return super().get_serializer(*args, **kwargs)

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
        """Seller: calculate & lock prices, create ShopQuote, set QuoteRequest -> quoted."""
        from django.db import transaction
        from django.utils import timezone
        from quotes.pricing_service import compute_and_store_pricing

        qr = self.get_object()
        if qr.shop.owner_id != request.user.id:
            return Response({"detail": "Not your shop."}, status=status.HTTP_403_FORBIDDEN)
        if qr.status != QuoteStatus.SUBMITTED:
            return Response(
                {"detail": "Only submitted quote requests can be priced."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        with transaction.atomic():
            for item in qr.items.all():
                compute_and_store_pricing(item)
            total = sum((i.line_total or 0) for i in qr.items.all())
            now = timezone.now()
            rev = qr.shop_quotes.count() + 1
            shop_quote = ShopQuote.objects.create(
                quote_request=qr,
                shop=qr.shop,
                created_by=request.user,
                status=ShopQuote.SENT,
                total=total,
                sent_at=now,
                pricing_locked_at=now,
                revision_number=rev,
            )
            qr.items.update(shop_quote=shop_quote)
            qr.status = QuoteStatus.QUOTED
            qr.save(update_fields=["status", "updated_at"])
        return Response(QuoteRequestReadSerializer(qr).data)

    @action(detail=True, methods=["post"], url_path="send")
    def send(self, request, pk=None):
        """Seller: ensure ShopQuote sent (no-op if already quoted)."""
        qr = self.get_object()
        if qr.shop.owner_id != request.user.id:
            return Response({"detail": "Not your shop."}, status=status.HTTP_403_FORBIDDEN)
        if qr.status not in (QuoteStatus.SUBMITTED, QuoteStatus.QUOTED):
            return Response(
                {"detail": "Only submitted or quoted requests can be sent."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if qr.status == QuoteStatus.SUBMITTED:
            return self.price(request, pk)
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
        file_id = request.query_params.get("file")
        company_name = request.query_params.get("company_name", "")
        if not shop_slug:
            return Response(
                {"detail": "Query parameter 'shop' (shop slug) is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        shop = get_object_or_404(Shop, slug=shop_slug, is_active=True)
        user = request.user
        draft_file = None
        if file_id:
            draft_file = get_object_or_404(QuoteDraftFile, pk=file_id, created_by=user)
        draft_file = ensure_quote_draft_file(user=user, draft_file=draft_file, company_name=company_name)
        # Get or create ONE active draft per (user, file, shop). Mark older duplicates closed.
        drafts = list(
            QuoteRequest.objects.filter(
                shop=shop,
                created_by=user,
                status=QuoteStatus.DRAFT,
            ).order_by("-created_at")
        )
        if not drafts:
            draft = QuoteRequest.objects.create(
                shop=shop,
                created_by=user,
                status=QuoteStatus.DRAFT,
                quote_draft_file=draft_file,
                customer_name=draft_file.contact_name or draft_file.company_name,
                customer_email=draft_file.contact_email,
                customer_phone=draft_file.contact_phone,
            )
        else:
            draft = drafts[0]
            if draft.quote_draft_file_id is None:
                draft.quote_draft_file = draft_file
                draft.save(update_fields=["quote_draft_file", "updated_at"])
            # Mark older drafts closed so only one active draft exists
            for older in drafts[1:]:
                older.status = QuoteStatus.CLOSED
                older.save(update_fields=["status", "updated_at"])
        sync_quote_request_from_file(draft_file, draft)
        return Response(QuoteRequestReadSerializer(draft).data)

    def retrieve(self, request, pk=None):
        """GET /api/quote-drafts/{id}/ — buyer retrieves own draft."""
        draft = get_object_or_404(QuoteRequest.objects.select_related("shop"), pk=pk)
        if draft.created_by_id != request.user.id:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
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
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
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
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        if draft.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Only draft quotes can be submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        draft.status = QuoteStatus.SUBMITTED
        draft.save(update_fields=["status", "updated_at"])
        return Response(QuoteRequestReadSerializer(draft).data)

    @action(detail=True, methods=["get"], url_path="download-pdf")
    def download_pdf(self, request, pk=None):
        draft = get_object_or_404(QuoteRequest.objects.select_related("shop"), pk=pk)
        if draft.created_by_id != request.user.id:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        try:
            pdf_bytes = render_quote_draft_pdf(draft)
        except ImportError:
            return Response({"detail": "PDF export dependency is not installed."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="quote-draft-shop-{draft.shop.slug}-{draft.id}.pdf"'
        return response


class QuoteDraftFileWriteSerializer(ModelSerializer):
    class Meta:
        model = QuoteDraftFile
        fields = ["id", "company_name", "contact_name", "contact_email", "contact_phone", "notes", "status"]
        read_only_fields = ["id"]


class QuoteDraftFileViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        files = QuoteDraftFile.objects.filter(created_by=request.user).order_by("-updated_at", "-created_at")
        scope = request.query_params.get("scope")
        payload_builder = build_dashboard_quote_file_payload if scope == "dashboard" else build_quote_draft_file_payload
        return Response([payload_builder(draft_file) for draft_file in files])

    def retrieve(self, request, pk=None):
        draft_file = get_object_or_404(QuoteDraftFile, pk=pk, created_by=request.user)
        scope = request.query_params.get("scope")
        payload_builder = build_dashboard_quote_file_payload if scope == "dashboard" else build_quote_draft_file_payload
        return Response(payload_builder(draft_file))

    def create(self, request):
        serializer = QuoteDraftFileWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        draft_file = serializer.save(created_by=request.user)
        return Response(build_quote_draft_file_payload(draft_file), status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        draft_file = get_object_or_404(QuoteDraftFile, pk=pk, created_by=request.user)
        serializer = QuoteDraftFileWriteSerializer(draft_file, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        draft_file = serializer.save()
        for draft in draft_file.drafts.filter(status=QuoteStatus.DRAFT):
            sync_quote_request_from_file(draft_file, draft)
        return Response(build_quote_draft_file_payload(draft_file))

    @action(detail=True, methods=["get"], url_path="download-pdf")
    def download_pdf(self, request, pk=None):
        draft_file = get_object_or_404(QuoteDraftFile, pk=pk, created_by=request.user)
        try:
            if request.query_params.get("scope") == "dashboard":
                pdf_bytes = render_dashboard_quote_file_pdf(draft_file)
            else:
                pdf_bytes = render_quote_draft_file_pdf(draft_file)
        except ImportError:
            return Response({"detail": "PDF export dependency is not installed."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="quote-draft-file-{draft_file.id}.pdf"'
        return response

    @action(detail=True, methods=["get"], url_path="whatsapp-preview")
    def whatsapp_preview(self, request, pk=None):
        draft_file = get_object_or_404(QuoteDraftFile, pk=pk, created_by=request.user)
        return Response(get_quote_draft_file_summary_payload(draft_file))


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

        link = get_object_or_404(
            QuoteShareLink.objects.select_related("shop_quote__shop", "shop_quote__quote_request"),
            token=token,
        )
        if link.expires_at and timezone.now() > link.expires_at:
            return Response(
                {"detail": "This share link has expired."},
                status=status.HTTP_410_GONE,
            )
        shop_quote = link.shop_quote
        shop_quote = ShopQuote.objects.filter(pk=shop_quote.pk).select_related(
            "shop", "quote_request"
        ).prefetch_related(
            "items__product",
            "items__paper",
            "items__material",
            "items__finishings__finishing_rate",
        ).first()
        serializer = QuoteSharePublicSerializer(shop_quote)
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
        quote_request = serializer.save()
        draft_file = ensure_quote_draft_file_for_request(user=self.request.user, draft=quote_request)
        sync_quote_request_from_file(draft_file, quote_request)

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
        Uses or creates ShopQuote for the share link. Optional body: { expires_at: "2025-12-31T23:59:59Z" }.
        """
        import secrets
        from django.conf import settings
        from django.utils import timezone

        quote_request = self.get_object()
        shop = quote_request.shop

        # Get or create ShopQuote for sharing
        shop_quote = quote_request.get_latest_shop_quote()
        if not shop_quote:
            from quotes.pricing_service import compute_and_store_pricing
            from django.db import transaction

            with transaction.atomic():
                for item in quote_request.items.all():
                    compute_and_store_pricing(item)
                total = sum((i.line_total or 0) for i in quote_request.items.all())
                now = timezone.now()
                shop_quote = ShopQuote.objects.create(
                    quote_request=quote_request,
                    shop=shop,
                    created_by=request.user,
                    status=ShopQuote.SENT,
                    total=total,
                    sent_at=now,
                    pricing_locked_at=now,
                    revision_number=1,
                )
                quote_request.items.update(shop_quote=shop_quote)
                quote_request.status = QuoteStatus.QUOTED
                quote_request.save(update_fields=["status", "updated_at"])

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
            shop_quote=shop_quote,
            token=token,
            expires_at=expires_at,
            created_by=request.user,
        )

        whatsapp_text = format_quote_for_whatsapp(
            quote_request,
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

        if quote.status not in (QuoteStatus.DRAFT, QuoteStatus.QUOTED):
            return Response(
                {"detail": "Only DRAFT or QUOTED quotes can be sent."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        shop = quote.shop
        with transaction.atomic():
            for item in quote.items.all():
                compute_and_store_pricing(item)
                item.pricing_locked_at = now
                item.save(update_fields=["pricing_locked_at", "updated_at"])
            total = sum((item.line_total or 0) for item in quote.items.all())
            shop_quote = quote.get_latest_shop_quote()
            if not shop_quote:
                rev = quote.shop_quotes.count() + 1
                shop_quote = ShopQuote.objects.create(
                    quote_request=quote,
                    shop=shop,
                    created_by=request.user,
                    status=ShopQuote.SENT,
                    total=total,
                    sent_at=now,
                    pricing_locked_at=now,
                    whatsapp_message=format_quote_for_whatsapp(
                        quote,
                        company_name=shop.name or "",
                        company_phone=shop.phone_number or "",
                        turnaround="2-3 business days",
                        payment_terms=None,
                    ),
                    revision_number=rev,
                )
                quote.items.update(shop_quote=shop_quote)
            else:
                shop_quote.whatsapp_message = format_quote_for_whatsapp(
                    quote,
                    company_name=shop.name or "",
                    company_phone=shop.phone_number or "",
                    turnaround="2-3 business days",
                    payment_terms=None,
                )
                shop_quote.sent_at = now
                shop_quote.save(update_fields=["whatsapp_message", "sent_at", "updated_at"])
            quote.status = QuoteStatus.QUOTED
            quote.save(update_fields=["status", "updated_at"])

        return Response(ShopQuoteDetailSerializer(shop_quote).data)


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
            status=QuoteStatus.QUOTED,
        ).exists()
        if not has_eligible_quote:
            return Response(
                {
                    "detail": "You can only rate a shop after receiving a quote."
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
    GET /api/quote-requests/{id}/items/ — list items (customer only; shops use incoming-requests)
    PATCH /api/quote-requests/{id}/items/{item_id}/ — edit (DRAFT only)
    DELETE /api/quote-requests/{id}/items/{item_id}/ — remove (DRAFT only)
    """

    permission_classes = [IsAuthenticated, IsQuoteRequestItemBuyer]

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
        # Customer-only: shops use /shops/<slug>/incoming-requests/ for their view
        if qr.created_by_id != request.user.id and not request.user.is_staff:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        qr = self.get_quote_request()
        if qr.created_by_id != request.user.id and not request.user.is_staff:
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
        return super().retrieve(request, *args, **kwargs)


class QuoteDraftItemViewSet(viewsets.ModelViewSet):
    """
    POST /api/quote-drafts/{id}/items/ — add item (DRAFT only, owner only)
    GET /api/quote-drafts/{id}/items/ — list items
    PATCH /api/quote-drafts/{id}/items/{item_id}/ — edit (DRAFT only)
    DELETE /api/quote-drafts/{id}/items/{item_id}/ — remove (DRAFT only)
    """

    permission_classes = [IsAuthenticated, IsQuoteRequestItemBuyer]

    def get_queryset(self):
        quote_pk = self.kwargs.get("quote_draft_pk")
        return QuoteItem.objects.filter(quote_request_id=quote_pk).select_related("product")

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
        return (
            Shop.objects.filter(owner=self.request.user)
            | Shop.objects.filter(memberships__user=self.request.user, memberships__is_active=True)
        ).distinct()

    def perform_create(self, serializer):
        promote_to_shop_owner(self.request.user)
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

        if not shop or not can_manage_shop(shop, self.request.user):
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


class ShopOpeningHoursViewSet(ShopScopedMixin, viewsets.ReadOnlyModelViewSet):
    """GET /api/shops/{slug}/hours/ — list opening hours. Owner only."""

    serializer_class = OpeningHoursSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        shop = self._get_shop()
        return OpeningHours.objects.filter(shop=shop).order_by("weekday")


class ShopOpeningHoursBulkView(APIView):
    """POST /api/shops/{slug}/hours/bulk/ — bulk update hours. Owner only."""

    permission_classes = [IsAuthenticated]

    def post(self, request, shop_slug=None, shop_id=None):
        from rest_framework.exceptions import PermissionDenied, NotFound

        shop = None
        if shop_id is not None:
            shop = Shop.objects.filter(pk=shop_id).first()
        elif shop_slug:
            shop = Shop.objects.filter(slug=shop_slug).first()
        if not shop or shop.owner_id != request.user.id:
            if not shop:
                raise NotFound("Shop not found.")
            raise PermissionDenied("Not your shop.")

        ser = OpeningHoursBulkSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        updated = []
        for item in ser.validated_data["hours"]:
            obj, _ = OpeningHours.objects.update_or_create(
                shop=shop,
                weekday=item["weekday"],
                defaults={
                    "from_hour": item.get("from_hour") or "08:00",
                    "to_hour": item.get("to_hour") or "18:00",
                    "is_closed": item.get("is_closed", False),
                },
            )
            updated.append(OpeningHoursSerializer(obj).data)
        return Response(updated)


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

    @action(detail=True, methods=["post"], url_path="adjust")
    def adjust(self, request, shop_slug=None, pk=None):
        """POST papers/<pk>/adjust/ — adjust quantity_in_stock by delta."""
        from rest_framework.exceptions import ValidationError
        shop = self._get_shop()
        paper = Paper.objects.filter(pk=pk, shop=shop).first()
        if not paper:
            from rest_framework.exceptions import NotFound
            raise NotFound("Paper not found.")
        delta = request.data.get("adjustment")
        if delta is None:
            raise ValidationError("adjustment is required")
        try:
            delta = int(delta)
        except (TypeError, ValueError):
            raise ValidationError("adjustment must be an integer")
        current = paper.quantity_in_stock or 0
        new_qty = max(0, current + delta)
        paper.quantity_in_stock = new_qty
        paper.save(update_fields=["quantity_in_stock"])
        serializer = self.get_serializer(paper)
        return Response(serializer.data)


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


class ShopVolumeDiscountViewSet(ShopScopedMixin, viewsets.ModelViewSet):
    """CRUD /api/shops/{shop_slug}/pricing/discounts/"""

    serializer_class = VolumeDiscountSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        shop = self._get_shop()
        return VolumeDiscount.objects.filter(shop=shop)

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
            return Response({"detail": "Not your quote request."}, status=status.HTTP_403_FORBIDDEN)
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
        # #region agent log
        import json
        import traceback
        from pathlib import Path
        try:
            _log_path = str(Path(__file__).resolve().parent.parent.parent / "debug-981bc1.log")
            with open(_log_path, "a") as _f:
                _f.write(json.dumps({"sessionId": "981bc1", "hypothesisId": "H0", "location": "views.py:TweakedItemUpdateView.patch:entry", "message": "PATCH tweaked-items entry", "data": {"item_id": item_id, "request_data": dict(request.data)}, "timestamp": __import__("time").time() * 1000}) + "\n")
        except Exception:
            pass
        # #endregion
        item = get_object_or_404(
            QuoteItem.objects.select_related("quote_request__shop", "product", "paper", "material", "machine"),
            pk=item_id,
        )
        if item.quote_request.created_by_id != request.user.id:
            return Response({"detail": "Not your quote request item."}, status=status.HTTP_403_FORBIDDEN)
        if item.quote_request.status != QuoteStatus.DRAFT:
            return Response({"detail": "Cannot modify locked items."}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction
        from quotes.pricing_service import compute_and_store_pricing
        from .validators import validate_shop_consistency

        updatable_fields = ["quantity", "sides", "color_mode", "special_instructions", "has_artwork"]
        fk_fields = {"paper": Paper, "material": Material, "machine": Machine}

        try:
            with transaction.atomic():
                for field in updatable_fields:
                    if field in request.data:
                        setattr(item, field, request.data[field])
                for field, model in fk_fields.items():
                    if field in request.data:
                        val = request.data[field]
                        if val in ("", None):
                            setattr(item, f"{field}_id", None)
                        else:
                            related_obj = get_object_or_404(model, pk=val, is_active=True)
                            setattr(item, field, related_obj)
                if "chosen_width_mm" in request.data:
                    item.chosen_width_mm = request.data["chosen_width_mm"]
                if "chosen_height_mm" in request.data:
                    item.chosen_height_mm = request.data["chosen_height_mm"]

                validate_shop_consistency(
                    item.quote_request.shop,
                    product=item.product,
                    paper=item.paper,
                    material=item.material,
                    machine=item.machine,
                )

                item.save()

                if "finishings" in request.data:
                    item.finishings.all().delete()
                    for fin in request.data["finishings"]:
                        fr_id = fin.get("finishing_rate") if isinstance(fin, dict) else fin
                        finishing_rate = get_object_or_404(FinishingRate, pk=fr_id, is_active=True)
                        validate_shop_consistency(
                            item.quote_request.shop,
                            finishing_rate=finishing_rate,
                        )
                        fd = {"quote_item": item, "finishing_rate_id": fr_id}
                        if isinstance(fin, dict):
                            if "price_override" in fin:
                                fd["price_override"] = fin["price_override"]
                            if "apply_to_sides" in fin and fin["apply_to_sides"]:
                                fd["apply_to_sides"] = fin["apply_to_sides"]
                        QuoteItemFinishing.objects.create(**fd)

                compute_and_store_pricing(item)

            item.refresh_from_db()
            serializer = TweakedItemReadSerializer(item, context={"request": request})
            return Response(serializer.data)
        except Exception as e:
            # #region agent log
            try:
                import json
                import traceback as _tb
                from pathlib import Path
                _log_path = str(Path(__file__).resolve().parent.parent.parent / "debug-981bc1.log")
                with open(_log_path, "a") as _f:
                    _f.write(json.dumps({"sessionId": "981bc1", "hypothesisId": "H1-H5", "location": "views.py:TweakedItemUpdateView.patch:exception", "message": "PATCH tweaked-items 500", "data": {"error": str(e), "error_type": type(e).__name__, "traceback": _tb.format_exc(), "request_data": dict(request.data)}, "timestamp": __import__("time").time() * 1000}) + "\n")
            except Exception:
                pass
            # #endregion
            raise


class MatchShopsView(APIView):
    """
    POST /api/public/match-shops/

    Find shops that can fulfill buyer specs. Public, no auth.
    Payload: pricing_mode, dimensions, quantity, sides, color_mode, paper specs, finishing_ids, lat/lng/radius.
    Response: list of shops with can_calculate, reason, missing_fields.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = MatchShopsInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        shops = find_shops_for_spec(
            pricing_mode=data["pricing_mode"],
            finished_width_mm=data["finished_width_mm"],
            finished_height_mm=data["finished_height_mm"],
            quantity=data["quantity"],
            sides=data["sides"],
            color_mode=data["color_mode"],
            sheet_size=data.get("sheet_size") or "SRA3",
            paper_gsm=data.get("paper_gsm"),
            paper_type=data.get("paper_type") or "",
            finishing_ids=data.get("finishing_ids") or [],
            lat=data.get("lat"),
            lng=data.get("lng"),
            radius_km=data.get("radius_km") or 50,
        )

        results = [
            {
                "id": s.id,
                "name": s.name,
                "slug": s.slug or "",
                "can_calculate": True,
                "reason": "Ready to price",
                "missing_fields": [],
            }
            for s in shops
        ]

        return Response(
            MatchShopsResponseSerializer({"shops": results, "total": len(results)}).data,
            status=status.HTTP_200_OK,
        )


class ShopCustomOptionsView(APIView):
    """
    GET /api/public/shops/{slug}/custom-options/

    Returns papers, materials, finishings for custom quote building.
    No product required. Public, no auth.
    """

    permission_classes = [AllowAny]

    def get(self, request, slug):
        shop = get_object_or_404(Shop, slug=slug, is_active=True)
        papers = list(
            Paper.objects.filter(shop=shop, is_active=True, selling_price__gt=0)
            .order_by("sheet_size", "gsm", "paper_type")[:30]
        )
        materials = list(
            Material.objects.filter(shop=shop, is_active=True, selling_price__gt=0)
            .order_by("material_type")[:20]
        )
        finishings = list(
            FinishingRate.objects.filter(shop=shop, is_active=True)
            .select_related("category")
            .order_by("name")[:30]
        )
        return Response({
            "available_papers": [
                {
                    "id": p.id,
                    "sheet_size": p.sheet_size,
                    "gsm": p.gsm,
                    "paper_type": p.get_paper_type_display() or p.paper_type,
                    "selling_price": str(p.selling_price),
                }
                for p in papers
            ],
            "available_materials": [
                {
                    "id": m.id,
                    "material_type": m.material_type,
                    "unit": m.unit,
                    "selling_price": str(m.selling_price),
                }
                for m in materials
            ],
            "available_finishings": [
                {
                    "id": f.id,
                    "slug": f.slug,
                    "name": f.name,
                    "price": str(f.price),
                    "charge_unit": f.charge_unit,
                    "billing_basis": f.billing_basis,
                    "side_mode": f.side_mode,
                    "display_unit_label": f.display_unit_label,
                    "category": f.category.name if f.category else None,
                }
                for f in finishings
            ],
        })


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
