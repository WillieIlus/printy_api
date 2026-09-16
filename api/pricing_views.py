from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.services.roles import ActorRole, get_actor_role
from pricing.services.platform_fee_policy import calculate_financial_split, create_quote_financial_split
from quotes.models import Quote


class QuoteFinancialPreviewInputSerializer(serializers.Serializer):
    production_cost = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    manager_markup = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)

    def validate_production_cost(self, value):
        if value <= 0:
            raise serializers.ValidationError("Production cost must be greater than zero.")
        return value


def _money(value):
    return str(value) if value is not None else ""


def _financial_result_payload(result, *, split_id=None, locked=False):
    return {
        "id": split_id,
        "production_cost": _money(result.production_cost),
        "manager_markup": _money(result.manager_markup),
        "production_fee_component": _money(result.production_fee_component),
        "markup_fee_component": _money(result.markup_fee_component),
        "printy_fee": _money(result.printy_fee),
        "shop_payout": _money(result.shop_payout),
        "manager_payout": _money(result.manager_payout),
        "client_total": _money(result.client_total),
        "currency": result.currency,
        "pricing_tier": result.pricing_tier,
        "policy_version": result.applied_policy_version,
        "locked": bool(locked),
        "broker_client_price": _money(result.broker_client_price),
        "gross_margin": _money(result.gross_margin),
        "printer_side_fee": _money(result.printer_side_fee),
        "broker_margin_fee": _money(result.broker_margin_fee),
        "broker_payout": _money(result.broker_payout),
        "max_allowed_client_price": _money(result.max_allowed_client_price),
        "applied_markup_multiple": _money(result.applied_markup_multiple),
    }


def _financial_split_payload(split):
    return {
        "id": split.id,
        "production_cost": _money(split.production_cost),
        "manager_markup": _money(split.manager_markup),
        "production_fee_component": _money(split.production_fee_component),
        "markup_fee_component": _money(split.markup_fee_component),
        "printy_fee": _money(split.printy_fee),
        "shop_payout": _money(split.shop_payout),
        "manager_payout": _money(split.manager_payout),
        "client_total": _money(split.client_total),
        "currency": split.currency,
        "pricing_tier": split.pricing_tier,
        "policy_version": split.applied_policy_version,
        "locked": bool(split.locked),
        "broker_client_price": _money(split.broker_client_price),
        "gross_margin": _money(split.gross_margin),
        "printer_side_fee": _money(split.printer_side_fee),
        "broker_margin_fee": _money(split.broker_margin_fee),
        "broker_payout": _money(split.broker_payout),
        "max_allowed_client_price": _money(split.max_allowed_client_price),
        "applied_markup_multiple": _money(split.applied_markup_multiple),
    }


def _client_payload(split):
    return {
        "currency": split.currency,
        "client_total": _money(split.client_total),
    }


def _validation_response(exc):
    messages = getattr(exc, "messages", None) or [str(exc)]
    return Response({"detail": messages[0]}, status=status.HTTP_400_BAD_REQUEST)


def _can_view_internal_financials(user, quote):
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    role = get_actor_role(user)
    if role in {ActorRole.BROKER, ActorRole.MANAGER, ActorRole.ADMIN}:
        request = quote.quote_request
        return request.created_by_id == user.id or request.assigned_manager_id == user.id or quote.created_by_id == user.id
    return False


def _can_view_client_financials(user, quote):
    request = quote.quote_request
    return bool(
        _can_view_internal_financials(user, quote)
        or request.created_by_id == getattr(user, "id", None)
        or request.on_behalf_of_id == getattr(user, "id", None)
    )


class QuoteFinancialPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = QuoteFinancialPreviewInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = calculate_financial_split(**serializer.validated_data)
        except ValidationError as exc:
            return _validation_response(exc)
        return Response(_financial_result_payload(result))


class QuoteFinancialDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_quote(self, request, quote_id):
        quote = get_object_or_404(Quote.objects.select_related("quote_request", "financial_split"), pk=quote_id)
        audience = request.query_params.get("audience") or "internal"
        if audience == "client":
            if not _can_view_client_financials(request.user, quote):
                return quote, Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            return quote, None
        if not _can_view_internal_financials(request.user, quote):
            return quote, Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return quote, None

    def get(self, request, quote_id):
        quote, error = self.get_quote(request, quote_id)
        if error is not None:
            return error
        split = getattr(quote, "financial_split", None)
        if split is None:
            return Response({"detail": "Quote financials are not available yet."}, status=status.HTTP_404_NOT_FOUND)
        if request.query_params.get("audience") == "client":
            return Response(_client_payload(split))
        return Response(_financial_split_payload(split))

    def patch(self, request, quote_id):
        quote, error = self.get_quote(request, quote_id)
        if error is not None:
            return error
        if not _can_view_internal_financials(request.user, quote):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = QuoteFinancialPreviewInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            split = create_quote_financial_split(quote=quote, **serializer.validated_data)
        except ValidationError as exc:
            return _validation_response(exc)
        return Response(_financial_split_payload(split))