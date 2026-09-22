"""React-friendly input serializers for the public Printy quote calculator.

These serializers translate presentation keys from the React prototype into
the normalized payload already consumed by services.pricing.calculator_preview.
Pricing is deliberately not calculated here.
"""

from __future__ import annotations

from decimal import Decimal

from rest_framework import serializers

from api.spec_choice_fields import ColorModeField, PrintSidesField


PRODUCT_ALIASES = {
    "business-cards": "business_card",
    "business_card": "business_card",
    "flyers": "flyer",
    "flyer": "flyer",
    "brochures": "flyer",
    "booklets": "booklet",
    "booklet": "booklet",
    "stickers": "label_sticker",
    "label_sticker": "label_sticker",
    "letterheads": "letterhead",
    "letterhead": "letterhead",
    "banners": "large_format",
    "large_format": "large_format",
}

PRODUCT_FAMILIES = {
    "business_card": "flat",
    "flyer": "flat",
    "booklet": "booklet",
    "label_sticker": "flat",
    "letterhead": "flat",
    "large_format": "large_format",
}

SIZE_PRESETS = {
    "standard": (90, 55, "90x55mm"),
    "square": (55, 55, "55x55mm"),
    "slim": (85, 40, "85x40mm"),
    "a6": (105, 148, "A6"),
    "a5": (148, 210, "A5"),
    "a4": (210, 297, "A4"),
    "a3": (297, 420, "A3"),
    "dl": (99, 210, "DL"),
    "a4dl": (210, 297, "A4 to DL"),
    "a3a4": (297, 420, "A3 to A4"),
    "s50": (50, 50, "50x50mm"),
    "s75": (75, 75, "75x75mm"),
    "s100": (100, 100, "100x100mm"),
    "b1": (1000, 2000, "1x2m"),
    "b2": (2000, 1000, "2x1m"),
    "b3": (3000, 1000, "3x1m"),
    "b4": (800, 2000, "0.8x2m"),
}

# Prototype paper keys are accepted for a gradual migration. Production React
# should prefer paper_id values returned by GET /client-calculator/config/.
PAPER_ALIASES = {
    "bond80": ("bond", 80),
    "matt100": ("matt", 100),
    "gloss130": ("gloss", 130),
    "silk170": ("matt", 170),
    "board250": ("artcard", 250),
    "board300": ("artcard", 300),
    "board350": ("artcard", 350),
    "kraft400": ("special", 400),
}

MATERIAL_ALIASES = {
    "frontlit": "frontlit_banner",
    "mesh": "mesh_banner",
    "vinyl": "self_adhesive_vinyl",
    "canvas": "canvas",
}

FINISHING_ALIASES = {
    "lam-matt": "lamination",
    "lam-gloss": "lamination",
    "spot-uv": "spot_uv",
    "foil": "foiling",
    "diecut": "cutting",
    "roundcorner": "corner_rounding",
    "crease": "folding",
    "perf": "perforation",
    "drill": "drilling",
    "number": "numbering",
    "saddle": "stitching",
    "pur": "binding",
    "wiro": "binding",
    "shrink": "shrink_wrapping",
    "hem-eyelet": "hemming_eyelets",
    "pole": "pole_pockets",
}

URGENCY_ALIASES = {
    "standard": "standard",
    "48": "express",
    "priority_48": "express",
    "24": "same_day",
    "rush_24": "same_day",
}


class ClientFinishingSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, min_value=1)
    slug = serializers.CharField(required=False, allow_blank=False, max_length=80)
    selected_side = serializers.ChoiceField(
        choices=("front", "back", "both"), default="both"
    )

    def validate(self, attrs):
        if not attrs.get("id") and not attrs.get("slug"):
            raise serializers.ValidationError("Provide a finishing id or slug.")
        return attrs


class ClientCalculatorInputSerializer(serializers.Serializer):
    """The stable contract used by the React quote calculator."""

    product_type = serializers.ChoiceField(choices=tuple(PRODUCT_ALIASES))
    quantity = serializers.IntegerField(min_value=1, max_value=1_000_000)

    size_id = serializers.CharField(required=False, allow_blank=True, max_length=40)
    width_mm = serializers.DecimalField(
        required=False, allow_null=True, min_value=Decimal("1"),
        max_value=Decimal("20000"), max_digits=10, decimal_places=2,
    )
    height_mm = serializers.DecimalField(
        required=False, allow_null=True, min_value=Decimal("1"),
        max_value=Decimal("20000"), max_digits=10, decimal_places=2,
    )
    pages = serializers.IntegerField(required=False, min_value=4, max_value=1000)

    paper_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    paper_key = serializers.CharField(required=False, allow_blank=True, max_length=80)
    paper_type = serializers.CharField(required=False, allow_blank=True, max_length=80)
    paper_gsm = serializers.IntegerField(required=False, allow_null=True, min_value=40, max_value=1000)
    material_id = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    material_key = serializers.CharField(required=False, allow_blank=True, max_length=80)

    color_mode = ColorModeField(default="COLOR")
    sides = PrintSidesField(default="SIMPLEX")
    finishings = ClientFinishingSerializer(many=True, required=False, default=list)
    finishing_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, default=list,
    )
    finishing_slugs = serializers.ListField(
        child=serializers.CharField(max_length=80), required=False, default=list,
    )

    artwork_service = serializers.ChoiceField(
        choices=("print_ready", "file_fix", "full_design"), default="print_ready"
    )
    delivery_method = serializers.ChoiceField(
        choices=("pickup", "nairobi_cbd", "nairobi_metro", "upcountry"),
        default="pickup",
    )
    turnaround_tier = serializers.ChoiceField(
        choices=tuple(URGENCY_ALIASES), default="standard"
    )
    location_slug = serializers.SlugField(required=False, allow_blank=True)
    title = serializers.CharField(required=False, allow_blank=True, max_length=255)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    include_quantity_breaks = serializers.BooleanField(default=True)

    def to_internal_value(self, data):
        """Accept both the API contract and the prototype's CalcInput object."""

        if not isinstance(data, dict):
            return super().to_internal_value(data)

        normalized = data.copy()
        aliases = {
            "productId": "product_type",
            "sizeId": "size_id",
            "customW": "width_mm",
            "customH": "height_mm",
            "colorMode": "color_mode",
            "designId": "artwork_service",
            "deliveryId": "delivery_method",
            "rushId": "turnaround_tier",
            "includeQuantityBreaks": "include_quantity_breaks",
        }
        for source, target in aliases.items():
            if source in normalized and target not in normalized:
                normalized[target] = normalized[source]

        design_aliases = {
            "none": "print_ready",
            "tidy": "file_fix",
            "full": "full_design",
        }
        delivery_aliases = {
            "pickup": "pickup",
            "cbd": "nairobi_cbd",
            "metro": "nairobi_metro",
            "upcountry": "upcountry",
        }
        if normalized.get("artwork_service") in design_aliases:
            normalized["artwork_service"] = design_aliases[normalized["artwork_service"]]
        if normalized.get("delivery_method") in delivery_aliases:
            normalized["delivery_method"] = delivery_aliases[normalized["delivery_method"]]

        # The prototype uses one paperId property for paper and large-format
        # material keys. Database integer ids remain supported by the API form.
        prototype_paper = normalized.get("paperId")
        if prototype_paper not in (None, "") and "paper_id" not in normalized:
            try:
                normalized["paper_id"] = int(prototype_paper)
            except (TypeError, ValueError):
                product_key = normalized.get("product_type")
                if PRODUCT_ALIASES.get(product_key) == "large_format":
                    normalized.setdefault("material_key", str(prototype_paper))
                else:
                    normalized.setdefault("paper_key", str(prototype_paper))

        # finishingIds is an array of prototype slugs today, but accepting
        # numeric ids here makes the transition to config-driven choices easy.
        prototype_finishings = normalized.get("finishingIds")
        if isinstance(prototype_finishings, (list, tuple)):
            numeric, slugs = [], []
            for value in prototype_finishings:
                try:
                    numeric.append(int(value))
                except (TypeError, ValueError):
                    slugs.append(str(value))
            if numeric and "finishing_ids" not in normalized:
                normalized["finishing_ids"] = numeric
            if slugs and "finishing_slugs" not in normalized:
                normalized["finishing_slugs"] = slugs

        return super().to_internal_value(normalized)

    def validate(self, attrs):
        product_type = PRODUCT_ALIASES[attrs["product_type"]]
        size_id = str(attrs.get("size_id") or "").lower()
        preset = SIZE_PRESETS.get(size_id)

        if not preset and (not attrs.get("width_mm") or not attrs.get("height_mm")):
            raise serializers.ValidationError(
                {"size_id": "Choose a known size or provide width_mm and height_mm."}
            )

        if bool(attrs.get("width_mm")) != bool(attrs.get("height_mm")):
            raise serializers.ValidationError(
                {"width_mm": "Custom width and height must be provided together."}
            )

        if product_type == "booklet":
            pages = attrs.get("pages")
            if not pages:
                raise serializers.ValidationError({"pages": "Pages are required for booklets."})
            if pages % 4:
                raise serializers.ValidationError(
                    {"pages": "Booklet pages must be divisible by four."}
                )
            attrs["sides"] = "DUPLEX"

        if product_type == "label_sticker":
            attrs["sides"] = "SIMPLEX"

        if product_type == "large_format" and not (
            attrs.get("material_id") or attrs.get("material_key")
        ):
            raise serializers.ValidationError(
                {"material_key": "Select a large-format material."}
            )

        return attrs

    def to_engine_payload(self) -> dict:
        """Return the contract consumed by Printy's canonical pricing service."""

        data = self.validated_data
        product_type = PRODUCT_ALIASES[data["product_type"]]
        family = PRODUCT_FAMILIES[product_type]
        size_id = str(data.get("size_id") or "").lower()
        preset = SIZE_PRESETS.get(size_id)

        if data.get("width_mm") and data.get("height_mm"):
            width_mm = int(data["width_mm"])
            height_mm = int(data["height_mm"])
            size_label = f"{width_mm}x{height_mm}mm"
            size_mode = "custom"
        else:
            width_mm, height_mm, size_label = preset
            size_mode = "standard"

        paper_type = data.get("paper_type") or ""
        paper_gsm = data.get("paper_gsm")
        paper_key = data.get("paper_key") or ""
        if paper_key in PAPER_ALIASES:
            alias_type, alias_gsm = PAPER_ALIASES[paper_key]
            paper_type = paper_type or alias_type
            paper_gsm = paper_gsm or alias_gsm

        material_key = data.get("material_key") or ""
        finishing_ids = list(data.get("finishing_ids") or [])
        finishing_slugs = [
            FINISHING_ALIASES.get(value, value)
            for value in data.get("finishing_slugs") or []
        ]
        finishing_rows = []
        for item in data.get("finishings") or []:
            row = {"selected_side": item.get("selected_side", "both")}
            if item.get("id"):
                row["finishing_rate_id"] = item["id"]
                finishing_ids.append(item["id"])
            else:
                row["slug"] = FINISHING_ALIASES.get(item["slug"], item["slug"])
                finishing_slugs.append(row["slug"])
            finishing_rows.append(row)

        urgency = URGENCY_ALIASES[data["turnaround_tier"]]
        title = data.get("title") or product_type.replace("_", " ").title()
        brief = data.get("notes") or ""

        return {
            "calculator_mode": "marketplace",
            "shop_scope": "marketplace",
            "pricing_mode": "custom",
            "job_type": product_type,
            "product_type": product_type,
            "product_family": family,
            "product_pricing_mode": "LARGE_FORMAT" if family == "large_format" else "SHEET",
            "quantity": data["quantity"],
            "size_mode": size_mode,
            "size_label": size_label,
            "input_unit": "mm",
            "width_input": str(width_mm),
            "height_input": str(height_mm),
            "width_mm": width_mm,
            "height_mm": height_mm,
            "print_sides": data["sides"],
            "colour_mode": data["color_mode"],
            "paper_id": data.get("paper_id"),
            "paper_type": paper_type,
            "paper_gsm": paper_gsm,
            "material_id": data.get("material_id"),
            "material_type": MATERIAL_ALIASES.get(material_key, material_key),
            "finishing_ids": sorted(set(finishing_ids)),
            "finishing_slugs": sorted(set(finishing_slugs)),
            "finishings": finishing_rows,
            "total_pages": data.get("pages"),
            "urgency_type": urgency,
            "turnaround_mode": "standard" if urgency == "standard" else "rush",
            "custom_title": title,
            "custom_brief": brief,
            "location_slug": data.get("location_slug") or "",
            # Services which require a human or route decision stay in the
            # request snapshot. They are not silently invented as rate lines.
            "artwork_service": data["artwork_service"],
            "delivery_method": data["delivery_method"],
        }


class ClientCalculatorDraftSerializer(ClientCalculatorInputSerializer):
    guest_session_key = serializers.CharField(
        required=False, allow_blank=True, max_length=128
    )
    customer_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    customer_email = serializers.EmailField(required=False, allow_blank=True)
    customer_phone = serializers.CharField(required=False, allow_blank=True, max_length=50)
    delivery_address = serializers.CharField(required=False, allow_blank=True, max_length=1000)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        is_authenticated = bool(
            request and getattr(request.user, "is_authenticated", False)
        )
        if not is_authenticated and not (
            attrs.get("customer_email") or attrs.get("customer_phone")
        ):
            raise serializers.ValidationError(
                {"customer_email": "Email or phone is required to save a guest quote."}
            )
        return attrs