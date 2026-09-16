from rest_framework import serializers


def _money(value):
    return str(value) if value is not None else None


class PaymentClientSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {
            "id": instance.id,
            "status": instance.status,
            "amount": _money(instance.amount),
            "currency": instance.currency,
            "provider": instance.provider,
            "account_reference": instance.account_reference,
            "mpesa_receipt_number": instance.mpesa_receipt_number if instance.status == instance.STATUS_PAID else None,
            "confirmed_at": instance.confirmed_at,
        }


class PaymentBrokerSerializer(serializers.Serializer):
    def to_representation(self, instance):
        payer = getattr(instance, "payer", None)
        return {
            **PaymentClientSerializer(instance).data,
            "payer_summary": {
                "id": instance.payer_id,
                "name": getattr(payer, "name", "") if payer else "",
                "email": getattr(payer, "email", "") if payer else "",
            },
            "payment_timeline": {
                "created_at": instance.created_at,
                "confirmed_at": instance.confirmed_at,
            },
            "related_split_summary": {
                "quote": instance.quote_id,
                "managed_job": instance.managed_job_id,
            },
            "method": instance.method,
            "checkout_request_id": instance.checkout_request_id,
            "merchant_request_id": instance.merchant_request_id,
        }


class PaymentShopSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {}


class PaymentAdminSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {field.name: getattr(instance, field.name) for field in instance._meta.fields}


PaymentManagerSerializer = PaymentBrokerSerializer
