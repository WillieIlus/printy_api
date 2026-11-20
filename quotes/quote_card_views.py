"""Read-only role-aware Quote card endpoint (Stage 1C)."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.roles import (
    ActiveRoleNotHeld,
    get_request_active_role_raw,
    validate_active_role,
)
from quotes.models import CalculatorDraft, Quote, QuoteRequest
from quotes.quote_projection import build_quote_card

ACTIVE_ROLE_TO_VIEWER = {
    "client": "buyer",
    "partner": "manager",
    "production": "printer",
    "super_admin": "admin",
}

LEGACY_TYPE_ALIASES = {
    "quote": "quote",
    "offer": "quote",
    "response": "quote",
    "request": "request",
    "quote_request": "request",
    "draft": "draft",
    "calculator_draft": "draft",
}


def _can_view_quote(*, user, source, viewer_role: str) -> bool:
    if viewer_role == "admin":
        return True
    if isinstance(source, Quote):
        request = source.quote_request
        if viewer_role == "buyer":
            return request.created_by_id == user.id or request.on_behalf_of_id == user.id
        if viewer_role == "manager":
            return (
                request.assigned_manager_id == user.id
                or request.created_by_manager_id == user.id
                or source.created_by_id == user.id
            )
        if viewer_role == "printer":
            return bool(source.shop_id and source.shop.owner_id == user.id)
        return False
    if isinstance(source, QuoteRequest):
        if viewer_role == "buyer":
            return source.created_by_id == user.id or source.on_behalf_of_id == user.id
        if viewer_role == "manager":
            return (
                source.assigned_manager_id == user.id
                or source.created_by_manager_id == user.id
            )
        if viewer_role == "printer":
            return source.quotes.filter(shop__owner=user).exists()
        return False
    if isinstance(source, CalculatorDraft):
        return viewer_role == "buyer" and source.user_id == user.id
    return False


class QuoteCardView(APIView):
    """One Quote envelope over the legacy request/quote/draft shapes.

    Read-only. The active role comes from ``X-Printy-Active-Role`` and all
    role privacy is applied server-side.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, legacy_type: str, pk: int):
        try:
            active_role = validate_active_role(
                request.user,
                get_request_active_role_raw(request),
            )
        except ActiveRoleNotHeld as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        viewer_role = ACTIVE_ROLE_TO_VIEWER.get(active_role)
        if viewer_role is None:
            return Response(
                {"detail": "This account role cannot open a quote card."},
                status=status.HTTP_403_FORBIDDEN,
            )

        kind = LEGACY_TYPE_ALIASES.get(str(legacy_type or "").lower())
        if kind is None:
            return Response({"detail": "Unknown quote shape."}, status=status.HTTP_404_NOT_FOUND)

        if kind == "quote":
            source = get_object_or_404(
                Quote.objects.select_related(
                    "quote_request",
                    "quote_request__assigned_manager",
                    "quote_request__created_by_manager",
                    "quote_request__created_by",
                    "quote_request__on_behalf_of",
                    "shop",
                    "created_by",
                    "production_option",
                    "financial_split",
                ).prefetch_related("quote_request__items"),
                pk=pk,
            )
        elif kind == "request":
            source = get_object_or_404(
                QuoteRequest.objects.select_related(
                    "assigned_manager",
                    "created_by_manager",
                    "created_by",
                    "on_behalf_of",
                    "shop",
                ).prefetch_related("items", "quotes"),
                pk=pk,
            )
        else:
            source = get_object_or_404(
                CalculatorDraft.objects.select_related("user", "direct_intake_shop"),
                pk=pk,
            )

        if not _can_view_quote(user=request.user, source=source, viewer_role=viewer_role):
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

        return Response(build_quote_card(source, viewer_role=viewer_role))


__all__ = ["QuoteCardView"]
