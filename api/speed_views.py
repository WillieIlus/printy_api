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
from pricing.models import ShopSpeedTierOptIn, SpeedTier
from services.pricing.role_projection import ROLE_MANAGER, ROLE_PRINTER
from services.pricing.speed_config import (
    FASTER_TIER,
    FASTEST_TIER,
    STANDARD_TIER,
    surcharge_percent_for_tier,
)
from services.pricing.speed_tiers import (
    apply_speed_selection,
    build_speed_options,
    buyer_speed_payload,
    select_speed_option,
    shop_tier_target_hours,
)
from shops.models import Shop
from shops.services import can_manage_shop, refresh_shop_pricing_ready

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


class ShopSpeedSettingsView(APIView):
    """GET/POST /api/pricing/shop-speed-settings/ — printer's own speed setup.

    LAW 17/19: the owner declares the shop's standard turnaround, may override
    either rush tier's hours, and may switch either tier off. Standard is always
    on. A switched-off tier is excluded from every speed option list.
    """

    permission_classes = [IsAuthenticated]

    def _shop_for(self, request, shop_id):
        if not shop_id:
            return None, Response({"detail": "shop is required."}, status=status.HTTP_400_BAD_REQUEST)
        shop = get_object_or_404(Shop, pk=shop_id)
        if not can_manage_shop(shop, request.user):
            return None, Response(
                {"detail": "You cannot change this shop's speed settings."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return shop, None

    def _payload(self, shop) -> dict:
        tiers = []
        for tier in SpeedTier.objects.filter(key__in=[FASTER_TIER, FASTEST_TIER]).order_by("sort_order", "id"):
            optin = ShopSpeedTierOptIn.objects.filter(shop=shop, tier=tier).first()
            tiers.append(
                {
                    "key": tier.key,
                    "label": tier.label,
                    "enabled": bool(optin.is_active) if optin else False,
                    "hours": shop_tier_target_hours(shop=shop, tier=tier),
                    "hours_overridden": bool(optin and optin.target_hours),
                    "surcharge_percent": surcharge_percent_for_tier(tier.key),
                }
            )
        return {
            "shop": shop.id,
            "standard_turnaround_hours": shop.standard_turnaround_hours,
            "tiers": tiers,
        }

    def get(self, request):
        shop, error = self._shop_for(request, request.query_params.get("shop"))
        if error:
            return error
        return Response(self._payload(shop))

    def post(self, request):
        shop, error = self._shop_for(request, request.data.get("shop"))
        if error:
            return error
        standard = request.data.get("standard_turnaround_hours")
        if standard not in (None, ""):
            try:
                hours = int(standard)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "standard_turnaround_hours must be a whole number of hours."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if hours < 1:
                return Response(
                    {"detail": "standard_turnaround_hours must be at least 1 hour."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            shop.standard_turnaround_hours = hours
            shop.save(update_fields=["standard_turnaround_hours", "updated_at"])
            refresh_shop_pricing_ready(shop)
        tiers = request.data.get("tiers") or []
        if not isinstance(tiers, list):
            return Response({"detail": "tiers must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        for entry in tiers:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "").strip().lower()
            if key == STANDARD_TIER:
                return Response(
                    {"detail": "The standard speed can never be switched off."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            tier = SpeedTier.objects.filter(key=key).first()
            if tier is None:
                return Response(
                    {"detail": f"Unknown speed tier: {key}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raw_hours = entry.get("hours")
            if raw_hours in (None, ""):
                override = None
            else:
                try:
                    override = int(raw_hours)
                except (TypeError, ValueError):
                    return Response(
                        {"detail": f"{key} hours must be a whole number."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                if override < 1:
                    return Response(
                        {"detail": f"{key} hours must be at least 1 hour."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            ShopSpeedTierOptIn.objects.update_or_create(
                shop=shop,
                tier=tier,
                defaults={"is_active": bool(entry.get("enabled", True)), "target_hours": override},
            )
        return Response(self._payload(shop))
