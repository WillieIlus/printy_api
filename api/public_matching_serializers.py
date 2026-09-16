from rest_framework import serializers
from decimal import Decimal
from api.size_utils import normalize_size_payload, validate_size_selection


class PublicFinishingSelectionSerializer(serializers.Serializer):
    finishing_rate_id = serializers.IntegerField(required=False)
    finishing_id = serializers.IntegerField(required=False)
    slug = serializers.CharField(required=False, allow_blank=False)
    selected_side = serializers.ChoiceField(choices=["front", "back", "both"], required=False, default="both")


class PublicCalculatorPayloadSerializer(serializers.Serializer):
    calculator_mode = serializers.CharField(required=False, allow_blank=True, default="marketplace")
    job_type = serializers.CharField(required=False, allow_blank=True, default="")
    product_type = serializers.CharField(required=False, allow_blank=True, default="")
    product_family = serializers.ChoiceField(
        choices=["flat", "booklet", "large_format"],
        required=False,
        default="flat",
    )
    shop_scope = serializers.ChoiceField(
        choices=["marketplace", "single_shop", "admin", "calculator_draft", "tweak"],
        required=False,
        default="marketplace",
    )
    pricing_mode = serializers.ChoiceField(choices=["catalog", "custom"], required=False, default="custom")
    product_pricing_mode = serializers.ChoiceField(
        choices=["SHEET", "LARGE_FORMAT"],
        required=False,
        default="SHEET",
    )
    product_id = serializers.IntegerField(required=False, allow_null=True, help_text="Specific product ID for catalog matching.")
    product_slug = serializers.CharField(required=False, allow_blank=True, default="", help_text="Specific product slug for catalog matching.")
    template_id = serializers.IntegerField(required=False, allow_null=True, help_text="Alias for product_id.")
    quantity = serializers.IntegerField(min_value=1, default=1, help_text="Job quantity.")
    size_mode = serializers.ChoiceField(choices=["standard", "custom"], required=False, default="custom")
    size_label = serializers.CharField(required=False, allow_blank=True, default="", help_text="Human-readable size label (e.g. 'A4').")
    input_unit = serializers.ChoiceField(choices=["mm", "cm", "m", "in"], required=False, default="mm")
    width_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    height_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1, help_text="Finished width in mm.")
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1, help_text="Finished height in mm.")
    normalized_size = serializers.CharField(required=False, allow_blank=True, default="")
    print_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="SIMPLEX")
    apply_duplex_surcharge = serializers.BooleanField(required=False, allow_null=True, default=None)
    colour_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    paper_id = serializers.IntegerField(required=False, allow_null=True)
    paper_preference = serializers.CharField(required=False, allow_blank=True, default="")
    material_id = serializers.IntegerField(required=False, allow_null=True)
    material_preference = serializers.CharField(required=False, allow_blank=True, default="")
    material_type = serializers.CharField(required=False, allow_blank=True, default="")
    sheet_size = serializers.CharField(required=False, allow_blank=True, default="")
    paper_gsm = serializers.IntegerField(required=False, allow_null=True)
    paper_type = serializers.CharField(required=False, allow_blank=True, default="")
    finishing_ids = serializers.ListField(child=serializers.IntegerField(), required=False, default=list)
    finishing_slugs = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    finishings = PublicFinishingSelectionSerializer(many=True, required=False, default=list)
    turnaround_days = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    turnaround_hours = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    turnaround_mode = serializers.ChoiceField(choices=["standard", "rush"], required=False, default="standard")
    urgency_type = serializers.ChoiceField(
        choices=["standard", "same_day", "express", "after_hours", "emergency"],
        required=False,
        default="standard",
    )
    requested_deadline = serializers.DateTimeField(required=False, allow_null=True)
    requested_delivery_time = serializers.DateTimeField(required=False, allow_null=True)
    custom_title = serializers.CharField(required=False, allow_blank=True, default="")
    custom_brief = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="", source="custom_brief")
    fixed_shop_slug = serializers.CharField(required=False, allow_blank=True, default="")
    location_slug = serializers.CharField(required=False, allow_blank=True, default="")
    lat = serializers.FloatField(required=False, allow_null=True)
    lng = serializers.FloatField(required=False, allow_null=True)
    radius_km = serializers.FloatField(required=False, allow_null=True, min_value=0.1, max_value=500)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            if "product_type" in data and not data.get("job_type"):
                data["job_type"] = data["product_type"]
            if "job_type" in data and not data.get("product_family"):
                jt = str(data["job_type"]).lower()
                if "booklet" in jt:
                    data["product_family"] = "booklet"
                elif "large" in jt or "banner" in jt or "vinyl" in jt:
                    data["product_family"] = "large_format"
                    data["product_pricing_mode"] = "LARGE_FORMAT"
                else:
                    data["product_family"] = "flat"
            
            if "notes" in data and "custom_brief" not in data:
                data["custom_brief"] = data["notes"]
            
            if "sides" in data and "print_sides" not in data:
                data["print_sides"] = data["sides"]
            
            if "color_mode" in data and "colour_mode" not in data:
                data["colour_mode"] = data["color_mode"]

            normalized = normalize_size_payload(
                data,
                legacy_width_keys=("finished_width_mm",),
                legacy_height_keys=("finished_height_mm",),
            )

            legacy_path = normalized.get("mode")
            legacy_product_mode = normalized.get("pricing_mode")
            if legacy_path in {"catalog", "custom"}:
                normalized.setdefault("pricing_mode", legacy_path)
            if legacy_product_mode in {"SHEET", "LARGE_FORMAT"}:
                normalized["product_pricing_mode"] = legacy_product_mode
            if normalized.get("product_family") not in {"flat", "booklet", "large_format"}:
                normalized["product_family"] = "large_format" if normalized.get("product_pricing_mode") == "LARGE_FORMAT" else "flat"

            if "sides" in normalized and "print_sides" not in normalized:
                normalized["print_sides"] = normalized["sides"]
            if "color_mode" in normalized and "colour_mode" not in normalized:
                normalized["colour_mode"] = normalized["color_mode"]
            if normalized.get("template_id") and not normalized.get("product_id"):
                normalized["product_id"] = normalized["template_id"]
            
            if normalized.get("paper_preference") and not normalized.get("paper_type"):
                normalized["paper_type"] = normalized["paper_preference"]
            if normalized.get("material_preference") and not normalized.get("material_type"):
                normalized["material_type"] = normalized["material_preference"]

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
        attrs = validate_size_selection(attrs)

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
    # Booklet-specific selections
    cover_paper_id = serializers.IntegerField(required=False)
    cover_paper_label = serializers.CharField(required=False)
    insert_paper_id = serializers.IntegerField(required=False)
    insert_paper_label = serializers.CharField(required=False)
    binding_rate_id = serializers.IntegerField(required=False)
    binding_rate_label = serializers.CharField(required=False)


