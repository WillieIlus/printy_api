from rest_framework import serializers

from api.services.actor_serializer import select_actor_serializer
from payments.models import MpesaSTKRequest, Payment


class PaymentSerializer(serializers.Serializer):
    def to_representation(self, instance):
        request = self.context.get("request")
        serializer_class = select_actor_serializer("payment", getattr(request, "user", None), default=None)
        if serializer_class is None:
            from payments.payment_actor_serializers import PaymentClientSerializer

            serializer_class = PaymentClientSerializer
        return serializer_class(instance, context=self.context).data


class MpesaSTKRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = MpesaSTKRequest
        fields = [
            "id",
            "payment",
            "phone_number",
            "amount",
            "status",
            "account_reference",
            "checkout_request_id",
            "merchant_request_id",
            "requested_at",
            "created_at",
            "updated_at",
        ]
