"""Speed tier options and quote selection (prompt 6).

The numbers are computed in ``services.pricing.speed_tiers``; these views only
validate input and project what each role may see. Buyer payloads contain the
price and time only — never shop identity or a cost breakdown.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.visibility import projection_role_for_request
from catalog.models import Product
from quotes.models import QuoteRequest
from services.pricing.role_projection import ROLE_MANAGER, ROLE_PRINTER
from services.pricing.speed_tiers import (
    apply_speed_selection,
    build_speed_options,
    buyer_speed_payload,
    select_speed_option,
)
from shops.models import Shop

MANAGER_ROLES = {ROLE_MANAGER, ROLE_PRINTER}


def _decimal(value, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field} must be a number.")
    if parsed < 0:
        raise ValueError(f"{field} cannot be negative.")
    return parsed


def _parse_start(value) -> datetime:
    if not value:
        return timezone.now()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("start_at must be an ISO datetime.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _category_for(*, product_id, category: str) -> str:
    if category:
        return category.strip().lower()
    if product_id:
        product = Product.objects.select_related("category").filter(pk=product_id).first()
        if product is not None:
            slug = getattr(getattr(product, "category", None), "slug", "")
            return (slug or getattr(product, "slug", "") or "").lower()
    return ""


class SpeedOptionsView(APIView):
    """GET /api/pricing/speed-options/?shop=&product=&quantity=&base_production_cost="""

    permission_classes = [AllowAny]

    def get(self, request):
        shop_id = request.query_params.get("shop") or request.query_params.get("shop_id")
        if not shop_id:
            return Response({"detail": "shop is required."}, status=status.HTTP_400_BAD_REQUEST)
        shop = get_object_or_404(Shop, pk=shop_id)
        try:
            base = _decimal(
                request.query_params.get("base_production_cost"),
                field="base_production_cost",
            )
            start = _parse_start(request.query_params.get("start_at"))
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        category = _category_for(
            product_id=request.query_params.get("product"),
            category=request.query_params.get("product_category", ""),
        )
        options = build_speed_options(
            shop=shop,
            product_category=category,
            base_production_cost=base,
            start_at=start,
        )
        buyer = buyer_speed_payload(options=options)
        role = projection_role_for_request(request)
        payload = {**buyer, "role": role}
        if role in MANAGER_ROLES:
            payload["options_detail"] = options
            payload["shop"] = {"id": shop.id, "name": shop.name}
        return Response(payload)


class QuoteSpeedSelectionView(APIView):
    """POST /api/pricing/quote-speed/ {quote_request, speed_tier}."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        quote_request_id = request.data.get("quote_request") or request.data.get("quote_request_id")
        tier_key = str(request.data.get("speed_tier") or "").strip().lower()
        if not quote_request_id or not tier_key:
            return Response(
                {"detail": "quote_request and speed_tier are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        quote_request = get_object_or_404(
            QuoteRequest.objects.select_related("shop"),
            pk=quote_request_id,
        )
        user = request.user
        if not (
            getattr(user, "is_staff", False)
            or quote_request.created_by_id == user.id
            or quote_request.assigned_manager_id == user.id
            or quote_request.created_by_manager_id == user.id
        ):
            return Response({"detail": "You cannot change this request."}, status=status.HTTP_403_FORBIDDEN)
        shop = quote_request.shop
        if shop is None:
            return Response(
                {"detail": "This request has no production shop yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        base_value = request.data.get("base_production_cost")
        if base_value in (None, ""):
            base_value = sum(
                (item.line_total or Decimal("0")) for item in quote_request.items.all()
            )
        try:
            base = _decimal(base_value, field="base_production_cost")
            start = _parse_start(request.data.get("start_at"))
        except ValueError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        category = _category_for(
            product_id=request.data.get("product"),
            category=str(request.data.get("product_category") or ""),
        )
        options = build_speed_options(
            shop=shop,
            product_category=category,
            base_production_cost=base,
            start_at=start,
        )
        option = select_speed_option(options=options, tier_key=tier_key)
        if option is None:
            return Response(
                {"detail": "That speed option is not available for this printer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        snapshot = apply_speed_selection(quote_request=quote_request, option=option)
        return Response({"quote_request": quote_request.id, "speed": snapshot})
