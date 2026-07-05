from rest_framework import serializers
from decimal import Decimal
from django.contrib.auth import get_user_model

from catalog.models import Product
from inventory.models import Machine, Paper
from pricing.models import FinishingRate
from api.size_utils import normalize_size_payload, validate_size_selection
from api.visibility import (
    CLIENT_ACTOR,
    OPS_ACTOR,
    PARTNER_ACTOR,
    SHOP_ACTOR,
    project_client_counterparty_name,
    project_client_identity,
    resolve_actor,
    project_identity,
    project_quote_response_snapshot_for_client,
    project_request_snapshot_for_client,
    project_revised_pricing_snapshot_for_client,
    resolve_topology_mode_for_quote_request,
    strip_forbidden_keys,
)
from quotes.choices import CalculatorDraftContext, CalculatorDraftIntent, CalculatorDraftStatus, QuoteStatus, QuoteOfferStatus
from quotes.guardrails import validate_partner_markup_amount
from quotes.models import CalculatorDraft, ProductionOption, QuoteItem, QuoteRequest, QuoteRequestMessage, Quote
from quotes.request_brief import build_quote_request_whatsapp_handoff
from quotes.status_normalization import (
    denormalize_quote_response_status,
    normalize_calculator_draft_status,
    normalize_quote_request_status,
    normalize_quote_response_status,
    calculator_draft_status_label,
    quote_request_status_label,
    quote_response_status_label,
)
from services.pricing.mvp_rate_card import FINISHING_DEFINITION_BY_KEY, PAPER_DEFINITION_BY_KEY


def _as_dict(value):
    return value if isinstance(value, dict) else {}


CLIENT_ROUTING_GUARD_FIELDS = {
    "shops",
    "shop",
    "shop_id",
    "selected_shop",
    "selected_shop_id",
    "selected_shop_ids",
    "production_option",
    "production_cost",
    "production_base_price",
    "broker_markup",
    "broker_payout",
    "broker_margin",
    "broker_margin_amount",
    "broker_margin_percent",
    "gross_margin",
    "printy_fee",
    "platform_service_amount",
    "platform_service_percent",
    "shop_payout",
    "internal_pricing_snapshot",
    "internal_sourcing_snapshot",
}


def _find_forbidden_payload_keys(value, forbidden_keys=CLIENT_ROUTING_GUARD_FIELDS, prefix=""):
    matches = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in forbidden_keys:
                matches.append(path)
            matches.extend(_find_forbidden_payload_keys(child, forbidden_keys, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            matches.extend(_find_forbidden_payload_keys(child, forbidden_keys, path))
    return matches


def validate_client_calculator_payload(data):
    matches = sorted(set(_find_forbidden_payload_keys(data)))
    if matches:
        raise serializers.ValidationError(
            {
                "detail": "Client/public calculator flows cannot route to shops or submit internal pricing fields.",
                "forbidden_fields": matches,
            }
        )


def _client_visible_quote_total(raw_total, raw_snapshot, *, client_total=None):
    customer_pricing = _as_dict(_as_dict(raw_snapshot).get("customer_pricing"))
    return (
        client_total
        or customer_pricing.get("final_client_price")
        or customer_pricing.get("estimated_total")
        or raw_total
    )


def _quote_financial_split_client_total(quote):
    if quote is None:
        return None
    try:
        split = getattr(quote, "financial_split", None)
    except Exception:
        return None
    return getattr(split, "client_total", None) if split is not None else None


from quotes.turnaround import estimate_turnaround, legacy_days_from_hours
from shops.models import Shop
from accounts.services.system_accounts import is_system_account
from .serializers import QuoteItemReadSerializer
from .quote_serializers import QuoteItemCustomerSerializer, QuoteRequestAttachmentSerializer


def _assigned_manager_payload(manager):
    if manager is None:
        return None
    is_printy_managed = is_system_account(manager)
    payload = {
        "id": manager.id,
        "display_name": (
            "Printy"
            if is_printy_managed
            else getattr(manager, "name", "") or getattr(manager, "email", "") or "Print Manager"
        ),
        "short_title": "Managed by Printy" if is_printy_managed else "Print Manager",
    }
    if is_printy_managed:
        payload["is_printy_fallback"] = True
        payload["support_email"] = "support@printy.ke"
    return payload


class FinishingSelectionSerializer(serializers.Serializer):
    finishing_rate = serializers.PrimaryKeyRelatedField(queryset=FinishingRate.objects.filter(is_active=True))
    selected_side = serializers.ChoiceField(choices=["front", "back", "both"], default="both")

    def to_internal_value(self, data):
        if isinstance(data, dict) and "finishing_rate" not in data and "finishing_rate_id" in data:
            data = {**data, "finishing_rate": data["finishing_rate_id"]}
        return super().to_internal_value(data)


class CalculatorConfigPreviewSerializer(serializers.Serializer):
    product_type = serializers.ChoiceField(
        choices=["business_card", "flyer", "label_sticker", "letterhead", "booklet", "large_format"],
        help_text="Homepage calculator product preset to preview.",
    )
    quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1, default=100, help_text="Requested quantity.")
    finished_size = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Use values from /api/calculator/config/, e.g. 85x55mm, A5, A4.")
    size_mode = serializers.ChoiceField(choices=["standard", "custom"], required=False, default="standard")
    input_unit = serializers.ChoiceField(choices=["mm", "cm", "m", "in"], required=False, default="mm")
    width_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    height_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    print_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], required=False, allow_null=True, default="SIMPLEX", help_text="Flat-job print sides.")
    color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], required=False, allow_null=True, default="COLOR", help_text="Flat-job colour mode.")
    paper_stock = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Paper stock key from /api/calculator/config/.")
    material_type = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Large-format material label from /api/calculator/config/.")
    product_subtype = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Large-format subtype such as banner or poster.")
    requested_paper_category = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Fallback paper category when the buyer wants the shop to advise.")
    requested_gsm = serializers.IntegerField(required=False, allow_null=True, min_value=1, help_text="Preferred paper gsm.")
    lamination = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Finishing slug such as gloss-lamination or matt-lamination.")
    corner_rounding = serializers.BooleanField(required=False, allow_null=True, help_text="Business-card corner rounding request.")
    folding = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Optional folding preference for flyers.")
    shape = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Sticker shape.")
    cut_type = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Sticker cut type.")
    total_pages = serializers.IntegerField(required=False, allow_null=True, min_value=1, help_text="Booklet page count before normalization.")
    cover_stock = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Booklet cover stock key from /api/calculator/config/.")
    insert_stock = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Booklet insert stock key from /api/calculator/config/.")
    requested_cover_paper_category = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Fallback cover paper category.")
    requested_cover_gsm = serializers.IntegerField(required=False, allow_null=True, min_value=1, help_text="Fallback cover paper gsm.")
    requested_insert_paper_category = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Fallback insert paper category.")
    requested_insert_gsm = serializers.IntegerField(required=False, allow_null=True, min_value=1, help_text="Fallback insert paper gsm.")
    cover_lamination = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Booklet cover lamination mode.")
    binding_type = serializers.CharField(required=False, allow_blank=True, allow_null=True, help_text="Booklet binding type, e.g. saddle_stitch.")
    cutting = serializers.BooleanField(required=False, allow_null=True, help_text="Whether booklet cutting is requested.")
    turnaround_hours = serializers.IntegerField(required=False, allow_null=True, min_value=1, help_text="Optional turnaround target in working hours.")
    urgency_type = serializers.ChoiceField(
        choices=["standard", "same_day", "express", "after_hours", "emergency"],
        required=False,
        default="standard",
    )
    requested_deadline = serializers.DateTimeField(required=False, allow_null=True)
    requested_delivery_time = serializers.DateTimeField(required=False, allow_null=True)

    def to_internal_value(self, data):
        normalized = normalize_size_payload(
            data,
            legacy_width_keys=("custom_width_mm",),
            legacy_height_keys=("custom_height_mm",),
        )
        return super().to_internal_value(normalized)

    def validate(self, attrs):
        if attrs.get("product_type") == "large_format":
            attrs = validate_size_selection(attrs)
        return attrs


