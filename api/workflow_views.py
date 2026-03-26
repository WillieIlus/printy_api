from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.roles import is_client
from quotes.models import QuoteDraft, QuoteRequest, ShopQuote
from quotes.services_workflow import create_quote_response, save_quote_draft, send_quote_draft_to_shops
from services.pricing.quote_builder import build_quote_preview
from setup.services import get_setup_status_for_shop, get_setup_status_for_user
from shops.models import Shop
from shops.services import can_manage_quotes, can_manage_shop

from .workflow_serializers import (
    CalculatorPreviewSerializer,
    QuoteDraftCreateSerializer,
    QuoteDraftReadSerializer,
    QuoteDraftSendSerializer,
    QuoteRequestReadSerializer,
    QuoteResponseCreateSerializer,
    QuoteResponseReadSerializer,
)


class SetupStatusCompatView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(get_setup_status_for_user(request.user))


class ShopSetupStatusCompatView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, shop_slug):
        shop = get_object_or_404(Shop, slug=shop_slug)
        if not can_manage_shop(shop, request.user):
            return Response({"detail": "You cannot access this shop setup status."}, status=status.HTTP_403_FORBIDDEN)
        return Response(get_setup_status_for_shop(shop))


class CalculatorPreviewView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CalculatorPreviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        pricing = build_quote_preview(
            shop=validated["shop"],
            product_id=validated["product"].id,
            quantity=validated["quantity"],
            paper_id=validated["paper"],
            machine_id=validated["machine"],
            color_mode=validated["color_mode"],
            sides=validated["sides"],
            finishing_selections=validated.get("finishings") or [],
        )
        return Response(pricing)


class QuoteDraftListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = QuoteDraftReadSerializer

    def get_queryset(self):
        return QuoteDraft.objects.filter(user=self.request.user).select_related("shop", "selected_product")

    def get_serializer_class(self):
        if self.request.method == "POST":
            return QuoteDraftCreateSerializer
        return QuoteDraftReadSerializer

    def create(self, request, *args, **kwargs):
        if not is_client(request.user):
            return Response({"detail": "Only client accounts can save quote drafts."}, status=status.HTTP_403_FORBIDDEN)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        draft = save_quote_draft(
            user=request.user,
            selected_product=validated.get("selected_product"),
            shop=validated.get("shop"),
            title=validated.get("title", ""),
            calculator_inputs_snapshot=validated["calculator_inputs_snapshot"],
            pricing_snapshot=validated.get("pricing_snapshot"),
            custom_product_snapshot=validated.get("custom_product_snapshot"),
            request_details_snapshot=validated.get("request_details_snapshot"),
        )
        return Response(QuoteDraftReadSerializer(draft).data, status=status.HTTP_201_CREATED)


class QuoteDraftDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = QuoteDraftReadSerializer

    def get_queryset(self):
        return QuoteDraft.objects.filter(user=self.request.user)


class QuoteDraftSendView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        if not is_client(request.user):
            return Response({"detail": "Only client accounts can send drafts to shops."}, status=status.HTTP_403_FORBIDDEN)
        draft = get_object_or_404(QuoteDraft, pk=pk, user=request.user)
        serializer = QuoteDraftSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quote_requests = send_quote_draft_to_shops(
            draft=draft,
            shops=list(serializer.validated_data["shops"]),
            request_details_snapshot=serializer.validated_data.get("request_details_snapshot"),
        )
        return Response(QuoteRequestReadSerializer(quote_requests, many=True).data, status=status.HTTP_201_CREATED)


class QuoteResponseCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id):
        quote_request = get_object_or_404(QuoteRequest.objects.select_related("shop"), pk=request_id)
        if not can_manage_quotes(quote_request.shop, request.user):
            return Response({"detail": "You cannot respond to quote requests for this shop."}, status=status.HTTP_403_FORBIDDEN)
        serializer = QuoteResponseCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = create_quote_response(
            quote_request=quote_request,
            shop=quote_request.shop,
            user=request.user,
            status=serializer.validated_data["status"],
            response_snapshot=serializer.validated_data["response_snapshot"],
            revised_pricing_snapshot=serializer.validated_data.get("revised_pricing_snapshot"),
            total=serializer.validated_data.get("total"),
            note=serializer.validated_data.get("note", ""),
            turnaround_days=serializer.validated_data.get("turnaround_days"),
        )
        return Response(QuoteResponseReadSerializer(response).data, status=status.HTTP_201_CREATED)


class ShopHomeDashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shop = Shop.objects.filter(owner=request.user).order_by("id").first()
        if not shop:
            membership_shop = Shop.objects.filter(memberships__user=request.user, memberships__is_active=True).order_by("id").first()
            shop = membership_shop
        if not shop or not can_manage_quotes(shop, request.user):
            return Response({"detail": "No accessible shop dashboard."}, status=status.HTTP_403_FORBIDDEN)

        received = QuoteRequest.objects.filter(shop=shop)
        responses = ShopQuote.objects.filter(shop=shop)
        counts = dict(received.values("status").annotate(count=Count("id")).values_list("status", "count"))

        return Response(
            {
                "shop": {"id": shop.id, "name": shop.name, "slug": shop.slug},
                "received_quote_requests": received.count(),
                "status_counts": {
                    "pending": counts.get("submitted", 0),
                    "modified": responses.filter(status="modified").count(),
                    "accepted": responses.filter(status="accepted").count(),
                    "rejected": responses.filter(status__in=["rejected", "declined"]).count(),
                },
                "recent_requests": QuoteRequestReadSerializer(received.order_by("-created_at")[:10], many=True).data,
                "recent_responses": QuoteResponseReadSerializer(responses.order_by("-created_at")[:10], many=True).data,
            }
        )
