from rest_framework import serializers

from api.visibility import SHOP_ACTOR, strip_forbidden_keys


def _money(value):
    return str(value) if value is not None else None


def _summary_lines(quote):
    snapshot = quote.response_snapshot if isinstance(quote.response_snapshot, dict) else {}
    lines = snapshot.get("summary_lines")
    if isinstance(lines, list):
        return lines
    note = quote.note or snapshot.get("note")
    return [note] if note else []


def _client_total(quote):
    split = getattr(quote, "financial_split", None)
    if split is not None:
        return split.client_total
    return getattr(quote, "client_total", None) or quote.total


class QuoteClientSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {
            "id": instance.id,
            "reference": getattr(instance, "quote_reference", "") or instance.id,
            "status": instance.status,
            "client_total": _money(_client_total(instance)),
            "deadline": instance.estimated_ready_at or instance.expires_at,
            "accepted_at": instance.accepted_at,
            "created_at": instance.created_at,
            "quote_request": instance.quote_request_id,
            "summary_lines": _summary_lines(instance),
        }


class QuoteBrokerSerializer(serializers.Serializer):
    def to_representation(self, instance):
        from quotes.financial_split_actor_serializers import QuoteFinancialSplitBrokerSerializer

        split = getattr(instance, "financial_split", None)
        shop = getattr(instance, "shop", None)
        return {
            **QuoteClientSerializer(instance).data,
            "production_option": instance.production_option_id,
            "financial_split": QuoteFinancialSplitBrokerSerializer(split).data if split else None,
            "shop_identity": {
                "id": instance.shop_id,
                "name": getattr(shop, "name", "") if shop else "",
            },
            "updated_at": instance.updated_at,
            "sent_at": instance.sent_at,
        }


class QuoteShopSerializer(serializers.Serializer):
    def to_representation(self, instance):
        from quotes.financial_split_actor_serializers import QuoteFinancialSplitShopSerializer

        split = getattr(instance, "financial_split", None)
        snapshot = instance.response_snapshot if isinstance(instance.response_snapshot, dict) else {}
        return {
            "id": instance.id,
            "status": instance.status,
            "accepted_at": instance.accepted_at,
            "shop_payout": _money(split.shop_payout if split else None),
            "deadline": instance.estimated_ready_at or instance.expires_at,
            "specs": strip_forbidden_keys(snapshot.get("specs") or snapshot.get("production_preview"), SHOP_ACTOR),
            "files": [],
            "production_notes": instance.note,
            "financial_split": QuoteFinancialSplitShopSerializer(split).data if split else None,
        }


class QuoteAdminSerializer(serializers.Serializer):
    def to_representation(self, instance):
        from quotes.financial_split_actor_serializers import QuoteFinancialSplitAdminSerializer

        split = getattr(instance, "financial_split", None)
        data = {field.name: getattr(instance, field.name) for field in instance._meta.fields}
        data["financial_split"] = QuoteFinancialSplitAdminSerializer(split).data if split else None
        return data