class PartnerProductionMatchResultSerializer(serializers.Serializer):
    shop_id = serializers.IntegerField()
    shop_name = serializers.CharField(required=False, allow_blank=True)
    shop_display_name = serializers.CharField()
    shop_slug = serializers.CharField(required=False, allow_blank=True)
    shop_location = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    shop_location_area = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    can_produce = serializers.BooleanField()
    production_cost = serializers.CharField(required=False, allow_null=True)
    estimated_production_cost = serializers.CharField(required=False, allow_null=True)
    estimated_shop_payout = serializers.CharField(required=False, allow_null=True)
    currency = serializers.CharField(required=False, allow_blank=True)
    price_available = serializers.BooleanField()
    price_status = serializers.CharField()
    pricing_source = serializers.CharField(required=False, allow_blank=True)
    missing_requirements = serializers.ListField(child=serializers.CharField(), default=list)
    missing_spec_warnings = serializers.ListField(child=serializers.CharField(), default=list)
    available_reasons = serializers.ListField(child=serializers.CharField(), default=list)
    capability_notes = serializers.ListField(child=serializers.CharField(), default=list)
    estimated_turnaround = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    turnaround_hours = serializers.IntegerField(required=False, allow_null=True)
    turnaround_days = serializers.IntegerField(required=False, allow_null=True)
    turnaround_label = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    location_summary = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    location_area = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    match_type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    match_score = serializers.FloatField(required=False, allow_null=True)
    score = serializers.FloatField(required=False, allow_null=True)
    is_recommended = serializers.BooleanField(required=False, default=False)
    recommendation_rank = serializers.IntegerField(required=False, allow_null=True)
    recommendation_label = serializers.CharField(required=False, allow_blank=True)
    explanation = serializers.CharField(required=False, allow_blank=True)
    reason = serializers.CharField(required=False, allow_blank=True)
    product_type = serializers.CharField()
    preview_snapshot = serializers.JSONField(required=False, allow_null=True)
    production_breakdown = serializers.JSONField(required=False, allow_null=True)
    selection = serializers.JSONField(required=False, allow_null=True)


class PartnerProductionMatchResponseSerializer(serializers.Serializer):
    product_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    summary = serializers.CharField()
    missing_fields = serializers.ListField(child=serializers.CharField(), default=list)
    results = PartnerProductionMatchResultSerializer(many=True, default=list)
    matched_count = serializers.IntegerField(default=0)
    results_count = serializers.IntegerField(default=0)
    pricing_snapshot = serializers.JSONField(required=False, allow_null=True)
    spec_snapshot = serializers.JSONField(required=False, allow_null=True)
    visibility = serializers.JSONField(required=False, allow_null=True)


class CalculatorPreviewSerializer(serializers.Serializer):
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all())
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    quantity = serializers.IntegerField(min_value=1)
    paper = serializers.PrimaryKeyRelatedField(queryset=Paper.objects.filter(is_active=True))
    machine = serializers.PrimaryKeyRelatedField(queryset=Machine.objects.filter(is_active=True))
    color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="SIMPLEX")
    apply_duplex_surcharge = serializers.BooleanField(required=False, allow_null=True, default=None)
    size_mode = serializers.ChoiceField(choices=["standard", "custom"], required=False, default="custom")
    size_label = serializers.CharField(required=False, allow_blank=True, default="")
    input_unit = serializers.ChoiceField(choices=["mm", "cm", "m", "in"], required=False, default="mm")
    width_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    height_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    finishings = FinishingSelectionSerializer(many=True, required=False)
    urgency_type = serializers.ChoiceField(
        choices=["standard", "same_day", "express", "after_hours", "emergency"],
        required=False,
        default="standard",
    )
    requested_deadline = serializers.DateTimeField(required=False, allow_null=True)
    requested_delivery_time = serializers.DateTimeField(required=False, allow_null=True)

    def to_internal_value(self, data):
        normalized = normalize_size_payload(
            data,
            legacy_width_keys=("chosen_width_mm",),
            legacy_height_keys=("chosen_height_mm",),
        )
        return super().to_internal_value(normalized)

    def validate(self, attrs):
        attrs = validate_size_selection(attrs)
        shop = attrs["shop"]
        product = attrs.get("product")
        errors = {}

        if product and product.shop_id != shop.id:
            errors["product"] = ["Product must belong to the selected shop."]
        if not product and (not attrs.get("width_mm") or not attrs.get("height_mm")):
            errors["non_field_errors"] = ["width_mm and height_mm are required for custom previews."]

        if attrs["paper"].shop_id != shop.id:
            errors["paper"] = ["Paper must belong to the selected shop."]
        if attrs["machine"].shop_id != shop.id:
            errors["machine"] = ["Machine must belong to the selected shop."]

        finishing_errors = []
        for selection in attrs.get("finishings") or []:
            if selection["finishing_rate"].shop_id != shop.id:
                finishing_errors.append(
                    {"finishing_rate": ["Finishing rate must belong to the selected shop."]}
                )
            else:
                finishing_errors.append({})
        if any(item for item in finishing_errors):
            errors["finishings"] = finishing_errors

        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class BookletCalculatorPreviewSerializer(serializers.Serializer):
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all())
    quantity = serializers.IntegerField(min_value=1)
    total_pages = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    binding_type = serializers.ChoiceField(choices=["saddle_stitch", "perfect_bind", "wire_o"], default="saddle_stitch")
    cover_paper = serializers.PrimaryKeyRelatedField(queryset=Paper.objects.filter(is_active=True), required=False, allow_null=True)
    insert_paper = serializers.PrimaryKeyRelatedField(queryset=Paper.objects.filter(is_active=True), required=False, allow_null=True)
    cover_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="DUPLEX")
    insert_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="DUPLEX")
    cover_color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    insert_color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    cover_lamination_mode = serializers.ChoiceField(choices=["none", "front", "both"], default="none")
    cover_lamination_finishing_rate = serializers.PrimaryKeyRelatedField(
        queryset=FinishingRate.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    finishings = FinishingSelectionSerializer(many=True, required=False)
    binding_finishing_rate = serializers.PrimaryKeyRelatedField(
        queryset=FinishingRate.objects.filter(is_active=True),
        required=False,
        allow_null=True,
    )
    turnaround_hours = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    urgency_type = serializers.ChoiceField(
        choices=["standard", "same_day", "express", "after_hours", "emergency"],
        required=False,
        default="standard",
    )
    requested_deadline = serializers.DateTimeField(required=False, allow_null=True)
    requested_delivery_time = serializers.DateTimeField(required=False, allow_null=True)
    size_mode = serializers.ChoiceField(choices=["standard", "custom"], required=False, default="custom")
    size_label = serializers.CharField(required=False, allow_blank=True, default="")
    input_unit = serializers.ChoiceField(choices=["mm", "cm", "m", "in"], required=False, default="mm")
    width_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    height_input = serializers.DecimalField(required=False, allow_null=True, max_digits=10, decimal_places=3, min_value=Decimal("0.001"))
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def to_internal_value(self, data):
        normalized = normalize_size_payload(
            data,
            legacy_width_keys=("chosen_width_mm",),
            legacy_height_keys=("chosen_height_mm",),
        )
        return super().to_internal_value(normalized)

    def validate(self, attrs):
        attrs = validate_size_selection(attrs)
        shop = attrs["shop"]
        errors = {}
        cover_paper = attrs.get("cover_paper")
        insert_paper = attrs.get("insert_paper")
        if cover_paper and cover_paper.shop_id != shop.id:
            errors["cover_paper"] = ["Cover paper must belong to the selected shop."]
        if insert_paper and insert_paper.shop_id != shop.id:
            errors["insert_paper"] = ["Insert paper must belong to the selected shop."]
        if attrs.get("cover_lamination_finishing_rate") and attrs["cover_lamination_finishing_rate"].shop_id != shop.id:
            errors["cover_lamination_finishing_rate"] = ["Lamination rate must belong to the selected shop."]
        if attrs.get("binding_finishing_rate") and attrs["binding_finishing_rate"].shop_id != shop.id:
            errors["binding_finishing_rate"] = ["Binding rate must belong to the selected shop."]
        finishing_errors = []
        for selection in attrs.get("finishings") or []:
            if selection["finishing_rate"].shop_id != shop.id:
                finishing_errors.append(
                    {"finishing_rate": ["Finishing rate must belong to the selected shop."]}
                )
            else:
                finishing_errors.append({})
        if any(item for item in finishing_errors):
            errors["finishings"] = finishing_errors
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class LargeFormatCalculatorPreviewSerializer(serializers.Serializer):
    def validate(self, attrs):
        raise serializers.ValidationError("Large-format material pricing is postponed for MVP.")


class DashboardFinishingSelectionSerializer(serializers.Serializer):
    finishing_id = serializers.PrimaryKeyRelatedField(
        queryset=FinishingRate.objects.filter(is_active=True),
        source="rule"
    )
    selected_side = serializers.ChoiceField(choices=["front", "back", "both"], default="both")

    def to_internal_value(self, data):
        if isinstance(data, dict) and "finishing_id" not in data and "id" in data:
            data = {**data, "finishing_id": data["id"]}
        return super().to_internal_value(data)


class DashboardCalculatorPayloadSerializer(serializers.Serializer):
    job_type = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1)
    width_mm = serializers.IntegerField(required=False, min_value=1)
    height_mm = serializers.IntegerField(required=False, min_value=1)
    paper_id = serializers.PrimaryKeyRelatedField(queryset=Paper.objects.filter(is_active=True))
    sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], default="SIMPLEX")
    color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], default="COLOR")
    orientation = serializers.CharField(required=False, allow_blank=True)
    bleed_mm = serializers.IntegerField(required=False, default=3)
    finishings = DashboardFinishingSelectionSerializer(many=True, required=False)
    urgency_type = serializers.ChoiceField(
        choices=["standard", "same_day", "express", "after_hours", "emergency"],
        required=False,
        default="standard",
    )
    requested_deadline = serializers.DateTimeField(required=False, allow_null=True)
    requested_delivery_time = serializers.DateTimeField(required=False, allow_null=True)

    def to_internal_value(self, data):
        normalized = normalize_size_payload(
            data,
            legacy_width_keys=("chosen_width_mm",),
            legacy_height_keys=("chosen_height_mm",),
        )
        return super().to_internal_value(normalized)


class CalculatorDraftCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    session_key = serializers.CharField(required=False, allow_blank=True, max_length=64)
    selected_product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    calculator_inputs_snapshot = serializers.JSONField()
    custom_product_snapshot = serializers.JSONField(required=False)
    request_details_snapshot = serializers.JSONField(required=False)
    artwork_token = serializers.CharField(required=False, allow_blank=True, max_length=64)
    artwork_filename = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        validate_client_calculator_payload(self.initial_data)
        return attrs


class CalculatorDraftUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    session_key = serializers.CharField(required=False, allow_blank=True, max_length=64)
    selected_product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    calculator_inputs_snapshot = serializers.JSONField(required=False)
    custom_product_snapshot = serializers.JSONField(required=False)
    request_details_snapshot = serializers.JSONField(required=False)
    artwork_token = serializers.CharField(required=False, allow_blank=True, max_length=64)
    artwork_filename = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        validate_client_calculator_payload(self.initial_data)
        return attrs


class GuestCalculatorDraftSerializer(serializers.Serializer):
    session_key = serializers.CharField(max_length=64)
    title = serializers.CharField(required=False, allow_blank=True)
    calculator_inputs_snapshot = serializers.JSONField()
    request_details_snapshot = serializers.JSONField(required=False)
    artwork_token = serializers.CharField(required=False, allow_blank=True, max_length=64)
    artwork_filename = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        validate_client_calculator_payload(self.initial_data)
        return attrs


class GuestDraftClaimSerializer(serializers.Serializer):
    session_key = serializers.CharField(max_length=64)


class GuestArtworkUploadSerializer(serializers.Serializer):
    session_key = serializers.CharField(max_length=64)
    file = serializers.FileField()


class PartnerQuotePreviewSerializer(serializers.Serializer):
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all())
    pricing_snapshot = serializers.JSONField()
    partner_markup = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.00"))

    def validate(self, attrs):
        pricing_snapshot = attrs.get("pricing_snapshot")
        if not isinstance(pricing_snapshot, dict):
            raise serializers.ValidationError({"pricing_snapshot": ["Production pricing snapshot is required."]})

        selected_shops = pricing_snapshot.get("selected_shops")
        if not isinstance(selected_shops, list) or not selected_shops:
            raise serializers.ValidationError({"pricing_snapshot": ["Choose a priced production shop before sending this quote."]})

        shop_entry = next(
            (
                entry for entry in selected_shops
                if isinstance(entry, dict) and (entry.get("id") == attrs["shop"].id or entry.get("slug") == attrs["shop"].slug)
            ),
            None,
        )
        if not shop_entry:
            raise serializers.ValidationError({"shop": ["Selected shop must be one of the priced production options."]})

        preview = _as_dict(shop_entry.get("preview")) or shop_entry
        totals = _as_dict(preview.get("totals"))
        base_price = totals.get("shop_total") or totals.get("subtotal") or totals.get("grand_total")
        if base_price in (None, ""):
            raise serializers.ValidationError({"pricing_snapshot": ["Production price is not available yet for the selected shop."]})

        try:
            validate_partner_markup_amount(base_price=base_price, markup_amount=attrs["partner_markup"])
        except ValueError as exc:
            raise serializers.ValidationError({"partner_markup": [str(exc)]})
        return attrs


class PartnerAssignedRequestShopOptionsSerializer(serializers.Serializer):
    finished_size = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    paper_stock = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    print_sides = serializers.ChoiceField(choices=["SIMPLEX", "DUPLEX"], required=False, allow_null=True)
    color_mode = serializers.ChoiceField(choices=["BW", "COLOR"], required=False, allow_null=True)
    lamination = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    urgency_type = serializers.ChoiceField(
        choices=["standard", "same_day", "express", "after_hours", "emergency"],
        required=False,
        allow_null=True,
    )
    requested_paper_category = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    requested_gsm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    total_pages = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    cover_stock = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    insert_stock = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    requested_cover_paper_category = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    requested_cover_gsm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    requested_insert_paper_category = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    requested_insert_gsm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    cover_lamination = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    binding_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    material_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    product_subtype = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    width_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    height_mm = serializers.IntegerField(required=False, allow_null=True, min_value=1)


