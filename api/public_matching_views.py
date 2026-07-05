from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent
from quotes.models import CalculatorDraft
from services.production_matching import build_public_single_shop_quote_preview
from services.public_matching import (
    get_booklet_marketplace_matches,
    get_marketplace_matches,
    get_shop_specific_preview,
    recompute_shop_match_readiness,
)
from shops.models import Shop

from .visibility import project_public_marketplace_response
from .public_matching_serializers import (
    PublicBookletMatchPayloadSerializer,
    PublicCalculatorPayloadSerializer,
)


FORBIDDEN_PUBLIC_MATCH_KEYS = {"id", "shop_id", "shop_name", "shop", "slug", "shop_slug", "name"}


def _public_preview_response(payload):
    projected = project_public_marketplace_response(payload)
    for collection_name in ("matches", "shops", "selected_shops", "shop_matches"):
        for item in projected.get(collection_name) or []:
            for key in FORBIDDEN_PUBLIC_MATCH_KEYS:
                item.pop(key, None)
    fixed = projected.get("fixed_shop_preview")
    if isinstance(fixed, dict):
        for key in FORBIDDEN_PUBLIC_MATCH_KEYS:
            fixed.pop(key, None)
    return projected


class PublicMatchShopsView(APIView):
    """
    Public endpoint for marketplace shop matching and preview.
    Sample payload:
    {
        "job_type": "business_cards",
        "quantity": 100,
        "width_mm": 85,
        "height_mm": 55,
        "paper_preference": "300gsm matt",
        "print_sides": "SIMPLEX",
        "location_slug": "westlands"
    }
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PublicCalculatorPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = get_marketplace_matches(serializer.validated_data)
        return Response(_public_preview_response(response))


class PublicMatchBookletShopsView(APIView):
    """Job-first booklet matching — accepts booklet spec, returns best matching shops."""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PublicBookletMatchPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = get_booklet_marketplace_matches(serializer.validated_data)
        return Response(_public_preview_response(response))


class PublicShopCalculatorPreviewView(APIView):
    """
    Public endpoint for a single shop's calculator preview.
    Sample payload:
    {
        "quantity": 500,
        "width_mm": 210,
        "height_mm": 297,
        "paper_type": "art",
        "paper_gsm": 150
    }
    """
    permission_classes = [AllowAny]

    def post(self, request, slug):
        shop = get_object_or_404(Shop, slug=slug, is_active=True, is_public=True)
        recompute_shop_match_readiness(shop)
        serializer = PublicCalculatorPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        response = get_shop_specific_preview(shop, serializer.validated_data)
        return Response(_public_preview_response(response))


class PublicShopQuotePreviewView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, slug):
        shop = get_object_or_404(Shop, slug=slug, is_active=True, is_public=True)
        serializer = PublicCalculatorPayloadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        preview = build_public_single_shop_quote_preview(
            shop=shop,
            payload=serializer.validated_data,
        )
        draft = CalculatorDraft.objects.create(
            user=request.user if request.user.is_authenticated else None,
            guest_session_key=str(request.data.get("session_key") or "").strip(),
            title=str(request.data.get("title") or serializer.validated_data.get("custom_title") or "Direct shop quote preview")[:255],
            calculator_context=CalculatorDraftContext.PUBLIC_GUEST,
            intent=CalculatorDraftIntent.PUBLIC_PREVIEW,
            direct_intake_shop=shop,
            intake_mode=CalculatorDraft.INTAKE_MODE_DIRECT_SHOP,
            calculator_inputs_snapshot=dict(serializer.validated_data),
            request_details_snapshot={
                "source": "direct_shop_public_preview",
                "direct_shop_intake": True,
                "shop_id": shop.id,
                "shop_slug": shop.slug or "",
                "shop_name": shop.name,
                "pricing_preview": preview,
            },
        )
        draft.draft_reference = f"QD-{draft.id}"
        draft.save(update_fields=["draft_reference", "updated_at"])
        preview["draft"] = {
            "id": draft.id,
            "draft_reference": draft.draft_reference,
            "calculator_context": draft.calculator_context,
            "intent": draft.intent,
            "shop_slug": shop.slug or "",
        }
        return Response(preview)
