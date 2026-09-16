"""Serializers for quote models."""
from rest_framework import serializers

from .models import (
    EmailOutbox,
    PendingArtworkUpload,
    QuoteFinancialSplit,
    QuoteItem,
    QuoteRequest,
)


class QuoteItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteItem
        fields = ['id', 'quote_request', 'description', 'quantity', 'unit_price', 'created_at', 'updated_at']
        read_only_fields = ['quote_request', 'unit_price']


class QuoteRequestSerializer(serializers.ModelSerializer):
    items = QuoteItemSerializer(many=True, read_only=True)

    class Meta:
        model = QuoteRequest
        fields = ['id', 'shop', 'buyer', 'status', 'items', 'created_at', 'updated_at']
        read_only_fields = ['shop', 'buyer', 'status', 'created_at', 'updated_at']


class QuoteFinancialSplitReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuoteFinancialSplit
        fields = "__all__"


class EmailOutboxReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmailOutbox
        fields = "__all__"


class PendingArtworkUploadReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = PendingArtworkUpload
        fields = "__all__"