class PartnerQuoteCreateSerializer(serializers.Serializer):
    shop = serializers.PrimaryKeyRelatedField(queryset=Shop.objects.all())
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    client_id = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(),
        required=False,
        allow_null=True,
        source="client_user",
    )
    client_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    client_email = serializers.EmailField(required=False, allow_blank=True)
    client_phone = serializers.CharField(required=False, allow_blank=True, max_length=50)
    client_company = serializers.CharField(required=False, allow_blank=True, max_length=255)
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    calculator_inputs_snapshot = serializers.JSONField()
    pricing_snapshot = serializers.JSONField()
    partner_markup = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.00"))
    save_as_draft = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        selected_shops = attrs.get("pricing_snapshot", {}).get("selected_shops") or []
        shop_entry = next(
            (
                entry for entry in selected_shops
                if isinstance(entry, dict) and (entry.get("id") == attrs["shop"].id or entry.get("slug") == attrs["shop"].slug)
            ),
            {},
        )
        preview = _as_dict(shop_entry.get("preview")) or shop_entry
        totals = _as_dict(preview.get("totals"))
        base_price = totals.get("shop_total") or totals.get("subtotal") or totals.get("grand_total")
        try:
            validate_partner_markup_amount(base_price=base_price, markup_amount=attrs["partner_markup"])
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        if attrs.get("save_as_draft"):
            return attrs
        if not (attrs.get("client_user") or attrs.get("client_email") or attrs.get("client_phone") or attrs.get("client_name")):
            raise serializers.ValidationError("Client email or an existing client is required.")
        return attrs


class ProductionOptionCreateSerializer(serializers.Serializer):
    quote_request_id = serializers.PrimaryKeyRelatedField(
        queryset=QuoteRequest.objects.all(),
        source="quote_request",
    )
    shop_id = serializers.PrimaryKeyRelatedField(
        queryset=Shop.objects.filter(is_active=True),
        source="shop",
    )
    calculator_context = serializers.ChoiceField(choices=CalculatorDraftContext.choices)
    intent = serializers.ChoiceField(choices=CalculatorDraftIntent.choices)
    production_cost = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    estimated_turnaround_hours = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    capacity_status = serializers.CharField(required=False, allow_blank=True, max_length=40)
    score = serializers.DecimalField(required=False, allow_null=True, max_digits=8, decimal_places=4)
    pricing_snapshot = serializers.JSONField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        context = attrs.get("calculator_context")
        intent = attrs.get("intent")
        if context not in {
            CalculatorDraftContext.MANAGER_DASHBOARD,
            CalculatorDraftContext.BROKER_DASHBOARD,
            CalculatorDraftContext.ADMIN_DASHBOARD,
        }:
            raise serializers.ValidationError({"calculator_context": "Production sourcing is only available to manager, broker, or admin calculators."})
        if intent != CalculatorDraftIntent.SOURCE_PRODUCTION:
            raise serializers.ValidationError({"intent": "Production sourcing requires source_production intent."})
        quote_request = attrs.get("quote_request")
        if quote_request and quote_request.shop_id is not None:
            raise serializers.ValidationError({"quote_request_id": "Production options can only be attached to manager-led requests with shop=None."})
        return attrs


class ProductionOptionReadSerializer(serializers.ModelSerializer):
    shop_id = serializers.IntegerField(source="shop.id", read_only=True)
    shop_name = serializers.CharField(source="shop.name", read_only=True)

    class Meta:
        model = ProductionOption
        fields = [
            "id",
            "quote_request",
            "shop_id",
            "shop_name",
            "production_cost",
            "estimated_turnaround_hours",
            "capacity_status",
            "score",
            "status",
            "pricing_snapshot",
            "notes",
            "created_at",
            "updated_at",
        ]


class PartnerQuoteAttachClientSerializer(serializers.Serializer):
    client_id = serializers.PrimaryKeyRelatedField(
        queryset=get_user_model().objects.all(),
        required=False,
        allow_null=True,
        source="client_user",
    )
    client_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    client_email = serializers.EmailField(required=False, allow_blank=True)
    client_phone = serializers.CharField(required=False, allow_blank=True, max_length=50)
    client_company = serializers.CharField(required=False, allow_blank=True, max_length=255)

    def validate(self, attrs):
        if attrs.get("client_user") is not None:
            return attrs
        if not attrs.get("client_email"):
            raise serializers.ValidationError("Client email is required when no existing client is selected.")
        return attrs


class CalculatorDraftReadSerializer(serializers.ModelSerializer):
    generated_request_ids = serializers.SerializerMethodField()
    shop_name = serializers.SerializerMethodField()
    source_job_id = serializers.IntegerField(read_only=True)
    direct_intake_shop_id = serializers.IntegerField(read_only=True)
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = CalculatorDraft
        fields = [
            "id",
            "draft_reference",
            "title",
            "status",
            "raw_status",
            "status_label",
            "calculator_context",
            "intent",
            "shop_name",
            "selected_product",
            "source_job_id",
            "direct_intake_shop_id",
            "intake_mode",
            "calculator_inputs_snapshot",
            "custom_product_snapshot",
            "request_details_snapshot",
            "artwork_token",
            "artwork_filename",
            "generated_request_ids",
            "created_at",
            "updated_at",
        ]

    def get_shop_name(self, obj):
        return None

    def get_generated_request_ids(self, obj):
        return list(obj.generated_requests.values_list("id", flat=True))

    def get_status(self, obj):
        return normalize_calculator_draft_status(
            obj.status,
            has_shop=False,
            has_request_details=bool(obj.request_details_snapshot),
            has_pricing=False,
        )

    def get_status_label(self, obj):
        return calculator_draft_status_label(self.get_status(obj))


class CalculatorDraftSendSerializer(serializers.Serializer):
    selected_manager_id = serializers.IntegerField(required=False, allow_null=True)
    manager_selection_mode = serializers.ChoiceField(
        choices=["client_selected", "printy_auto"],
        required=False,
    )
    request_details_snapshot = serializers.JSONField(required=False)

    def validate_selected_manager_id(self, value):
        from quotes.services_workflow import resolve_assigned_manager

        if value in (None, ""):
            return None
        try:
            manager = resolve_assigned_manager(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return manager.id if manager is not None else None

    def validate(self, attrs):
        validate_client_calculator_payload(self.initial_data)
        mode = attrs.get("manager_selection_mode")
        if mode == "client_selected" and attrs.get("selected_manager_id") is None:
            raise serializers.ValidationError({"selected_manager_id": "manager_id is required for client_selected mode."})
        if not mode:
            attrs["manager_selection_mode"] = "client_selected" if attrs.get("selected_manager_id") is not None else "printy_auto"
        return attrs


class RecommendedPrintManagerSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    display_name = serializers.CharField()
    brand_name = serializers.CharField(allow_blank=True)
    specializations = serializers.ListField(child=serializers.CharField(), required=False)
    avg_response_hours = serializers.FloatField(required=False, allow_null=True)
    completed_jobs = serializers.IntegerField()
    satisfaction_rating = serializers.FloatField(required=False, allow_null=True)
    distance_km = serializers.FloatField(required=False, allow_null=True)
    is_previous_manager = serializers.BooleanField(default=False)
    badge = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    recommendation_reason = serializers.CharField()


class IntakeRecommendedManagerQuerySerializer(serializers.Serializer):
    product_type = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)
    paper_gsm = serializers.IntegerField(required=False, allow_null=True)
    size = serializers.CharField(required=False, allow_blank=True)
    client_id = serializers.IntegerField(required=False, allow_null=True)


