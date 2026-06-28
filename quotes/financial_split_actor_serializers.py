from rest_framework import serializers


def _money(value):
    return str(value) if value is not None else None


class QuoteFinancialSplitClientSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {}


class QuoteFinancialSplitBrokerSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {
            "id": instance.id,
            "production_cost": _money(instance.production_cost),
            "broker_client_price": _money(instance.broker_client_price),
            "gross_margin": _money(instance.gross_margin),
            "printer_side_fee": _money(instance.printer_side_fee),
            "broker_margin_fee": _money(instance.broker_margin_fee),
            "printy_fee": _money(instance.printy_fee),
            "shop_payout": _money(instance.shop_payout),
            "broker_payout": _money(instance.broker_payout),
            "client_total": _money(instance.client_total),
            "max_allowed_client_price": _money(instance.max_allowed_client_price),
            "applied_markup_multiple": _money(instance.applied_markup_multiple),
        }


class QuoteFinancialSplitShopSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {
            "id": instance.id,
            "shop_payout": _money(instance.shop_payout),
        }


class QuoteFinancialSplitAdminSerializer(QuoteFinancialSplitBrokerSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["policy_used"] = instance.policy_used_id
        data["production_option"] = instance.production_option_id
        data["quote"] = instance.quote_id
        data["calculated_at"] = instance.calculated_at
        return data
