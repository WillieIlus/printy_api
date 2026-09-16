"""Public views for the React quote calculator.

The views are intentionally thin:
1. validate and normalize buyer-friendly fields;
2. delegate all pricing to the canonical pricing service;
3. save CalculatorDraft snapshots for the existing claim/send workflow.
"""

from __future__ import annotations

import secrets
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.throttling import GuestQuoteRequestThrottle
from api.visibility import project_public_marketplace_response
from quotes.choices import (
    CalculatorDraftContext,
    CalculatorDraftIntent,
)
from quotes.models import CalculatorDraft
from services.pricing.calculator_config import get_calculator_config
from services.pricing.calculator_preview import build_public_calculator_preview

from .serializers import (
    ClientCalculatorDraftSerializer,
    ClientCalculatorInputSerializer,
)
from .services import build_client_quote_response


QUANTITY_LADDERS = {
    "business_card": [100, 250, 500, 1000, 2500, 5000, 10000],
    "flyer": [250, 500, 1000, 2500, 5000, 10000, 20000],
    "booklet": [50, 100, 250, 500, 1000, 2500],
    "label_sticker": [100, 250, 500, 1000, 2500, 5000],
    "letterhead": [100, 500, 1000, 2500, 5000, 10000],
    "large_format": [1, 2, 5, 10, 25, 50],
}


def _run_preview(payload: dict) -> tuple[dict, dict]:
    raw_preview = build_public_calculator_preview(payload)
    public_preview = project_public_marketplace_response(raw_preview)
    response = build_client_quote_response(
        preview=public_preview,
        payload=payload,
    )
    return public_preview, response


def _attach_quantity_breaks(payload: dict, response: dict) -> None:
    """Add two useful higher-quantity comparisons using the same engine."""

    quantity = int(payload.get("quantity") or 0)
    ladder = QUANTITY_LADDERS.get(payload.get("product_type"), [])
    candidates = [value for value in ladder if value > quantity][:2]
    try:
        base_unit = Decimal(str(response["total"]["unit_price"]))
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return

    breaks = []
    for candidate in candidates:
        candidate_payload = {**payload, "quantity": candidate}
        _, candidate_response = _run_preview(candidate_payload)
        unit = candidate_response.get("total", {}).get("unit_price")
        total = candidate_response.get("total", {}).get("grand_total")
        if unit in (None, "") or total in (None, ""):
            continue
        candidate_unit = Decimal(str(unit))
        saving = (
            ((base_unit - candidate_unit) / base_unit) * Decimal("100")
            if base_unit > 0
            else Decimal("0")
        )
        breaks.append(
            {
                "quantity": candidate,
                "grand_total": total,
                "unit_price": str(candidate_unit.quantize(Decimal("0.01"))),
                "unit_saving_percent": str(max(Decimal("0"), saving).quantize(Decimal("0.1"))),
            }
        )
    response["quantity_breaks"] = breaks


class ClientCalculatorConfigView(APIView):
    """Return canonical products, papers, sizes and finishing choices."""

    permission_classes = [AllowAny]

    def get(self, request):
        config = get_calculator_config()
        return Response(
            {
                "version": 1,
                "currency": "KES",
                "config": config,
                "contract": {
                    "preview": "/api/client-calculator/preview/",
                    "save_draft": "/api/client-calculator/drafts/",
                    "color_modes": ["COLOR", "BW"],
                    "sides": ["SIMPLEX", "DUPLEX"],
                    "turnaround_tiers": ["standard", "priority_48", "rush_24"],
                    "artwork_services": ["print_ready", "file_fix", "full_design"],
                    "delivery_methods": [
                        "pickup", "nairobi_cbd", "nairobi_metro", "upcountry"
                    ],
                },
            }
        )


class ClientCalculatorPreviewView(APIView):
    """Calculate a buyer-safe quote without creating database records."""

    permission_classes = [AllowAny]
    throttle_classes = [GuestQuoteRequestThrottle]

    def post(self, request):
        serializer = ClientCalculatorInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.to_engine_payload()
        _, response = _run_preview(payload)
        if serializer.validated_data.get("include_quantity_breaks"):
            _attach_quantity_breaks(payload, response)
        return Response(response)


class ClientCalculatorDraftView(APIView):
    """Reprice and save the exact calculator state as a canonical draft.

    This endpoint does not create a second quote model. Authenticated clients
    continue through calculator/drafts/{id}/send/. Guests claim the returned
    draft after authentication using calculator/drafts/claim/.
    """

    permission_classes = [AllowAny]
    throttle_classes = [GuestQuoteRequestThrottle]

    @transaction.atomic
    def post(self, request):
        serializer = ClientCalculatorDraftSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        payload = serializer.to_engine_payload()
        public_preview, client_response = _run_preview(payload)

        if not client_response["can_calculate"]:
            return Response(client_response, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

        user = request.user if request.user.is_authenticated else None
        guest_key = (
            serializer.validated_data.get("guest_session_key")
            or secrets.token_urlsafe(24)
        )
        details = {
            "source": "react_client_calculator_v1",
            "customer_name": serializer.validated_data.get("customer_name", ""),
            "customer_email": serializer.validated_data.get("customer_email", ""),
            "customer_phone": serializer.validated_data.get("customer_phone", ""),
            "delivery_address": serializer.validated_data.get("delivery_address", ""),
            "artwork_service": payload.get("artwork_service"),
            "delivery_method": payload.get("delivery_method"),
            "manager_selection_mode": "printy_auto",
        }

        draft = CalculatorDraft.objects.create(
            user=user,
            guest_session_key="" if user else guest_key,
            title=str(payload.get("custom_title") or "Print quote")[:255],
            calculator_context=(
                CalculatorDraftContext.CLIENT_DASHBOARD
                if user
                else CalculatorDraftContext.PUBLIC_GUEST
            ),
            intent=CalculatorDraftIntent.CLIENT_QUOTE_REQUEST,
            calculator_inputs_snapshot=payload,
            pricing_snapshot=public_preview,
            request_details_snapshot=details,
        )
        draft.draft_reference = f"QD-{draft.id}"
        draft.save(update_fields=["draft_reference", "updated_at"])

        return Response(
            {
                **client_response,
                "draft": {
                    "id": draft.id,
                    "reference": draft.draft_reference,
                    "guest_session_key": "" if user else guest_key,
                    "claim_required": not bool(user),
                    "status": draft.status,
                    "next": (
                        f"/api/calculator/drafts/{draft.id}/send/"
                        if user
                        else "/api/calculator/drafts/claim/"
                    ),
                },
            },
            status=status.HTTP_201_CREATED,
        )