class IntakeSubmitSerializer(serializers.Serializer):
    draft_id = serializers.IntegerField(required=False, allow_null=True)
    selected_manager_id = serializers.IntegerField(required=False, allow_null=True)
    manager_selection_mode = serializers.ChoiceField(
        choices=["client_selected", "printy_auto"],
        required=False,
    )
    artwork_reference = serializers.CharField(required=False, allow_blank=True)
    artwork_token = serializers.CharField(required=False, allow_blank=True, max_length=64)
    artwork_filename = serializers.CharField(required=False, allow_blank=True, max_length=255)
    title = serializers.CharField(required=False, allow_blank=True)
    calculator_inputs_snapshot = serializers.JSONField(required=False)
    pricing_snapshot = serializers.JSONField(required=False)
    request_details_snapshot = serializers.JSONField(required=False)

    def validate_selected_manager_id(self, value):
        from quotes.services_workflow import resolve_assigned_manager

        if value in (None, ""):
            return None
        try:
            manager = resolve_assigned_manager(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc))
        return manager.id if manager is not None else None

    def validate(self, attrs):
        if attrs.get("draft_id"):
            has_source = True
        elif attrs.get("calculator_inputs_snapshot"):
            has_source = True
        else:
            has_source = False
        if not has_source:
            raise serializers.ValidationError("draft_id or calculator_inputs_snapshot is required.")
        mode = attrs.get("manager_selection_mode")
        if mode == "client_selected" and attrs.get("selected_manager_id") is None:
            raise serializers.ValidationError({"selected_manager_id": "manager_id is required for client_selected mode."})
        if not mode:
            attrs["manager_selection_mode"] = "client_selected" if attrs.get("selected_manager_id") is not None else "printy_auto"
        return attrs


class QuoteRequestReadSerializer(serializers.ModelSerializer):
    source_draft_reference = serializers.CharField(source="source_draft.draft_reference", read_only=True)
    latest_response = serializers.SerializerMethodField()
    responses_count = serializers.SerializerMethodField()
    attachments = QuoteRequestAttachmentSerializer(many=True, read_only=True)
    assigned_manager = serializers.SerializerMethodField()
    manager_selection_mode = serializers.CharField(read_only=True)
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "request_reference",
            "shop",
            "created_by",
            "assigned_manager",
            "manager_selection_mode",
            "status",
            "raw_status",
            "status_label",
            "customer_name",
            "customer_email",
            "customer_phone",
            "source_draft",
            "source_draft_reference",
            "request_snapshot",
            "latest_response",
            "responses_count",
            "attachments",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if actor != OPS_ACTOR:
            data.pop("request_snapshot", None)
        if actor == CLIENT_ACTOR:
            for key in ("shop", "created_by", "source_draft"):
                data.pop(key, None)
        return data

    def get_latest_response(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        
        latest = obj.get_latest_response()
        if not latest:
            return None
            
        normalized_status = normalize_quote_response_status(latest.status)
        data = {
            "id": latest.id,
            "quote_reference": latest.quote_reference,
            "status": normalized_status,
            "raw_status": latest.status,
            "status_label": quote_response_status_label(normalized_status),
            "total": _client_visible_quote_total(
                latest.total,
                latest.response_snapshot,
                client_total=_quote_financial_split_client_total(latest),
            ),
            "turnaround_days": latest.turnaround_days,
            "turnaround_hours": latest.turnaround_hours,
            "estimated_ready_at": latest.estimated_ready_at,
            "human_ready_text": latest.human_ready_text,
            "turnaround_label": latest.turnaround_label,
            "created_at": latest.created_at,
            "sent_at": latest.sent_at,
        }
        
        if actor == OPS_ACTOR:
            data["response_snapshot"] = latest.response_snapshot
            data["revised_pricing_snapshot"] = latest.revised_pricing_snapshot
            
        return data

    def get_responses_count(self, obj):
        return obj.quotes.count()

    def get_assigned_manager(self, obj):
        return _assigned_manager_payload(getattr(obj, "assigned_manager", None))

    def get_status(self, obj):
        return normalize_quote_request_status(obj.status)

    def get_status_label(self, obj):
        return quote_request_status_label(self.get_status(obj))


class DashboardQuoteRequestSummarySerializer(serializers.ModelSerializer):
    source_draft_reference = serializers.CharField(source="source_draft.draft_reference", read_only=True)
    latest_response = serializers.SerializerMethodField()
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "request_reference",
            "shop",
            "status",
            "raw_status",
            "status_label",
            "customer_name",
            "customer_email",
            "customer_phone",
            "source_draft_reference",
            "request_snapshot",
            "latest_response",
            "created_at",
            "updated_at",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        if actor != OPS_ACTOR:
            data.pop("request_snapshot", None)
        if actor == CLIENT_ACTOR:
            data.pop("shop", None)
        return data

    def get_latest_response(self, obj):
        request = self.context.get("request")
        actor = resolve_actor(getattr(request, "user", None))
        
        latest_response_id = getattr(obj, "latest_response_id", None)
        if not latest_response_id:
            return None
            
        raw_status = getattr(obj, "latest_response_status", "")
        normalized_status = normalize_quote_response_status(raw_status)
        
        raw_snapshot = getattr(obj, "latest_response_snapshot", None)
        raw_revised = getattr(obj, "latest_revised_pricing_snapshot", None)
        
        data = {
            "id": latest_response_id,
            "quote_reference": getattr(obj, "latest_response_reference", ""),
            "status": normalized_status,
            "raw_status": raw_status,
            "status_label": quote_response_status_label(normalized_status),
            "total": _client_visible_quote_total(getattr(obj, "latest_response_total", None), raw_snapshot),
            "created_at": getattr(obj, "latest_response_created_at", None),
            "sent_at": getattr(obj, "latest_response_sent_at", None),
        }
        
        if actor == OPS_ACTOR:
            data["response_snapshot"] = raw_snapshot
            data["revised_pricing_snapshot"] = raw_revised
            
        return data

    def get_status(self, obj):
        return normalize_quote_request_status(obj.status)

    def get_status_label(self, obj):
        return quote_request_status_label(self.get_status(obj))


class QuoteResponseCreateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["draft", "sent", "modified", "accepted", "rejected", "expired"])
    response_snapshot = serializers.JSONField()
    revised_pricing_snapshot = serializers.JSONField(required=False)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    note = serializers.CharField(required=False, allow_blank=True)
    turnaround_days = serializers.IntegerField(required=False, min_value=0)
    turnaround_hours = serializers.IntegerField(required=False, min_value=1)

    def validate_status(self, value):
        normalized = denormalize_quote_response_status(value)
        if normalized not in {
            QuoteOfferStatus.PENDING,
            QuoteOfferStatus.SENT,
            QuoteOfferStatus.MODIFIED,
            QuoteOfferStatus.ACCEPTED,
            QuoteOfferStatus.REJECTED,
            QuoteOfferStatus.EXPIRED,
        }:
            raise serializers.ValidationError("Unsupported quote response status.")
        return normalized


class QuoteResponseUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["draft", "sent", "modified", "accepted", "rejected", "expired"])
    response_snapshot = serializers.JSONField(required=False)
    revised_pricing_snapshot = serializers.JSONField(required=False)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    note = serializers.CharField(required=False, allow_blank=True)
    turnaround_days = serializers.IntegerField(required=False, min_value=0)
    turnaround_hours = serializers.IntegerField(required=False, min_value=1)

    def validate_status(self, value):
        normalized = denormalize_quote_response_status(value)
        if normalized not in {
            QuoteOfferStatus.PENDING,
            QuoteOfferStatus.SENT,
            QuoteOfferStatus.MODIFIED,
            QuoteOfferStatus.ACCEPTED,
            QuoteOfferStatus.REJECTED,
            QuoteOfferStatus.EXPIRED,
        }:
            raise serializers.ValidationError("Unsupported quote response status.")
        return normalized


class QuoteResponseReadSerializer(serializers.ModelSerializer):
    request_reference = serializers.CharField(source="quote_request.request_reference", read_only=True)
    shop_name = serializers.SerializerMethodField()
    shop_slug = serializers.SerializerMethodField()
    raw_status = serializers.CharField(source="status", read_only=True)
    created_by_name = serializers.CharField(source="created_by.get_full_name", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    share_token = serializers.SerializerMethodField()
    whatsapp_available = serializers.SerializerMethodField()
    whatsapp_url = serializers.SerializerMethodField()
    whatsapp_label = serializers.SerializerMethodField()
    conversation = serializers.SerializerMethodField()
    response_snapshot = serializers.SerializerMethodField()
    revised_pricing_snapshot = serializers.SerializerMethodField()
    total = serializers.SerializerMethodField()
    expires_at = serializers.DateTimeField(read_only=True)
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = [
            "id",
            "quote_reference",
            "quote_request",
            "request_reference",
            "shop",
            "shop_name",
            "shop_slug",
            "status",
            "raw_status",
            "status_label",
            "total",
            "expires_at",
            "is_expired",
            "note",
            "turnaround_days",
            "turnaround_hours",
            "estimated_ready_at",
            "human_ready_text",
            "turnaround_label",
            "response_snapshot",
            "revised_pricing_snapshot",
            "accepted_at",
            "rejected_at",
            "rejection_reason",
            "rejection_message",
            "revision_number",
            "pricing_locked_at",
            "created_at",
            "sent_at",
            "conversation",
            "whatsapp_available",
            "whatsapp_url",
            "whatsapp_label",
            "share_token",
            "created_by_name",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        actor = self._visibility_actor(instance)
        if actor != OPS_ACTOR:
            data.pop("response_snapshot", None)
            data.pop("revised_pricing_snapshot", None)
        if actor == CLIENT_ACTOR:
            for key in ("shop", "shop_name", "shop_slug"):
                data.pop(key, None)
        return data

    def get_status(self, obj):
        if obj.is_expired and obj.status in {QuoteOfferStatus.SENT, QuoteOfferStatus.REVISED, QuoteOfferStatus.MODIFIED}:
            return QuoteOfferStatus.EXPIRED
        return normalize_quote_response_status(obj.status)

    def get_is_expired(self, obj):
        return obj.is_expired and obj.status != QuoteOfferStatus.ACCEPTED

    def get_total(self, obj):
        actor = self._visibility_actor(obj)
        if actor in {SHOP_ACTOR, OPS_ACTOR, PARTNER_ACTOR}:
            return obj.total
        response_snapshot = _as_dict(obj.response_snapshot)
        customer_pricing = _as_dict(response_snapshot.get("customer_pricing"))
        return (
            _quote_financial_split_client_total(obj)
            or customer_pricing.get("final_client_price")
            or customer_pricing.get("estimated_total")
            or obj.total
        )

    def get_shop_name(self, obj):
        actor = self._visibility_actor(obj)
        topology_mode = resolve_topology_mode_for_quote_request(obj.quote_request)
        if actor == CLIENT_ACTOR:
            return project_client_counterparty_name(
                fallback_name=obj.shop.name if obj.shop_id else None,
                topology_mode=topology_mode,
                request_snapshot=getattr(obj.quote_request, "request_snapshot", None),
                response_snapshot=obj.response_snapshot,
            )
        return project_identity(obj.shop.name if obj.shop_id else None, actor=actor, topology_mode=topology_mode)

    def get_shop_slug(self, obj):
        actor = self._visibility_actor(obj)
        topology_mode = resolve_topology_mode_for_quote_request(obj.quote_request)
        if actor in {SHOP_ACTOR, OPS_ACTOR, PARTNER_ACTOR} or topology_mode == "marketplace_legacy":
            return obj.shop.slug if obj.shop_id else ""
        return "partner"

    def _visibility_actor(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            if getattr(user, "is_staff", False):
                return OPS_ACTOR
            if getattr(user, "id", None) == obj.shop.owner_id:
                return SHOP_ACTOR
            if getattr(user, "role", None) == "broker":
                return PARTNER_ACTOR
        return CLIENT_ACTOR

    def get_share_token(self, obj):
        link = obj.share_links.first()
        return link.token if link else None

    def get_status_label(self, obj):
        return quote_response_status_label(self.get_status(obj))

    def get_response_snapshot(self, obj):
        actor = self._visibility_actor(obj)
        if actor == OPS_ACTOR:
            return obj.response_snapshot
        return None

    def get_revised_pricing_snapshot(self, obj):
        actor = self._visibility_actor(obj)
        if actor == OPS_ACTOR:
            return obj.revised_pricing_snapshot
        return None

    def _whatsapp_handoff(self, obj):
        request = self.context.get("request")
        viewer_role = "buyer"
        request_user = getattr(request, "user", None) if request else None
        if request_user and getattr(request_user, "id", None) == obj.shop.owner_id:
            viewer_role = "shop"
        return build_quote_request_whatsapp_handoff(obj.quote_request, viewer_role=viewer_role)

    def get_whatsapp_available(self, obj):
        return self._whatsapp_handoff(obj).get("available", False)

    def get_whatsapp_url(self, obj):
        return self._whatsapp_handoff(obj).get("url", "")

    def get_whatsapp_label(self, obj):
        return self._whatsapp_handoff(obj).get("label", "")

    def get_conversation(self, obj):
        messages = obj.messages.select_related("sender").order_by("created_at", "id")
        return QuoteConversationMessageSerializer(messages, many=True).data


class QuoteConversationMessageSerializer(serializers.ModelSerializer):
    request_id = serializers.IntegerField(source="quote_request_id", read_only=True)
    response_id = serializers.IntegerField(source="quote_id", read_only=True)
    sender_name = serializers.SerializerMethodField()
    sender_role = serializers.SerializerMethodField()
    message = serializers.CharField(source="body", read_only=True)
    message_type = serializers.SerializerMethodField()
    recipient_user_id = serializers.IntegerField(source="recipient_id", read_only=True)
    recipient_shop_id = serializers.IntegerField(source="shop_id", read_only=True)
    is_read = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequestMessage
        fields = [
            "id",
            "request_id",
            "response_id",
            "sender_name",
            "sender_role",
            "recipient_user_id",
            "recipient_shop_id",
            "message_type",
            "subject",
            "message",
            "proposed_price",
            "proposed_turnaround",
            "proposed_quantity",
            "proposed_material",
            "proposed_gsm",
            "proposed_size",
            "proposed_finishing",
            "is_read",
            "read_at",
            "created_at",
            "updated_at",
        ]

    def get_sender_name(self, obj):
        sender = obj.sender
        if not sender:
            return "System"
        return getattr(sender, "name", "") or getattr(sender, "email", "") or "User"

    def get_sender_role(self, obj):
        if obj.sender_role == QuoteRequestMessage.SenderRole.SHOP:
            return "shop_owner"
        return obj.sender_role

    def get_message_type(self, obj):
        return obj.conversation_type or obj.message_type

    def get_is_read(self, obj):
        return obj.read_at is not None


class ClientResponseRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=True, allow_blank=False, max_length=255)
    message = serializers.CharField(required=False, allow_blank=True)


class ClientResponseReplySerializer(serializers.Serializer):
    message_type = serializers.ChoiceField(
        choices=QuoteRequestMessage.ConversationType.choices,
    )
    subject = serializers.CharField(required=False, allow_blank=True)
    message = serializers.CharField(required=True, allow_blank=False)
    proposed_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    proposed_turnaround = serializers.CharField(required=False, allow_blank=True)
    proposed_quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    proposed_material = serializers.CharField(required=False, allow_blank=True)
    proposed_gsm = serializers.CharField(required=False, allow_blank=True)
    proposed_size = serializers.CharField(required=False, allow_blank=True)
    proposed_finishing = serializers.JSONField(required=False)

    def validate_message_type(self, value):
        allowed = {
            QuoteRequestMessage.ConversationType.CLIENT_QUESTION,
            QuoteRequestMessage.ConversationType.CLIENT_COUNTER_OFFER,
            QuoteRequestMessage.ConversationType.CLIENT_CHANGE_REQUEST,
            QuoteRequestMessage.ConversationType.CLIENT_FILE_UPDATE,
        }
        if value not in allowed:
            raise serializers.ValidationError("Unsupported client conversation type.")
        return value


class ShopResponseReplySerializer(serializers.Serializer):
    subject = serializers.CharField(required=False, allow_blank=True)
    message = serializers.CharField(required=True, allow_blank=False)
    proposed_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    proposed_turnaround = serializers.CharField(required=False, allow_blank=True)
    proposed_quantity = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    proposed_material = serializers.CharField(required=False, allow_blank=True)
    proposed_gsm = serializers.CharField(required=False, allow_blank=True)
    proposed_size = serializers.CharField(required=False, allow_blank=True)
    proposed_finishing = serializers.JSONField(required=False)


class ClientResponseListItemSerializer(serializers.ModelSerializer):
    request_id = serializers.IntegerField(source="quote_request_id", read_only=True)
    currency = serializers.CharField(source="shop.currency", read_only=True)
    price = serializers.DecimalField(source="total", max_digits=12, decimal_places=2, read_only=True)
    status = serializers.SerializerMethodField()
    latest_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Quote
        fields = [
            "id",
            "request_id",
            "price",
            "currency",
            "turnaround_days",
            "turnaround_hours",
            "status",
            "latest_message",
            "unread_count",
            "created_at",
            "updated_at",
        ]

    def get_latest_message(self, obj):
        latest = obj.messages.order_by("-created_at", "-id").first()
        if not latest:
            return ""
        return latest.body

    def get_status(self, obj):
        return normalize_quote_response_status(obj.status)

    def get_unread_count(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return 0
        return obj.messages.filter(
            recipient=user,
            recipient_role=QuoteRequestMessage.RecipientRole.CLIENT,
            read_at__isnull=True,
        ).count()


class ClientQuoteItemDetailSerializer(serializers.ModelSerializer):
    """Nested item serializer for client-facing detail with production readout."""
    product_name = serializers.SerializerMethodField()
    finishings = serializers.SerializerMethodField()
    sheets_needed = serializers.SerializerMethodField()
    imposition_count = serializers.SerializerMethodField()
    finishing_summary = serializers.SerializerMethodField()
    production_description = serializers.SerializerMethodField()

    class Meta:
        model = QuoteItem
        fields = [
            "id",
            "item_type",
            "product_name",
            "title",
            "spec_text",
            "quantity",
            "chosen_width_mm",
            "chosen_height_mm",
            "sides",
            "color_mode",
            "special_instructions",
            "finishings",
            "sheets_needed",
            "imposition_count",
            "finishing_summary",
            "production_description",
        ]

    def get_product_name(self, obj):
        if obj.item_type == "PRODUCT" and obj.product_id:
            return obj.product.name
        return obj.title or ""

    def get_finishings(self, obj):
        finishings = []
        for finishing in obj.finishings.all().select_related("finishing_rate"):
            rate = getattr(finishing, "finishing_rate", None)
            label = getattr(rate, "name", "") or ""
            if rate is not None and hasattr(rate, "get_code_display"):
                try:
                    label = rate.get_code_display() or label
                except Exception:
                    label = label or ""
            finishings.append(
                {
                    "id": finishing.id,
                    "finishing_rate_name": label,
                    "selected_side": finishing.selected_side,
                }
            )
        return finishings

    def get_sheets_needed(self, obj):
        return (obj.pricing_snapshot or {}).get("sheets_needed")

    def get_imposition_count(self, obj):
        return (obj.pricing_snapshot or {}).get("imposition_count")

    def get_finishing_summary(self, obj):
        names = []
        for finishing in obj.finishings.all().select_related("finishing_rate"):
            rate = getattr(finishing, "finishing_rate", None)
            if rate is None:
                continue
            try:
                label = rate.get_code_display() if hasattr(rate, "get_code_display") else getattr(rate, "name", "")
            except Exception:
                label = getattr(rate, "name", "")
            if label:
                names.append(label)
        return ", ".join(names) if names else "None"

    def get_production_description(self, obj):
        return (obj.pricing_snapshot or {}).get("production_summary") or obj.spec_text


class ClientQuoteRequestDetailSerializer(serializers.ModelSerializer):
    """
    Comprehensive serializer for client-facing request detail page.
    Aggregates responses from all sibling requests (broadcast group).
    """

    shop_name = serializers.SerializerMethodField()
    shop_slug = serializers.SerializerMethodField()
    shop_currency = serializers.CharField(source="shop.currency", read_only=True)
    items = ClientQuoteItemDetailSerializer(many=True, read_only=True)
    attachments = QuoteRequestAttachmentSerializer(many=True, read_only=True)
    sibling_responses = serializers.SerializerMethodField()
    responses = serializers.SerializerMethodField()
    managed_job = serializers.SerializerMethodField()
    tracking_token = serializers.SerializerMethodField()
    public_token = serializers.SerializerMethodField()
    assigned_manager = serializers.SerializerMethodField()
    manager_selection_mode = serializers.CharField(read_only=True)
    delivery_location = serializers.SerializerMethodField()
    raw_status = serializers.CharField(source="status", read_only=True)
    status = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()

    class Meta:
        model = QuoteRequest
        fields = [
            "id",
            "request_reference",
            "shop",
            "shop_name",
            "shop_slug",
            "shop_currency",
            "status",
            "raw_status",
            "status_label",
            "customer_name",
            "customer_email",
            "customer_phone",
            "assigned_manager",
            "manager_selection_mode",
            "notes",
            "delivery_preference",
            "delivery_address",
            "delivery_location",
            "request_snapshot",
            "items",
            "attachments",
            "responses",
            "sibling_responses",
            "managed_job",
            "tracking_token",
            "public_token",
            "created_at",
            "updated_at",
        ]

    def get_status(self, obj):
        return normalize_quote_request_status(obj.status)

    def get_assigned_manager(self, obj):
        return _assigned_manager_payload(getattr(obj, "assigned_manager", None))

    def get_delivery_location(self, obj):
        snapshot = _as_dict(getattr(obj, "request_snapshot", None))
        delivery = _as_dict(snapshot.get("delivery"))
        location = (
            snapshot.get("delivery_location")
            or snapshot.get("delivery_location_name")
            or delivery.get("location")
            or delivery.get("location_name")
        )
        if isinstance(location, dict):
            return location.get("name") or location.get("label") or location
        return location

    def get_shop_name(self, obj):
        topology_mode = resolve_topology_mode_for_quote_request(obj)
        return project_client_counterparty_name(
            fallback_name=obj.shop.name if obj.shop_id else None,
            topology_mode=topology_mode,
            request_snapshot=obj.request_snapshot,
        )

    def get_shop_slug(self, obj):
        topology_mode = resolve_topology_mode_for_quote_request(obj)
        if topology_mode == "marketplace_legacy":
            return obj.shop.slug if obj.shop_id else ""
        return "partner"

    def get_status_label(self, obj):
        return quote_request_status_label(self.get_status(obj))

    def get_sibling_responses(self, obj):
        return self.get_responses(obj)

    def get_responses(self, obj):
        source_draft = obj.source_draft
        if not source_draft:
            siblings = [obj]
        else:
            siblings = source_draft.generated_requests.all().select_related("shop")

        responses = []
        for sib in siblings:
            # Only show responses that are sent/accepted/etc, not pending drafts
            latest = sib.quotes.exclude(status=QuoteOfferStatus.PENDING).order_by("-created_at").first()
            if latest:
                # We want the full QuoteResponseReadSerializer to give all the details requested
                responses.append(QuoteResponseReadSerializer(latest, context=self.context).data)
        return responses

    def get_managed_job(self, obj):
        managed_job = obj.managed_jobs.order_by("-id").first()
        if not managed_job:
            return None
        return {
            "id": managed_job.id,
            "status": managed_job.status,
            "payment_status": managed_job.payment_status,
            "assignment_status": managed_job.assignment_status,
            "client_total": str(managed_job.client_total) if managed_job.client_total is not None else None,
            "tracking_token": str(managed_job.tracking_token) if managed_job.tracking_token else None,
            "public_token": None,
        }

    def get_tracking_token(self, obj):
        managed_job = obj.managed_jobs.order_by("-id").first()
        if not managed_job or not managed_job.tracking_token:
            return None
        return str(managed_job.tracking_token)

    def get_public_token(self, obj):
        return None

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["request_snapshot"] = project_request_snapshot_for_client(instance.request_snapshot)
        return data


class RateWizardValueSerializer(serializers.Serializer):
    key = serializers.CharField()
    value = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)


class RateWizardStepActionSerializer(serializers.Serializer):
    shop_slug = serializers.CharField(required=False, allow_blank=True)
    step_key = serializers.CharField()
    quantity = serializers.IntegerField(required=False, min_value=1)
    values = RateWizardValueSerializer(many=True, required=False, default=list)


class PublicRateWizardPreviewSerializer(serializers.Serializer):
    preset_key = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)
    rates = serializers.DictField(child=serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True), required=False, default=dict)