class PublicBookletMatchPayloadSerializer(serializers.Serializer):
    """Job-first payload for booklet marketplace matching — no shop required."""
    product_family = serializers.ChoiceField(choices=["booklet"], required=False, default="booklet")
    quantity = serializers.IntegerField(min_value=1, default=100)
    total_pages = serializers.IntegerField(min_value=4, default=12)
    binding_type = serializers.ChoiceField(
        choices=["saddle_stitch", "perfect_bind", "wire_o"], default="saddle_stitch"
    )
    cover_paper_type = serializers.CharField(required=False, allow_blank=True, default="")
    cover_paper_gsm = serializers.IntegerField(required=False, allow_null=True)
    insert_paper_type = serializers.CharField(required=False, allow_blank=True, default="")
    insert_paper_gsm = serializers.IntegerField(required=False, allow_null=True)
    sheet_size = serializers.CharField(required=False, allow_blank=True, default="")
    cover_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="DUPLEX")
    insert_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="DUPLEX")
    cover_color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    insert_color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    cover_lamination_mode = serializers.ChoiceField(choices=["none", "front", "both"], default="none")
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    turnaround_hours = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    lat = serializers.FloatField(required=False, allow_null=True)
    lng = serializers.FloatField(required=False, allow_null=True)
    radius_km = serializers.FloatField(required=False, allow_null=True, min_value=0.1, max_value=500)


class ProductionPreviewSerializer(serializers.Serializer):
    pieces_per_sheet = serializers.IntegerField(required=False, allow_null=True)
    sheets_required = serializers.IntegerField(required=False, allow_null=True)
    parent_sheet = serializers.CharField(required=False, allow_null=True)
    imposition_label = serializers.CharField(required=False, allow_null=True)
    size_label = serializers.CharField(required=False, allow_null=True)
    quantity = serializers.IntegerField(required=False, allow_null=True)
    cutting_required = serializers.BooleanField(required=False, allow_null=True)
    selected_finishings = serializers.ListField(child=serializers.CharField(), default=list)
    suggested_finishings = serializers.ListField(child=serializers.CharField(), default=list)
    warnings = serializers.ListField(child=serializers.CharField(), default=list)
    roll_width_m = serializers.FloatField(required=False, allow_null=True)
    roll_width_mm = serializers.FloatField(required=False, allow_null=True)
    items_per_row = serializers.IntegerField(required=False, allow_null=True)
    rows = serializers.IntegerField(required=False, allow_null=True)
    used_length_m = serializers.FloatField(required=False, allow_null=True)
    orientation = serializers.CharField(required=False, allow_null=True)
    input_size_m = serializers.JSONField(required=False, allow_null=True)
    charged_area_m2 = serializers.FloatField(required=False, allow_null=True)
    printed_area_m2 = serializers.FloatField(required=False, allow_null=True)
    waste_area_m2 = serializers.FloatField(required=False, allow_null=True)
    overlap_area_m2 = serializers.FloatField(required=False, allow_null=True)
    tiling = serializers.JSONField(required=False, allow_null=True)


class PricingBreakdownLineSerializer(serializers.Serializer):
    label = serializers.CharField()
    amount = serializers.CharField(required=False, allow_null=True)
    formula = serializers.CharField(required=False, allow_null=True)


class PricingBreakdownSerializer(serializers.Serializer):
    currency = serializers.CharField(default="KES")
    base_price = serializers.FloatField(required=False, allow_null=True)
    client_price = serializers.FloatField(required=False, allow_null=True)
    paper_price = serializers.FloatField(required=False, allow_null=True)
    print_price_front = serializers.FloatField(required=False, allow_null=True)
    print_price_back = serializers.FloatField(required=False, allow_null=True)
    total_per_sheet = serializers.FloatField(required=False, allow_null=True)
    estimated_total = serializers.FloatField(required=False, allow_null=True)
    price_range = serializers.JSONField(required=False, allow_null=True)
    formula = serializers.CharField(required=False, allow_null=True)
    method = serializers.CharField(required=False, allow_null=True)
    rate = serializers.FloatField(required=False, allow_null=True)
    charged_area_m2 = serializers.FloatField(required=False, allow_null=True)
    charged_length_m = serializers.FloatField(required=False, allow_null=True)
    minimum_charge = serializers.FloatField(required=False, allow_null=True)
    minimum_charge_applied = serializers.BooleanField(required=False, allow_null=True)
    lines = PricingBreakdownLineSerializer(many=True, default=list)


class PublicMatchShopSerializer(serializers.Serializer):
    option_label = serializers.CharField()
    can_produce = serializers.BooleanField(required=False)
    currency = serializers.CharField(required=False, allow_blank=True)
    can_calculate = serializers.BooleanField(required=False)
    can_price_now = serializers.BooleanField(required=False)
    can_send_quote_request = serializers.BooleanField(required=False)
    reason = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    summary = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    missing_fields = serializers.ListField(child=serializers.CharField(), default=list)
    missing_specs = serializers.ListField(child=serializers.CharField(), default=list, source="missing_fields")
    match_type = serializers.CharField(required=False)
    price_confidence = serializers.CharField(required=False, allow_null=True)
    quote_basis = serializers.CharField(required=False)
    preview = serializers.JSONField(required=False, allow_null=True)
    turnaround_hours = serializers.IntegerField(required=False, allow_null=True)
    estimated_working_hours = serializers.IntegerField(required=False, allow_null=True)
    estimated_ready_at = serializers.DateTimeField(required=False, allow_null=True)
    human_ready_text = serializers.CharField(required=False, allow_blank=True)
    turnaround_label = serializers.CharField(required=False, allow_blank=True)
    selection = PublicPreviewSelectionSerializer(required=False)
    exact_or_estimated = serializers.BooleanField(required=False, default=False)
    product_match = serializers.JSONField(required=False, allow_null=True)
    matched_specs = serializers.ListField(child=serializers.CharField(), default=list)
    needs_confirmation = serializers.ListField(child=serializers.CharField(), default=list)
    closest_alternatives = serializers.ListField(child=serializers.JSONField(), default=list)
    alternative_suggestions = serializers.ListField(child=serializers.JSONField(), default=list, source="closest_alternatives")
    price_range = serializers.JSONField(required=False, allow_null=True)
    production_preview = ProductionPreviewSerializer(required=False, allow_null=True)
    pricing_breakdown = serializers.SerializerMethodField()

    def get_pricing_breakdown(self, obj):
        return None


class PublicCalculatorResponseSerializer(serializers.Serializer):
    mode = serializers.CharField()
    can_calculate = serializers.BooleanField(required=False, default=True)
    product_type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    price_mode = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    total = serializers.CharField(required=False, allow_null=True)
    matches_count = serializers.IntegerField()
    min_price = serializers.CharField(required=False, allow_null=True)
    max_price = serializers.CharField(required=False, allow_null=True)
    estimate_min = serializers.CharField(required=False, allow_null=True)
    estimate_max = serializers.CharField(required=False, allow_null=True)
    display_price_text = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    display_mode = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    confidence_label = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    source_label = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    currency = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    matches = PublicMatchShopSerializer(many=True)
    shops = PublicMatchShopSerializer(many=True)
    selected_shops = PublicMatchShopSerializer(many=True)
    shop_matches = PublicMatchShopSerializer(many=True, required=False, source="matches")
    fixed_shop_preview = PublicMatchShopSerializer(required=False, allow_null=True)
    production_preview = ProductionPreviewSerializer(required=False, allow_null=True)
    pricing_breakdown = serializers.SerializerMethodField()
    missing_requirements = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    missing_fields = serializers.ListField(child=serializers.CharField(), required=False, default=list, source="missing_requirements")
    unsupported_reasons = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    summary = serializers.CharField()
    suggestions = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    exact_or_estimated = serializers.BooleanField(required=False, default=False)
    warnings = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    visibility = serializers.JSONField(required=False, allow_null=True)

    def get_pricing_breakdown(self, obj):
        return None
