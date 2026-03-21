"""
Quote marketplace API views — customer vs shop separation.

A. Customer: /quote-requests/ — create, list, retrieve, submit, accept, cancel
B. Shop: /shops/<slug>/incoming-requests/ — list, retrieve, send-quote, mark-viewed, decline
C. Shop: /sent-quotes/<id>/ — retrieve, partial_update (revise), create-job
"""
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from .permissions import IsQuoteRequestBuyer, IsQuoteRequestSeller, IsShopQuoteOwner
from rest_framework.response import Response

from notifications.models import Notification
from quotes.choices import QuoteStatus, ShopQuoteStatus
from quotes.models import QuoteRequest, QuoteRequestAttachment, ShopQuote, ShopQuoteAttachment
from shops.models import Shop

from .quote_serializers import (
    QuoteRequestAttachmentSerializer,
    QuoteRequestAttachmentUploadSerializer,
    QuoteRequestCustomerCreateSerializer,
    QuoteRequestCustomerDetailSerializer,
    QuoteRequestCustomerListSerializer,
    QuoteRequestCustomerUpdateSerializer,
    QuoteRequestShopDetailSerializer,
    QuoteRequestShopListSerializer,
    ShopQuoteAttachmentSerializer,
    ShopQuoteAttachmentUploadSerializer,
    ShopQuoteCreateSerializer,
    ShopQuoteDetailSerializer,
    ShopQuoteListSerializer,
    ShopQuoteUpdateSerializer,
)


# ---------------------------------------------------------------------------
# A. Customer: /quote-requests/
# ---------------------------------------------------------------------------


