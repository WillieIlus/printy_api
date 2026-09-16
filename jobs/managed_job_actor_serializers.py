from rest_framework import serializers

from api.visibility import SHOP_ACTOR, strip_forbidden_keys


def _money(value):
    return str(value) if value is not None else None


def _current_event(instance):
    event = instance.events.order_by("-created_at", "-id").first()
    if not event:
        return None
    return {"id": event.id, "event_type": event.event_type, "summary": event.summary, "created_at": event.created_at}


def _timeline(instance, *, own_only=False):
    events = instance.events.order_by("-created_at", "-id")
    if own_only:
        events = events.exclude(event_type__icontains="client")
    return [
        {"id": event.id, "event_type": event.event_type, "summary": event.summary, "created_at": event.created_at}
        for event in events[:50]
    ]


class ManagedJobClientSerializer(serializers.Serializer):
    def to_representation(self, instance):
        from quotes.quote_actor_serializers import QuoteClientSerializer

        return {
            "id": instance.id,
            "status": instance.status,
            "tracking_status": instance.status,
            "expected_delivery": instance.requested_delivery_time or instance.ready_at,
            "current_status_event": _current_event(instance),
            "quote": QuoteClientSerializer(instance.source_quote).data if instance.source_quote_id else None,
        }


class ManagedJobBrokerSerializer(serializers.Serializer):
    def to_representation(self, instance):
        from quotes.financial_split_actor_serializers import QuoteFinancialSplitBrokerSerializer

        split = getattr(getattr(instance, "source_quote", None), "financial_split", None)
        shop = getattr(instance, "assigned_shop", None)
        return {
            **ManagedJobClientSerializer(instance).data,
            "assignment": instance.assignment_status,
            "financial_split": QuoteFinancialSplitBrokerSerializer(split).data if split else None,
            "sourced_shop_identity": {
                "id": instance.assigned_shop_id,
                "name": getattr(shop, "name", "") if shop else "",
            },
            "status_timeline": _timeline(instance),
        }


class ManagedJobShopSerializer(serializers.Serializer):
    def to_representation(self, instance):
        assignment = instance.assignments.filter(reassigned_from__isnull=True).first()
        split = getattr(getattr(instance, "source_quote", None), "financial_split", None)
        shop_payout = getattr(assignment, "shop_payout", None) or getattr(split, "shop_payout", None)
        return {
            "id": instance.id,
            "status": instance.status,
            "assigned_specs": strip_forbidden_keys(instance.operational_snapshot, SHOP_ACTOR),
            "files": [],
            "deadline": getattr(assignment, "due_at", None) or instance.requested_deadline,
            "shop_payout": _money(shop_payout),
            "production_notes": getattr(assignment, "assignment_notes", ""),
            "status_timeline": _timeline(instance, own_only=True),
        }


class ManagedJobAdminSerializer(serializers.Serializer):
    def to_representation(self, instance):
        data = {}
        for field in instance._meta.fields:
            value = getattr(instance, field.name)
            data[field.name] = getattr(value, "pk", value)
        data["status_timeline"] = _timeline(instance)
        return data
