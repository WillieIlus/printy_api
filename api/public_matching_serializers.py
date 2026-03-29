from rest_framework import serializers


class PublicFinishingSelectionSerializer(serializers.Serializer):
    finishing_rate_id = serializers.IntegerField(required=False)
    finishing_id = serializers.IntegerField(required=False)
    slug = serializers.CharField(required=False, allow_blank=False)
    selected_side = serializers.ChoiceField(choices=["front", "back", "both"], required=False, default="both")


class PublicCalculatorPayloadSerializer(serializers.Serializer):
    calculator_mode = serializers.CharField(required=False, allow_blank=True, default="marketplace")
    shop_scope = serializers.ChoiceField(
        choices=["marketplace", "single_shop", "admin", "quote_draft", "tweak"],
        required=False,
        default="marketplace",
    )
    pricing_mode = serializers.ChoiceField(choices=["catalog", "custom"], required=False, default="custom")
    product_pricing_mode = serializers.ChoiceField(
        choices=["SHEET", "LARGE_FORMAT"],
        required=False,
        default="SHEET",
    )
    product_id = serializers.IntegerField(required=False, allow_null=True)
    template_id = serializers.IntegerField(required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1, default=1)
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    normalized_size = serializers.CharField(required=False, allow_blank=True, default="")
    print_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="SIMPLEX")
    colour_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    paper_id = serializers.IntegerField(required=False, allow_null=True)
    material_id = serializers.IntegerField(required=False, allow_null=True)
    sheet_size = serializers.CharField(required=False, allow_blank=True, default="")
    paper_gsm = serializers.IntegerField(required=False, allow_null=True)
    paper_type = serializers.CharField(required=False, allow_blank=True, default="")
    finishing_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    finishing_slugs = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    finishings = PublicFinishingSelectionSerializer(many=True, required=False, default=list)
    turnaround_days = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    custom_title = serializers.CharField(required=False, allow_blank=True, default="")
    custom_brief = serializers.CharField(required=False, allow_blank=True, default="")
    fixed_shop_slug = serializers.CharField(required=False, allow_blank=True, default="")
    lat = serializers.FloatField(required=False, allow_null=True)
    lng = serializers.FloatField(required=False, allow_null=True)
    radius_km = serializers.FloatField(required=False, allow_null=True, min_value=0.1, max_value=500)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            normalized = dict(data)

            legacy_path = normalized.get("mode")
            legacy_product_mode = normalized.get("pricing_mode")
            if legacy_path in {"catalog", "custom"}:
                normalized.setdefault("pricing_mode", legacy_path)
            if legacy_product_mode in {"SHEET", "LARGE_FORMAT"}:
                normalized["product_pricing_mode"] = legacy_product_mode

            if "finished_width_mm" in normalized and "width_mm" not in normalized:
                normalized["width_mm"] = normalized["finished_width_mm"]
            if "finished_height_mm" in normalized and "height_mm" not in normalized:
                normalized["height_mm"] = normalized["finished_height_mm"]
            if "sides" in normalized and "print_sides" not in normalized:
                normalized["print_sides"] = normalized["sides"]
            if "color_mode" in normalized and "colour_mode" not in normalized:
                normalized["colour_mode"] = normalized["color_mode"]
            if normalized.get("template_id") and not normalized.get("product_id"):
                normalized["product_id"] = normalized["template_id"]

            if "finishings" in normalized:
                finishings = normalized["finishings"]
                if isinstance(finishings, list) and finishings:
                    first_item = finishings[0]
                    if isinstance(first_item, str) and "finishing_slugs" not in normalized:
                        normalized["finishing_slugs"] = finishings
                    elif isinstance(first_item, int) and "finishing_ids" not in normalized:
                        normalized["finishing_ids"] = finishings

            return super().to_internal_value(normalized)
        return super().to_internal_value(data)

    def validate(self, attrs):
        attrs = super().validate(attrs)

        if attrs["pricing_mode"] == "catalog" and not attrs.get("product_id"):
            raise serializers.ValidationError({"product_id": ["product_id is required for catalog previews."]})

        if attrs["pricing_mode"] == "custom" and not attrs.get("custom_title") and not attrs.get("custom_brief"):
            attrs["custom_title"] = "Custom print job"

        finishing_ids = list(attrs.get("finishing_ids") or [])
        finishing_slugs = list(attrs.get("finishing_slugs") or [])
        finishing_selections = []
        for selection in attrs.get("finishings") or []:
            finishing_id = selection.get("finishing_rate_id") or selection.get("finishing_id")
            if finishing_id:
                finishing_ids.append(finishing_id)
            if selection.get("slug"):
                finishing_slugs.append(selection["slug"])
            finishing_selections.append(
                {
                    "finishing_id": finishing_id,
                    "slug": selection.get("slug"),
                    "selected_side": selection.get("selected_side", "both"),
                }
            )

        attrs["finishing_ids"] = list(dict.fromkeys(finishing_ids))
        attrs["finishing_slugs"] = list(dict.fromkeys(finishing_slugs))
        attrs["finishing_selections"] = finishing_selections
        return attrs


class PublicPreviewSelectionSerializer(serializers.Serializer):
    paper_id = serializers.IntegerField(required=False)
    paper_label = serializers.CharField(required=False)
    material_id = serializers.IntegerField(required=False)
    material_label = serializers.CharField(required=False)
    machine_id = serializers.IntegerField(required=False)
    machine_label = serializers.CharField(required=False)


class PublicMatchShopSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    slug = serializers.CharField()
    currency = serializers.CharField(required=False, allow_blank=True)
    can_calculate = serializers.BooleanField()
    reason = serializers.CharField()
    missing_fields = serializers.ListField(child=serializers.CharField(), default=list)
    similarity_score = serializers.FloatField(required=False)
    total = serializers.CharField(required=False, allow_null=True)
    preview = serializers.JSONField(required=False, allow_null=True)
    selection = PublicPreviewSelectionSerializer(required=False)
    exact_or_estimated = serializers.BooleanField(required=False, default=False)


class PublicCalculatorResponseSerializer(serializers.Serializer):
    mode = serializers.CharField()
    matches_count = serializers.IntegerField()
    min_price = serializers.CharField(required=False, allow_null=True)
    max_price = serializers.CharField(required=False, allow_null=True)
    currency = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    shops = PublicMatchShopSerializer(many=True)
    selected_shops = PublicMatchShopSerializer(many=True)
    fixed_shop_preview = PublicMatchShopSerializer(required=False, allow_null=True)
    missing_requirements = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    unsupported_reasons = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    summary = serializers.CharField()
    exact_or_estimated = serializers.BooleanField(required=False, default=False)
