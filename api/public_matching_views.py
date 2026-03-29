from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from services.public_matching import get_marketplace_matches, get_shop_specific_preview, recompute_shop_match_readiness
from shops.models import Shop

from .public_matching_serializers import PublicCalculatorPayloadSerializer, PublicCalculatorResponseSerializer


class PublicMatchShopsView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PublicCalculatorPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = get_marketplace_matches(serializer.validated_data)
        return Response(PublicCalculatorResponseSerializer(response).data)


class PublicShopCalculatorPreviewView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, slug):
        shop = get_object_or_404(Shop, slug=slug, is_active=True, is_public=True)
        recompute_shop_match_readiness(shop)
        serializer = PublicCalculatorPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = get_shop_specific_preview(shop, serializer.validated_data)
        return Response(PublicCalculatorResponseSerializer(response).data)
