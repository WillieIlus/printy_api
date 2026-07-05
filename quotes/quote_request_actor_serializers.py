from rest_framework import serializers

from api.visibility import project_request_snapshot_for_client


def _first_item(instance):
    try:
        return instance.items.order_by("id").first()
    except Exception:
        return None


def _item_specs(item):
    if item is None:
        return {}
    return {
        "title": item.title,
        "quantity": item.quantity,
        "spec_text": item.spec_text,
        "paper": item.paper_id,
        "size": f"{item.chosen_width_mm}x{item.chosen_height_mm}mm"
        if item.chosen_width_mm and item.chosen_height_mm
        else "",
    }


class QuoteRequestClientSerializer(serializers.Serializer):
    def to_representation(self, instance):
        item = _first_item(instance)
        public_snapshot = project_request_snapshot_for_client(instance.request_snapshot)
        return {
            "id": instance.id,
            "status": instance.status,
            "product": getattr(item, "product_id", None),
            "quantity": getattr(item, "quantity", None),
            "specs": _item_specs(item),
            "uploaded_file": None,
            "public_draft_snapshot": public_snapshot,
            "created_at": instance.created_at,
            "updated_at": instance.updated_at,
            "current_quote_summary": None,
        }


class QuoteRequestBrokerSerializer(serializers.Serializer):
    def to_representation(self, instance):
        data = QuoteRequestClientSerializer(instance).data
        data.update(
            {
                "internal_sourcing_snapshot": instance.request_snapshot or {},
                "production_options": [
                    option.id for option in instance.production_options.all()
                ],
                "client": {
                    "id": instance.on_behalf_of_id or instance.created_by_id,
                    "name": instance.customer_name,
                    "email": instance.customer_email,
                    "phone": instance.customer_phone,
                },
                "internal_notes": instance.notes,
            }
        )
        return data


class QuoteRequestShopSerializer(serializers.Serializer):
    def to_representation(self, instance):
        item = _first_item(instance)
        return {
            "id": instance.id,
            "product": getattr(item, "product_id", None),
            "quantity": getattr(item, "quantity", None),
            "specs": _item_specs(item),
            "uploaded_file": None,
            "deadline": None,
        }


class QuoteRequestAdminSerializer(serializers.Serializer):
    def to_representation(self, instance):
        return {field.name: getattr(instance, field.name) for field in instance._meta.fields}