class MvpRateCardPaperRowSerializer(serializers.Serializer):
    key = serializers.CharField(required=False, allow_blank=True)
    id = serializers.CharField(required=False, allow_blank=True)
    label = serializers.CharField(required=False, allow_blank=True)
    paper_name = serializers.CharField(required=False, allow_blank=True)
    gsm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    paper_type = serializers.CharField(required=False, allow_blank=True)
    category = serializers.CharField(required=False, allow_blank=True)
    size = serializers.CharField(required=False, allow_blank=True)
    paper_base_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    single_print_base = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    double_print_base = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    heavy_paper_surcharge = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    surcharge_threshold_gsm = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    single_side_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    double_side_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    active = serializers.BooleanField(required=False, default=False)


class MvpRateCardFinishingRowSerializer(serializers.Serializer):
    key = serializers.CharField(required=False, allow_blank=True)
    id = serializers.CharField(required=False, allow_blank=True)
    label = serializers.CharField(required=False, allow_blank=True)
    name = serializers.CharField(required=False, allow_blank=True)
    pricing_mode = serializers.CharField(required=False, allow_blank=True, default="flat_per_job")
    unit = serializers.CharField(required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    minimum_charge = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    active = serializers.BooleanField(required=False, default=False)


class MvpRateCardShopDetailsSerializer(serializers.Serializer):
    shop_name = serializers.CharField(required=False, allow_blank=True, default="")
    whatsapp_number = serializers.CharField(required=False, allow_blank=True, default="")
    location_area = serializers.CharField(required=False, allow_blank=True, default="")


class MvpRateCardPublicShopSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, default="")
    whatsapp = serializers.CharField(required=False, allow_blank=True, default="")
    location = serializers.CharField(required=False, allow_blank=True, default="")


