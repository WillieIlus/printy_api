"""
API views for Printy API.
"""
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError, PermissionDenied

from .models import (
    User,
    Shop,
    SheetSize,
    Paper,
    Machine,
    FinishingRate,
    Material,
    Product,
    QuoteRequest,
    QuoteItem,
)
from .serializers import (
    UserSerializer,
    UserRegistrationSerializer,
    SheetSizeSerializer,
    PaperSerializer,
    PaperCreateSerializer,
    MachineSerializer,
    FinishingRateSerializer,
    MaterialSerializer,
    ProductListSerializer,
    ProductDetailSerializer,
    ProductCreateSerializer,
    ShopListSerializer,
    ShopDetailSerializer,
    QuoteRequestSerializer,
    QuoteRequestCreateSerializer,
    QuoteItemSerializer,
    QuoteItemCreateSerializer,
)
from .permissions import IsShopOwner, IsQuoteBuyerOrSeller, BuyerCanCreateQuote
from .quote_engine import recalculate_quote_request


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(
                UserSerializer(user).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class SheetSizeListView(generics.ListAPIView):
    queryset = SheetSize.objects.all()
    serializer_class = SheetSizeSerializer
    permission_classes = [AllowAny]


class ShopListView(generics.ListAPIView):
    queryset = Shop.objects.all()
    serializer_class = ShopListSerializer
    permission_classes = [AllowAny]


class ShopDetailView(generics.RetrieveAPIView):
    queryset = Shop.objects.all()
    serializer_class = ShopDetailSerializer
    permission_classes = [AllowAny]
    lookup_field = "slug"
    lookup_url_kwarg = "slug"


class ShopDetailByIdView(generics.RetrieveAPIView):
    """GET /api/shops/{id}/ - retrieve shop by numeric ID."""
    queryset = Shop.objects.all()
    serializer_class = ShopDetailSerializer
    permission_classes = [AllowAny]
    lookup_url_kwarg = "shop_id"


class ShopCreateView(generics.CreateAPIView):
    queryset = Shop.objects.all()
    serializer_class = ShopListSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


def _get_shop_filter(kwargs):
    """Resolve shop filter from kwargs: either shop_id or shop_slug."""
    if "shop_id" in kwargs:
        return {"shop_id": kwargs["shop_id"]}
    return {"shop__slug": kwargs["shop_slug"]}


class ProductListView(generics.ListAPIView):
    serializer_class = ProductListSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return Product.objects.filter(
            **_get_shop_filter(self.kwargs), is_active=True
        )


class ProductDetailView(generics.RetrieveAPIView):
    serializer_class = ProductDetailSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        return Product.objects.filter(
            **_get_shop_filter(self.kwargs), is_active=True
        )


class ProductCreateView(generics.CreateAPIView):
    serializer_class = ProductCreateSerializer
    permission_classes = [IsAuthenticated]

    def get_shop(self):
        if "shop_id" in self.kwargs:
            return get_object_or_404(Shop, pk=self.kwargs["shop_id"])
        return get_object_or_404(Shop, slug=self.kwargs["shop_slug"])

    def check_permissions(self, request):
        super().check_permissions(request)
        shop = self.get_shop()
        if shop.owner_id != request.user.pk:
            raise PermissionDenied("Only shop owner can create products.")

    def perform_create(self, serializer):
        serializer.save(shop=self.get_shop())


class PaperCreateView(generics.CreateAPIView):
    serializer_class = PaperCreateSerializer
    permission_classes = [IsAuthenticated]

    def get_shop(self):
        if "shop_id" in self.kwargs:
            return get_object_or_404(Shop, pk=self.kwargs["shop_id"])
        return get_object_or_404(Shop, slug=self.kwargs["shop_slug"])

    def check_permissions(self, request):
        super().check_permissions(request)
        shop = self.get_shop()
        if shop.owner_id != request.user.pk:
            raise PermissionDenied("Only shop owner can create paper.")

    def perform_create(self, serializer):
        serializer.save(shop=self.get_shop())


class QuoteRequestListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return QuoteRequestCreateSerializer
        return QuoteRequestSerializer

    def get_queryset(self):
        user = self.request.user
        # Buyer sees own; seller sees shop's
        return QuoteRequest.objects.filter(buyer=user) | QuoteRequest.objects.filter(
            shop__owner=user
        ).distinct()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quote = serializer.save(buyer=request.user)
        return Response(
            QuoteRequestSerializer(quote).data,
            status=status.HTTP_201_CREATED,
        )


class QuoteRequestDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = QuoteRequestSerializer
    permission_classes = [IsAuthenticated, IsQuoteBuyerOrSeller]

    def get_queryset(self):
        user = self.request.user
        return QuoteRequest.objects.filter(buyer=user) | QuoteRequest.objects.filter(
            shop__owner=user
        ).distinct()


class QuoteItemCreateView(generics.CreateAPIView):
    serializer_class = QuoteItemCreateSerializer
    permission_classes = [IsAuthenticated]

    def get_quote_request(self):
        return get_object_or_404(
            QuoteRequest,
            pk=self.kwargs["quote_pk"],
            buyer=self.request.user,
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        quote = self.get_quote_request()
        ctx["shop"] = quote.shop
        return ctx

    def perform_create(self, serializer):
        quote = self.get_quote_request()
        if quote.status != QuoteRequest.Status.DRAFT:
            raise ValidationError("Can only add items to draft quotes.")
        item = serializer.save(quote_request=quote)
        recalculate_quote_request(quote)


class QuoteItemDestroyView(generics.DestroyAPIView):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = QuoteItem.objects.filter(
            quote_request__buyer=self.request.user,
            quote_request__status=QuoteRequest.Status.DRAFT,
        )
        quote_pk = self.kwargs.get("quote_pk")
        if quote_pk:
            qs = qs.filter(quote_request_id=quote_pk)
        return qs


class QuotePriceView(APIView):
    """Seller action: recalculate and mark as priced."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        quote = get_object_or_404(QuoteRequest, pk=pk)
        if quote.shop.owner_id != request.user.pk:
            return Response(
                {"detail": "Only shop owner can price quotes."},
                status=status.HTTP_403_FORBIDDEN,
            )
        recalculate_quote_request(quote)
        quote.status = QuoteRequest.Status.PRICED
        quote.save()
        return Response(QuoteRequestSerializer(quote).data)


class QuoteSubmitView(APIView):
    """Buyer action: submit quote for pricing."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        quote = get_object_or_404(
            QuoteRequest,
            pk=pk,
            buyer=request.user,
        )
        if quote.status != QuoteRequest.Status.DRAFT:
            return Response(
                {"detail": "Quote already submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not quote.items.exists():
            return Response(
                {"detail": "Add at least one item before submitting."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        quote.status = QuoteRequest.Status.SUBMITTED
        quote.save()
        return Response(QuoteRequestSerializer(quote).data)
