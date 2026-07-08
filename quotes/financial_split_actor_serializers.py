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
            "manager_markup": _money(getattr(instance, "manager_markup", instance.gross_margin)),
            "production_fee_component": _money(getattr(instance, "production_fee_component", instance.printer_side_fee)),
            "markup_fee_component": _money(getattr(instance, "markup_fee_component", instance.broker_margin_fee)),
            "printy_fee": _money(instance.printy_fee),
            "shop_payout": _money(instance.shop_payout),
            "manager_payout": _money(getattr(instance, "manager_payout", instance.broker_payout)),
            "client_total": _money(instance.client_total),
            "currency": getattr(instance, "currency", "KES"),
            "policy_version": getattr(instance, "applied_policy_version", ""),
            "pricing_tier": getattr(instance, "pricing_tier", ""),
            "locked": getattr(instance, "locked", False),
            # Backwards-compatible aliases for existing backend consumers.
            "broker_client_price": _money(instance.broker_client_price),
            "gross_margin": _money(instance.gross_margin),
            "printer_side_fee": _money(instance.printer_side_fee),
            "broker_margin_fee": _money(instance.broker_margin_fee),
            "broker_payout": _money(instance.broker_payout),
            "max_allowed_client_price": _money(instance.max_allowed_client_price),
            "applied_markup_multiple": _money(instance.applied_markup_multiple),
        }


class QuoteFinancialSplitShopSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {
            "id": instance.id,
            "production_cost": _money(instance.production_cost),
            "shop_payout": _money(instance.shop_payout),
            "currency": getattr(instance, "currency", "KES"),
        }


class QuoteFinancialSplitAdminSerializer(QuoteFinancialSplitBrokerSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["policy_used"] = instance.policy_used_id
        data["production_option"] = instance.production_option_id
        data["quote"] = instance.quote_id
        data["calculated_at"] = instance.calculated_at
        return data