LEGACY_MVP_FINISHING_KEY_ALIASES = {
    "matte_lamination": "matte_lamination_double",
    "gloss_lamination": "gloss_lamination_double",
}


def _normalize_mvp_finishing_rows(rows):
    normalized_rows = []
    for row in rows or []:
        current = dict(row or {})
        key = str(current.get("key") or "").strip()
        if key in LEGACY_MVP_FINISHING_KEY_ALIASES:
            current["key"] = LEGACY_MVP_FINISHING_KEY_ALIASES[key]
        normalized_rows.append(current)
    return normalized_rows


def _validate_mvp_rate_card_keys(*, rows, field_name: str, allowed_keys: set[str]):
    invalid_rows = []
    for idx, row in enumerate(rows or []):
        key = str((row or {}).get("key") or "").strip()
        if key and key not in allowed_keys:
            invalid_rows.append({idx: {"key": ["Unknown predefined product key."]}})
    if invalid_rows:
        raise serializers.ValidationError({field_name: invalid_rows})


class MvpRateCardPreviewSerializer(serializers.Serializer):
    paper_prices = MvpRateCardPaperRowSerializer(many=True, required=False, default=list)
    finishings = MvpRateCardFinishingRowSerializer(many=True, required=False, default=list)
    paper_rows = MvpRateCardPaperRowSerializer(many=True, required=False, default=list)
    finishing_rows = MvpRateCardFinishingRowSerializer(many=True, required=False, default=list)

    def validate(self, attrs):
        attrs["paper_rows"] = attrs.get("paper_prices") or attrs.get("paper_rows") or []
        attrs["finishing_rows"] = _normalize_mvp_finishing_rows(attrs.get("finishings") or attrs.get("finishing_rows") or [])
        _validate_mvp_rate_card_keys(
            rows=attrs["paper_rows"],
            field_name="paper_prices",
            allowed_keys=set(PAPER_DEFINITION_BY_KEY.keys()),
        )
        _validate_mvp_rate_card_keys(
            rows=attrs["finishing_rows"],
            field_name="finishings",
            allowed_keys=set(FINISHING_DEFINITION_BY_KEY.keys()),
        )
        return attrs


class MvpRateCardSetupSerializer(serializers.Serializer):
    paper_prices = MvpRateCardPaperRowSerializer(many=True, required=False, default=list)
    finishings = MvpRateCardFinishingRowSerializer(many=True, required=False, default=list)
    paper_rows = MvpRateCardPaperRowSerializer(many=True, required=False, default=list)
    finishing_rows = MvpRateCardFinishingRowSerializer(many=True, required=False, default=list)
    shop_details = MvpRateCardShopDetailsSerializer(required=False, default=dict)

    def validate(self, attrs):
        attrs["paper_rows"] = attrs.get("paper_prices") or attrs.get("paper_rows") or []
        attrs["finishing_rows"] = _normalize_mvp_finishing_rows(attrs.get("finishings") or attrs.get("finishing_rows") or [])
        _validate_mvp_rate_card_keys(
            rows=attrs["paper_rows"],
            field_name="paper_prices",
            allowed_keys=set(PAPER_DEFINITION_BY_KEY.keys()),
        )
        _validate_mvp_rate_card_keys(
            rows=attrs["finishing_rows"],
            field_name="finishings",
            allowed_keys=set(FINISHING_DEFINITION_BY_KEY.keys()),
        )
        return attrs


class MvpRateCardPublicSaveSerializer(serializers.Serializer):
    shop = MvpRateCardPublicShopSerializer(required=False, default=dict)
    paper_prices = MvpRateCardPaperRowSerializer(many=True, required=False, default=list)
    finishings = MvpRateCardFinishingRowSerializer(many=True, required=False, default=list)

    def validate(self, attrs):
        attrs["shop_details"] = {
            "shop_name": (attrs.get("shop") or {}).get("name", ""),
            "whatsapp_number": (attrs.get("shop") or {}).get("whatsapp", ""),
            "location_area": (attrs.get("shop") or {}).get("location", ""),
        }
        attrs["paper_rows"] = attrs.get("paper_prices") or []
        attrs["finishing_rows"] = _normalize_mvp_finishing_rows(attrs.get("finishings") or [])
        _validate_mvp_rate_card_keys(
            rows=attrs["paper_rows"],
            field_name="paper_prices",
            allowed_keys=set(PAPER_DEFINITION_BY_KEY.keys()),
        )
        _validate_mvp_rate_card_keys(
            rows=attrs["finishing_rows"],
            field_name="finishings",
            allowed_keys=set(FINISHING_DEFINITION_BY_KEY.keys()),
        )
        return attrs
