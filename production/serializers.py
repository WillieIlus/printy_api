"""Production serializers for shop-side ProductionOrder fulfillment."""
from rest_framework import serializers

from .models import ProductionOrder


class ProductionOrderListSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionOrder
        fields = [
            "id",
            "shop",
            "quote",
            "order_number",
            "title",
            "quantity",
            "status",
            "delivery_status",
            "due_date",
            "completed_at",
            "delivered_at",
            "created_at",
        ]


class ProductionOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionOrder
        fields = [
            "id",
            "shop",
            "quote",
            "order_number",
            "title",
            "quantity",
            "status",
            "delivery_status",
            "delivered_at",
            "due_date",
            "completed_at",
            "notes",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_by", "created_at", "updated_at"]


class ProductionOrderWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductionOrder
        fields = [
            "shop",
            "quote",
            "order_number",
            "title",
            "quantity",
            "status",
            "delivery_status",
            "delivered_at",
            "due_date",
            "completed_at",
            "notes",
        ]