class CustomerQuoteRequestViewSet(viewsets.ModelViewSet):
    """
    Customer quote request flow.
    POST /quote-requests/ — create
    GET /quote-requests/ — list my requests
    GET /quote-requests/{id}/ — view one
    PATCH /quote-requests/{id}/ — update draft
    POST /quote-requests/{id}/submit/ — submit draft
    POST /quote-requests/{id}/accept/ — accept shop quote
    POST /quote-requests/{id}/cancel/ — cancel request
    """

    permission_classes = [IsAuthenticated, IsQuoteRequestBuyer]

    def get_queryset(self):
        user = self.request.user
        qs = QuoteRequest.objects.filter(created_by=user)
        if user.is_staff:
            qs = QuoteRequest.objects.all()
        return qs.select_related(
            "shop", "delivery_location"
        ).prefetch_related(
            "items__product", "items__paper", "items__material", "items__finishings__finishing_rate",
            "services__service_rate", "attachments",
        ).order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "create":
            return QuoteRequestCustomerCreateSerializer
        if self.action in ("update", "partial_update"):
            return QuoteRequestCustomerUpdateSerializer
        if self.action == "list":
            return QuoteRequestCustomerListSerializer
        return QuoteRequestCustomerDetailSerializer

    def perform_create(self, serializer):
        serializer.save()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            QuoteRequestCustomerDetailSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        qr = self.get_object()
        if qr.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Only draft quote requests can be updated."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().update(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        """Submit draft (status -> submitted)."""
        from notifications.services import notify

        qr = self.get_object()
        if qr.status != QuoteStatus.DRAFT:
            return Response(
                {"detail": "Only draft quote requests can be submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qr.status = QuoteStatus.SUBMITTED
        qr.save(update_fields=["status", "updated_at"])
        if qr.shop.owner_id and qr.shop.owner_id != request.user.id:
            notify(
                recipient=qr.shop.owner,
                notification_type=Notification.QUOTE_REQUEST_SUBMITTED,
                message=f"New quote request #{qr.id} from {qr.customer_name or 'customer'}",
                object_type="quote_request",
                object_id=qr.id,
                actor=request.user,
            )
        return Response(QuoteRequestCustomerDetailSerializer(qr).data)

    @action(detail=True, methods=["post"], url_path="accept")
    def accept(self, request, pk=None):
        """Accept a sent quote. Body: { "sent_quote_id": <id> } (or "shop_quote_id" for backwards compat)."""
        qr = self.get_object()
        shop_quote_id = request.data.get("sent_quote_id") or request.data.get("shop_quote_id")
        if not shop_quote_id:
            return Response(
                {"detail": "sent_quote_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        shop_quote = get_object_or_404(
            ShopQuote.objects.filter(quote_request=qr),
            pk=shop_quote_id,
        )
        if shop_quote.status not in (ShopQuoteStatus.SENT, ShopQuoteStatus.REVISED):
            return Response(
                {"detail": "Only sent or revised quotes can be accepted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if qr.status == QuoteStatus.ACCEPTED:
            return Response(
                {"detail": "Request already accepted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        shop_quote.status = ShopQuoteStatus.ACCEPTED
        shop_quote.save(update_fields=["status", "updated_at"])
        qr.status = QuoteStatus.ACCEPTED
        qr.save(update_fields=["status", "updated_at"])
        if qr.shop.owner_id and qr.shop.owner_id != request.user.id:
            from notifications.services import notify

            notify(
                recipient=qr.shop.owner,
                notification_type=Notification.SHOP_QUOTE_ACCEPTED,
                message=f"Your quote for request #{qr.id} was accepted",
                object_type="shop_quote",
                object_id=shop_quote.id,
                actor=request.user,
            )
        return Response(QuoteRequestCustomerDetailSerializer(qr).data)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """Cancel quote request (draft or submitted)."""
        qr = self.get_object()
        if qr.status in (QuoteStatus.ACCEPTED, QuoteStatus.CLOSED, QuoteStatus.CANCELLED):
            return Response(
                {"detail": "Cannot cancel an accepted, closed, or already cancelled request."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qr.status = QuoteStatus.CANCELLED
        qr.save(update_fields=["status", "updated_at"])
        if qr.shop.owner_id and qr.shop.owner_id != request.user.id:
            from notifications.services import notify

            notify(
                recipient=qr.shop.owner,
                notification_type=Notification.QUOTE_REQUEST_CANCELLED,
                message=f"Quote request #{qr.id} was cancelled",
                object_type="quote_request",
                object_id=qr.id,
                actor=request.user,
            )
        return Response(QuoteRequestCustomerDetailSerializer(qr).data)


# ---------------------------------------------------------------------------
# B. Shop: /shops/<slug>/incoming-requests/
# ---------------------------------------------------------------------------


class IncomingRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Shop incoming quote requests.
    GET /shops/<slug>/incoming-requests/ — list
    GET /shops/<slug>/incoming-requests/{id}/ — view detail
    POST /shops/<slug>/incoming-requests/{id}/send-quote/ — send shop quote
    POST /shops/<slug>/incoming-requests/{id}/mark-viewed/ — mark as viewed
    POST /shops/<slug>/incoming-requests/{id}/decline/ — decline request
    """

    permission_classes = [IsAuthenticated, IsQuoteRequestSeller]
    lookup_url_kwarg = "request_id"

    def get_shop(self):
        return get_object_or_404(Shop, slug=self.kwargs["shop_slug"], is_active=True)

    def get_queryset(self):
        shop = self.get_shop()
        if not self.check_shop_owner():
            return QuoteRequest.objects.none()
        return QuoteRequest.objects.filter(shop=shop).select_related(
            "shop", "delivery_location"
        ).prefetch_related(
            "items__product", "items__paper", "items__material", "items__finishings__finishing_rate",
            "services__service_rate", "shop_quotes", "attachments",
        ).order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "list":
            return QuoteRequestShopListSerializer
        return QuoteRequestShopDetailSerializer

    def check_shop_owner(self):
        shop = self.get_shop()
        if self.request.user.is_staff:
            return True
        return shop.owner_id == self.request.user.id

    def list(self, request, *args, **kwargs):
        if not self.check_shop_owner():
            return Response({"detail": "Not your shop."}, status=status.HTTP_403_FORBIDDEN)
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        if not self.check_shop_owner():
            return Response({"detail": "Not your shop."}, status=status.HTTP_403_FORBIDDEN)
        return super().retrieve(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="send-quote")
    def send_quote(self, request, shop_slug=None, request_id=None):
        """Send shop quote. Body: { "total", "note", "turnaround_days" }."""
        from django.db import transaction
        from django.utils import timezone
        from quotes.pricing_service import compute_and_store_pricing

        if not self.check_shop_owner():
            return Response({"detail": "Not your shop."}, status=status.HTTP_403_FORBIDDEN)
        qr = self.get_object()
        if qr.status not in (QuoteStatus.SUBMITTED, QuoteStatus.VIEWED):
            return Response(
                {"detail": "Only submitted or viewed requests can receive a quote."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = ShopQuoteCreateSerializer(
            data=request.data,
            context={"quote_request": qr, "request": request},
        )
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            for item in qr.items.all():
                compute_and_store_pricing(item)
            total = serializer.validated_data.get("total")
            if total is None:
                total = sum((i.line_total or 0) for i in qr.items.all())
            now = timezone.now()
            shop_quote = serializer.save()
            from quotes.summary_service import get_shop_quote_summary_text

            shop_quote.total = total
            shop_quote.sent_at = now
            shop_quote.pricing_locked_at = now
            shop_quote.whatsapp_message = get_shop_quote_summary_text(shop_quote)
            shop_quote.save(update_fields=["total", "sent_at", "pricing_locked_at", "whatsapp_message", "updated_at"])
            qr.items.update(shop_quote=shop_quote)
            qr.status = QuoteStatus.QUOTED
            qr.save(update_fields=["status", "updated_at"])
        if qr.created_by_id and qr.created_by_id != request.user.id:
            from notifications.services import notify

            notify(
                recipient=qr.created_by,
                notification_type=Notification.SHOP_QUOTE_SENT,
                message=f"{qr.shop.name} sent you a quote for request #{qr.id}",
                object_type="quote_request",
                object_id=qr.id,
                actor=request.user,
            )
        return Response(
            QuoteRequestShopDetailSerializer(qr).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="mark-viewed")
    def mark_viewed(self, request, shop_slug=None, request_id=None):
        """Mark request as viewed by shop."""
        if not self.check_shop_owner():
            return Response({"detail": "Not your shop."}, status=status.HTTP_403_FORBIDDEN)
        qr = self.get_object()
        if qr.status == QuoteStatus.SUBMITTED:
            qr.status = QuoteStatus.VIEWED
            qr.save(update_fields=["status", "updated_at"])
        return Response(QuoteRequestShopDetailSerializer(qr).data)

    @action(detail=True, methods=["post"], url_path="decline")
    def decline(self, request, shop_slug=None, request_id=None):
        """Decline the request (shop will not quote)."""
        if not self.check_shop_owner():
            return Response({"detail": "Not your shop."}, status=status.HTTP_403_FORBIDDEN)
        qr = self.get_object()
        if qr.status in (QuoteStatus.ACCEPTED, QuoteStatus.CLOSED, QuoteStatus.CANCELLED):
            return Response(
                {"detail": "Cannot decline an accepted, closed, or cancelled request."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qr.status = QuoteStatus.CLOSED
        qr.save(update_fields=["status", "updated_at"])
        if qr.created_by_id and qr.created_by_id != request.user.id:
            from notifications.services import notify

            notify(
                recipient=qr.created_by,
                notification_type=Notification.REQUEST_DECLINED,
                message=f"{qr.shop.name} declined your quote request #{qr.id}",
                object_type="quote_request",
                object_id=qr.id,
                actor=request.user,
            )
        return Response(QuoteRequestShopDetailSerializer(qr).data)


# ---------------------------------------------------------------------------
# C. Shop: /sent-quotes/
# ---------------------------------------------------------------------------


class ShopQuoteViewSet(viewsets.ModelViewSet):
    """
    Sent quotes — shop's quotes sent to customers.
    GET /sent-quotes/ — list shop's sent quotes
    GET /sent-quotes/{id}/ — view detail
    PATCH /sent-quotes/{id}/ — revise (note, turnaround_days, total)
    POST /sent-quotes/{id}/create-job/ — create job from accepted quote
    """

    permission_classes = [IsAuthenticated, IsShopQuoteOwner]
    http_method_names = ["get", "head", "options", "patch"]

    def get_queryset(self):
        user = self.request.user
        qs = ShopQuote.objects.filter(shop__owner=user)
        if user.is_staff:
            qs = ShopQuote.objects.all()
        return qs.select_related(
            "quote_request", "shop"
        ).prefetch_related(
            "items__product", "items__paper", "items__material", "items__finishings__finishing_rate",
            "attachments",
        ).order_by("-sent_at", "-created_at")

    def get_serializer_class(self):
        if self.action in ("update", "partial_update"):
            return ShopQuoteUpdateSerializer
        if self.action == "list":
            return ShopQuoteListSerializer
        return ShopQuoteDetailSerializer

    def partial_update(self, request, *args, **kwargs):
        from notifications.services import notify

        quote = self.get_object()
        if quote.shop.owner_id != request.user.id:
            return Response({"detail": "Not your shop quote."}, status=status.HTTP_403_FORBIDDEN)
        response = super().partial_update(request, *args, **kwargs)
        quote.refresh_from_db()
        from quotes.summary_service import get_shop_quote_summary_text

        quote.whatsapp_message = get_shop_quote_summary_text(quote)
        quote.save(update_fields=["whatsapp_message"])
        qr = quote.quote_request
        if qr.created_by_id and qr.created_by_id != request.user.id:
            notify(
                recipient=qr.created_by,
                notification_type=Notification.SHOP_QUOTE_REVISED,
                message=f"{quote.shop.name} revised their quote for request #{qr.id}",
                object_type="shop_quote",
                object_id=quote.id,
                actor=request.user,
            )
        return response

    @action(detail=True, methods=["post"], url_path="create-job")
    def create_job(self, request, pk=None):
        """Create production job from accepted shop quote."""
        from production.serializers import ProductionOrderSerializer, ProductionOrderWriteSerializer

        quote = self.get_object()
        if quote.shop.owner_id != request.user.id:
            return Response({"detail": "Not your shop quote."}, status=status.HTTP_403_FORBIDDEN)
        if quote.status != ShopQuoteStatus.ACCEPTED:
            return Response(
                {"detail": "Only accepted quotes can be turned into jobs."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qr = quote.quote_request
        if qr.status != QuoteStatus.ACCEPTED:
            return Response(
                {"detail": "Quote request must be accepted first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if quote.production_orders.exists():
            return Response(
                {"detail": "Job already created from this quote."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = request.data.copy() if request.data else {}
        data["shop_quote"] = quote.id
        serializer = ProductionOrderWriteSerializer(
            data=data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        job = serializer.save()
        return Response(
            ProductionOrderSerializer(job).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# D. Attachments — nested under quote-requests and sent-quotes
# ---------------------------------------------------------------------------


class QuoteRequestAttachmentViewSet(viewsets.ModelViewSet):
    """
    GET/POST /quote-requests/{id}/attachments/
    DELETE /quote-requests/{id}/attachments/{pk}/

    Customer: add/delete when draft; list always.
    Shop: list only (via quote_request id from incoming-requests).
    """

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "delete"]

    def get_quote_request(self):
        return get_object_or_404(QuoteRequest, pk=self.kwargs["quote_request_pk"])

    def _check_access(self):
        qr = self.get_quote_request()
        user = self.request.user
        is_buyer = qr.created_by_id == user.id
        is_seller = qr.shop_id and qr.shop.owner_id == user.id
        if user.is_staff:
            return qr, True, True  # qr, can_write, can_read
        if is_buyer:
            return qr, qr.status == QuoteStatus.DRAFT, True
        if is_seller:
            return qr, False, True
        return qr, False, False

    def get_queryset(self):
        qr, _, can_read = self._check_access()
        if not can_read:
            return QuoteRequestAttachment.objects.none()
        return QuoteRequestAttachment.objects.filter(quote_request=qr)

    def get_serializer_class(self):
        if self.action == "create":
            return QuoteRequestAttachmentUploadSerializer
        return QuoteRequestAttachmentSerializer

    def list(self, request, *args, **kwargs):
        _, _, can_read = self._check_access()
        if not can_read:
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        qr, can_write, _ = self._check_access()
        if not can_write:
            return Response(
                {"detail": "Only the customer can add attachments, and only when the request is in draft."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        att = serializer.save(quote_request=qr)
        return Response(
            QuoteRequestAttachmentSerializer(att).data,
            status=status.HTTP_201_CREATED,
        )

    def destroy(self, request, *args, **kwargs):
        qr, can_write, _ = self._check_access()
        if not can_write:
            return Response(
                {"detail": "Only the customer can remove attachments, and only when the request is in draft."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)


class ShopQuoteAttachmentViewSet(viewsets.ModelViewSet):
    """
    GET/POST /sent-quotes/{id}/attachments/
    DELETE /sent-quotes/{id}/attachments/{pk}/

    Shop owner only. Ownership enforced via get_queryset and perform_create.
    """

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "delete"]

    def get_queryset(self):
        shop_quote_pk = self.kwargs["shop_quote_pk"]
        user = self.request.user
        qs = ShopQuoteAttachment.objects.filter(shop_quote_id=shop_quote_pk)
        if not user.is_staff:
            qs = qs.filter(shop_quote__shop__owner=user)
        return qs

    def get_serializer_class(self):
        if self.action == "create":
            return ShopQuoteAttachmentUploadSerializer
        return ShopQuoteAttachmentSerializer

    def perform_create(self, serializer):
        shop_quote = get_object_or_404(
            ShopQuote.objects.filter(shop__owner=self.request.user)
            if not self.request.user.is_staff
            else ShopQuote.objects.all(),
            pk=self.kwargs["shop_quote_pk"],
        )
        serializer.save(shop_quote=shop_quote)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            ShopQuoteAttachmentSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )
