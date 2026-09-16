"""Views for quote models with buyer/seller permissions."""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from core.permissions import (
    IsBuyerOrSeller,
    IsBuyerForDraftQuote,
    CanSubmitQuoteRequest,
    CanPriceOrLockQuote,
)
from .models import QuoteRequest, QuoteItem
from .serializers import QuoteRequestSerializer, QuoteItemSerializer


class QuoteRequestViewSet(viewsets.ModelViewSet):
    """
    Quote requests - buyer creates, manages DRAFT, submits; seller prices/locks.
    """
    queryset = QuoteRequest.objects.all()
    serializer_class = QuoteRequestSerializer
    permission_classes = [IsBuyerOrSeller]

    def get_queryset(self):
        qs = QuoteRequest.objects.for_buyer_or_seller(self.request.user)
        shop_pk = self.kwargs.get('shop_pk')
        if shop_pk:
            qs = qs.for_shop(shop_pk)
        return qs

    def perform_create(self, serializer):
        serializer.save(shop_id=self.kwargs['shop_pk'], buyer=self.request.user)

    def get_permissions(self):
        if self.action == 'submit':
            return [CanSubmitQuoteRequest()]
        if self.action in ['price', 'lock']:
            return [CanPriceOrLockQuote()]
        return [IsBuyerOrSeller()]

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """Buyer submits own DRAFT quote (status -> SUBMITTED)."""
        quote = self.get_object()
        if quote.buyer_id != request.user.pk:
            return Response({'detail': 'Not your quote.'}, status=status.HTTP_403_FORBIDDEN)
        if quote.status != QuoteRequest.DRAFT:
            return Response({'detail': 'Only draft quotes can be submitted.'}, status=status.HTTP_400_BAD_REQUEST)
        quote.status = QuoteRequest.SUBMITTED
        quote.save(update_fields=['status', 'updated_at'])
        return Response(QuoteRequestSerializer(quote).data)

    @action(detail=True, methods=['post'])
    def price(self, request, pk=None):
        """Seller prices a submitted quote (status -> PRICED)."""
        quote = self.get_object()
        if quote.status != QuoteRequest.SUBMITTED:
            return Response({'detail': 'Only submitted quotes can be priced.'}, status=status.HTTP_400_BAD_REQUEST)
        quote.status = QuoteRequest.PRICED
        quote.save(update_fields=['status', 'updated_at'])
        return Response(QuoteRequestSerializer(quote).data)

    @action(detail=True, methods=['post'])
    def lock(self, request, pk=None):
        """Seller locks a priced quote (status -> LOCKED)."""
        quote = self.get_object()
        if quote.status != QuoteRequest.PRICED:
            return Response({'detail': 'Only priced quotes can be locked.'}, status=status.HTTP_400_BAD_REQUEST)
        quote.status = QuoteRequest.LOCKED
        quote.save(update_fields=['status', 'updated_at'])
        return Response(QuoteRequestSerializer(quote).data)


class QuoteItemViewSet(viewsets.ModelViewSet):
    """
    Quote items - buyer add/update/remove on own DRAFT only.
    """
    queryset = QuoteItem.objects.all()
    serializer_class = QuoteItemSerializer
    permission_classes = [IsBuyerOrSeller, IsBuyerForDraftQuote]

    def get_queryset(self):
        qs = QuoteItem.objects.for_buyer_or_seller(self.request.user)
        quote_request_pk = self.kwargs.get('quote_request_pk')
        if quote_request_pk:
            qs = qs.filter(quote_request_id=quote_request_pk)
        return qs

    def get_quote_request(self):
        return QuoteRequest.objects.get(pk=self.kwargs['quote_request_pk'])

    def perform_create(self, serializer):
        quote = self.get_quote_request()
        if quote.buyer_id != self.request.user.pk or quote.status != QuoteRequest.DRAFT:
            raise PermissionError('Can only add items to own draft quote.')
        serializer.save(quote_request=quote)

    def perform_update(self, serializer):
        quote = self.get_object().quote_request
        if quote.buyer_id != self.request.user.pk or quote.status != QuoteRequest.DRAFT:
            raise PermissionError('Can only update items on own draft quote.')
        serializer.save()

    def perform_destroy(self, instance):
        quote = instance.quote_request
        if quote.buyer_id != self.request.user.pk or quote.status != QuoteRequest.DRAFT:
            raise PermissionError('Can only remove items from own draft quote.')
        instance.delete()
