from __future__ import annotations

import re
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db.models import Q
from django.utils.text import slugify

from inventory.choices import PaperCategory
from inventory.models import Paper
from pricing.choices import Sides
from pricing.models import FinishingRate
from shops.models import Shop


DIGITAL_GSM_CHOICES = [60, 70, 80, 100, 115, 130, 150, 170, 200, 220, 250, 300, 350, 400]

COLOR_MODES = [
    {"value": "COLOR", "label": "Full color"},
    {"value": "BW", "label": "Black only"},
]

BOOKLET_COLOR_MODES = [
    {"value": "COLOR", "label": "Full color"},
    {"value": "BW", "label": "Black only"},
    {"value": "COVER_COLOR_INSERT_BW", "label": "Color cover, black inside"},
]

PRINT_SIDES = [
    {"value": Sides.SIMPLEX, "label": "Single sided"},
    {"value": Sides.DUPLEX, "label": "Double sided"},
]

SHAPES = [
    {"value": "rectangle", "label": "Rectangle"},
    {"value": "square", "label": "Square"},
    {"value": "circle", "label": "Circle"},
    {"value": "oval", "label": "Oval"},
    {"value": "custom", "label": "Custom"},
]

CUT_TYPES = [
    {"value": "kiss_cut", "label": "Kiss cut"},
    {"value": "die_cut", "label": "Die cut"},
    {"value": "straight_cut", "label": "Straight cut"},
]

SIZE_LIBRARY = {
    "business_card": [
        {"value": "90x55mm", "label": "90 x 55 mm", "width_mm": 90, "height_mm": 55},
        {"value": "85x55mm", "label": "85 x 55 mm", "width_mm": 85, "height_mm": 55},
    ],
    "flyer": [
        {"value": "A6", "label": "A6", "width_mm": 105, "height_mm": 148},
        {"value": "A5", "label": "A5", "width_mm": 148, "height_mm": 210},
        {"value": "DL", "label": "DL", "width_mm": 99, "height_mm": 210},
        {"value": "A4", "label": "A4", "width_mm": 210, "height_mm": 297},
        {"value": "A3", "label": "A3", "width_mm": 297, "height_mm": 420},
    ],
    "label_sticker": [
        {"value": "A6", "label": "A6", "width_mm": 105, "height_mm": 148},
        {"value": "A5", "label": "A5", "width_mm": 148, "height_mm": 210},
        {"value": "A4", "label": "A4", "width_mm": 210, "height_mm": 297},
        {"value": "100x50mm", "label": "100 x 50 mm", "width_mm": 100, "height_mm": 50},
    ],
    "letterhead": [
        {"value": "A4", "label": "A4", "width_mm": 210, "height_mm": 297},
        {"value": "A5", "label": "A5", "width_mm": 148, "height_mm": 210},
    ],
    "booklet": [
        {"value": "A5", "label": "A5", "width_mm": 148, "height_mm": 210},
        {"value": "A4", "label": "A4", "width_mm": 210, "height_mm": 297},
    ],
    "large_format": [
        {"value": "A4", "label": "A4", "width_mm": 210, "height_mm": 297},
        {"value": "A3", "label": "A3", "width_mm": 297, "height_mm": 420},
        {"value": "poster_500x700", "label": "Poster 500 x 700 mm", "width_mm": 500, "height_mm": 700},
        {"value": "banner_850x2000", "label": "Banner 850 x 2000 mm", "width_mm": 850, "height_mm": 2000},
    ],
}

LARGE_FORMAT_SUBTYPE_OPTIONS = [
    {"value": "banner", "label": "Banner"},
    {"value": "poster", "label": "Poster"},
    {"value": "sticker", "label": "Sticker"},
    {"value": "roll_up_banner", "label": "Roll-up Banner"},
    {"value": "mounted_board", "label": "Mounted Board"},
]


PRODUCT_DEFINITIONS = OrderedDict(
    [
        (
            "business_card",
            {
                "label": "Business Cards",
                "required_fields": ["quantity", "finished_size", "print_sides", "color_mode"],
                "optional_fields": ["requested_paper_category", "requested_gsm", "lamination", "corner_rounding"],
                "defaults": {
                    "quantity": 100,
                    "finished_size": "90x55mm",
                    "print_sides": "DUPLEX",
                    "color_mode": "COLOR",
                    "lamination": "none",
                    "corner_rounding": False,
                },
                "allowed_paper_categories": ["artcard", "matt", "gloss", "ivory", "special", "other"],
                "allowed_finishings": ["lamination", "corner_rounding"],
                "allowed_print_sides": ["SIMPLEX", "DUPLEX"],
            },
        ),
        (
            "flyer",
            {
                "label": "Flyers",
                "required_fields": ["quantity", "finished_size", "print_sides", "color_mode"],
                "optional_fields": ["requested_paper_category", "requested_gsm", "folding", "lamination"],
                "defaults": {
                    "quantity": 100,
                    "finished_size": "A5",
                    "print_sides": "SIMPLEX",
                    "color_mode": "COLOR",
                    "folding": "none",
                    "lamination": "none",
                },
                "allowed_paper_categories": ["matt", "gloss", "bond", "ivory", "special", "other"],
                "allowed_finishings": ["folding", "lamination"],
                "allowed_print_sides": ["SIMPLEX", "DUPLEX"],
            },
        ),
        (
            "label_sticker",
            {
                "label": "Label Stickers / Tictac",
                "required_fields": ["quantity", "finished_size", "shape", "cut_type", "color_mode"],
                "optional_fields": ["requested_paper_category", "requested_gsm", "lamination"],
                "defaults": {
                    "quantity": 100,
                    "finished_size": "100x50mm",
                    "shape": "rectangle",
                    "cut_type": "kiss_cut",
                    "color_mode": "COLOR",
                    "lamination": "none",
                },
                "allowed_paper_categories": ["tictac", "special", "other"],
                "allowed_finishings": ["lamination", "cutting"],
                "allowed_print_sides": ["SIMPLEX"],
            },
        ),
        (
            "letterhead",
            {
                "label": "Letterheads / Conqueror",
                "required_fields": ["quantity", "finished_size", "print_sides", "color_mode"],
                "optional_fields": ["requested_paper_category", "requested_gsm"],
                "defaults": {
                    "quantity": 100,
                    "finished_size": "A4",
                    "print_sides": "SIMPLEX",
                    "color_mode": "COLOR",
                },
                "allowed_paper_categories": ["bond", "conqueror", "ivory", "special", "other"],
                "allowed_finishings": [],
                "allowed_print_sides": ["SIMPLEX", "DUPLEX"],
            },
        ),
        (
            "booklet",
            {
                "label": "Booklets",
                "required_fields": ["quantity", "finished_size", "total_pages"],
                "optional_fields": [
                    "cover_lamination",
                    "binding_type",
                    "cutting",
                    "requested_cover_paper_category",
                    "requested_cover_gsm",
                    "requested_insert_paper_category",
                    "requested_insert_gsm",
                ],
                "defaults": {
                    "quantity": 100,
                    "finished_size": "A5",
                    "total_pages": 12,
                    "binding_type": "saddle_stitch",
                    "cover_lamination": "none",
                    "cutting": True,
                },
                "allowed_cover_categories": ["artcard", "matt", "gloss", "cover_board", "ivory", "conqueror", "special", "other"],
                "allowed_insert_categories": ["bond", "matt", "gloss", "ivory", "conqueror", "special", "other"],
                "allowed_finishings": ["cover_lamination", "stitching", "cutting", "binding"],
                "allowed_print_sides": ["DUPLEX"],
            },
        ),
        (
            "large_format",
            {
                "label": "Large Format",
                "required_fields": ["quantity", "material_type"],
                "optional_fields": ["product_subtype"],
                "defaults": {
                    "quantity": 1,
                    "product_subtype": "banner",
                    "finished_size": "banner_850x2000",
                    "input_unit": "mm",
                },
                "allowed_finishings": [],
                "allowed_print_sides": [],
            },
        ),
    ]
)


SIZE_OPTION_METADATA: dict[str, list[dict[str, Any]]] = {
    "business_card": [
        {"id": "90x55mm", "label": "Standard Card", "description": "Most common size", "recommended": True},
        {"id": "85x55mm", "label": "Euro Card", "description": "Slightly narrower", "recommended": False},
    ],
    "flyer": [
        {"id": "A6", "label": "A6", "description": "Quarter-page flyer", "recommended": False},
        {"id": "A5", "label": "A5", "description": "Half-page flyer", "recommended": True},
        {"id": "DL", "label": "DL", "description": "Long slim flyer", "recommended": False},
        {"id": "A4", "label": "A4", "description": "Full-page flyer", "recommended": False},
        {"id": "A3", "label": "A3", "description": "Large format flyer", "recommended": False},
    ],
    "label_sticker": [
        {"id": "100x50mm", "label": "100 × 50 mm", "description": "Standard label size", "recommended": True},
        {"id": "A6", "label": "A6", "description": "Small label sheet", "recommended": False},
        {"id": "A5", "label": "A5", "description": "Medium label sheet", "recommended": False},
        {"id": "A4", "label": "A4", "description": "Full sheet labels", "recommended": False},
    ],
    "letterhead": [
        {"id": "A4", "label": "A4", "description": "Standard letterhead", "recommended": True},
        {"id": "A5", "label": "A5", "description": "Half-page letterhead", "recommended": False},
    ],
    "booklet": [
        {"id": "A5", "label": "A5", "description": "Compact booklet", "recommended": True},
        {"id": "A4", "label": "A4", "description": "Full-size booklet", "recommended": False},
    ],
    "large_format": [
        {"id": "A4", "label": "A4", "description": "Small poster or notice", "recommended": False},
        {"id": "A3", "label": "A3", "description": "Indoor poster", "recommended": False},
        {"id": "poster_500x700", "label": "Poster", "description": "Standard poster size", "recommended": True},
        {"id": "banner_850x2000", "label": "Banner", "description": "Common pull-up banner size", "recommended": False},
    ],
}


def _size_options_for_product(product_key: str) -> list[dict[str, Any]]:
    meta_map = {m["id"]: m for m in SIZE_OPTION_METADATA.get(product_key, [])}
    options = []
    for size in SIZE_LIBRARY.get(product_key, []):
        value = size["value"]
        meta = meta_map.get(value, {})
        options.append({
            "id": value,
            "label": meta.get("label") or size["label"],
            "description": meta.get("description", ""),
            "recommended": meta.get("recommended", False),
            "width_mm": size["width_mm"],
            "height_mm": size["height_mm"],
        })
    return options


def _paper_queryset():
    public_shop_ids = Shop.objects.filter(is_active=True, is_public=True).values_list("id", flat=True)
    return Paper.objects.filter(shop_id__in=public_shop_ids, is_active=True, selling_price__gt=0)


def _paper_option_key(paper: Paper) -> str:
    return slugify(f"{paper.category}-{paper.marketplace_label}-{paper.gsm}") or f"paper-{paper.id}"


def _paper_option(paper: Paper) -> dict[str, Any]:
    return {
        "key": _paper_option_key(paper),
        "label": paper.marketplace_label,
        "display_name": paper.marketplace_label,
        "category": paper.category,
        "category_label": paper.category_label,
        "gsm": paper.gsm,
        "paper_type": paper.paper_type,
        "is_cover_stock": paper.supports_usage("cover"),
        "is_insert_stock": paper.supports_usage("insert"),
        "is_sticker_stock": paper.supports_usage("sticker"),
        "is_specialty": bool(paper.is_specialty),
    }


def _aggregate_paper_stocks() -> list[dict[str, Any]]:
    options: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for paper in _paper_queryset().order_by("category", "gsm", "display_name", "id"):
        option = _paper_option(paper)
        options.setdefault(option["key"], option)
    return list(options.values())


def _aggregate_material_types() -> list[dict[str, Any]]:
    return []


def _aggregate_finishings() -> list[dict[str, Any]]:
    rows: OrderedDict[str, dict[str, Any]] = OrderedDict()
    shop_ids = Shop.objects.filter(is_active=True, is_public=True).values_list("id", flat=True)
    queryset = (
        FinishingRate.objects.filter(shop_id__in=shop_ids, is_active=True)
        .order_by("slug", "name", "id")
    )
    for finishing in queryset:
        key = (finishing.slug or slugify(finishing.name or "")).strip()
        if not key:
            continue
        rows.setdefault(
            key,
            {
                "key": key,
                "label": finishing.name,
                "slug": key,
                "category": "",
                "help_text": finishing.help_text or "",
            },
        )
    return list(rows.values())


def _paper_categories() -> list[dict[str, Any]]:
    return [{"value": value, "label": label} for value, label in PaperCategory.choices]


def _field_definitions(
    product_key: str,
    definition: dict[str, Any],
    material_types: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if product_key == "booklet":
        return [
            {"key": "quantity", "label": "Quantity", "type": "number", "required": True, "help_text": "How many finished booklets do you need?"},
            {"key": "finished_size", "label": "Finished size", "type": "select", "required": True, "options": SIZE_LIBRARY["booklet"]},
            {"key": "total_pages", "label": "Total pages", "type": "number", "required": True, "help_text": "Include every page. Backend will normalize to a production-safe multiple of 4."},
            {"key": "requested_cover_paper_category", "label": "Requested cover paper", "type": "select", "required": False, "options": [item for item in _paper_categories() if item["value"] in definition["allowed_cover_categories"]], "help_text": "Optional cover paper preference. Shops price with their nearest available cover stock."},
            {"key": "requested_cover_gsm", "label": "Requested cover GSM", "type": "number", "required": False, "help_text": "Optional exact cover grammage request. Backend will match the nearest available stock if needed."},
            {"key": "requested_insert_paper_category", "label": "Requested insert paper", "type": "select", "required": False, "options": [item for item in _paper_categories() if item["value"] in definition["allowed_insert_categories"]], "help_text": "Optional insert paper preference. Shops price with their nearest available insert stock."},
            {"key": "requested_insert_gsm", "label": "Requested insert GSM", "type": "number", "required": False, "help_text": "Optional exact insert grammage request. Backend will match the nearest available stock if needed."},
            {"key": "cover_lamination", "label": "Cover lamination", "type": "select", "required": False, "options": [{"value": "none", "label": "No lamination"}, {"value": "front", "label": "Front only"}, {"value": "both", "label": "Both sides"}], "help_text": "Lamination is applied to cover sheets only."},
            {"key": "binding_type", "label": "Binding", "type": "select", "required": False, "options": [{"value": "saddle_stitch", "label": "Saddle stitch"}, {"value": "perfect_bind", "label": "Perfect bind"}, {"value": "wire_o", "label": "Wire-O"}]},
            {"key": "cutting", "label": "Cutting", "type": "boolean", "required": False, "help_text": "Backend applies cutting only when the shop has a matching finishing path."},
        ]

    if product_key == "large_format":
        return [
            {"key": "quantity", "label": "How many pieces?", "type": "number", "required": True},
            {"key": "material_type", "label": "Material", "type": "select", "required": True, "options": material_types},
            {"key": "product_subtype", "label": "Product style", "type": "select", "required": False, "options": LARGE_FORMAT_SUBTYPE_OPTIONS},
        ]

    fields = [
        {"key": "quantity", "label": "Quantity", "type": "number", "required": True},
        {"key": "finished_size", "label": "Finished size", "type": "select", "required": True, "options": SIZE_LIBRARY[product_key]},
        {"key": "requested_paper_category", "label": "Requested paper", "type": "select", "required": False, "options": [item for item in _paper_categories() if item["value"] in definition["allowed_paper_categories"]], "help_text": "Optional paper preference. Shops price with their nearest available stock."},
        {"key": "requested_gsm", "label": "Requested GSM", "type": "number", "required": False, "help_text": "Optional exact grammage request. Backend will match the nearest available stock if needed."},
        {"key": "color_mode", "label": "Color mode", "type": "select", "required": True, "options": COLOR_MODES},
    ]
    if definition["allowed_print_sides"]:
        fields.append({"key": "print_sides", "label": "Print sides", "type": "select", "required": "print_sides" in definition["required_fields"], "options": [item for item in PRINT_SIDES if item["value"] in definition["allowed_print_sides"]]})
    if product_key == "business_card":
        fields.extend(
            [
                {"key": "lamination", "label": "Lamination", "type": "select", "required": False, "options": [{"value": "none", "label": "No lamination"}, {"value": "gloss-lamination", "label": "Gloss lamination"}, {"value": "matt-lamination", "label": "Matt lamination"}]},
                {"key": "corner_rounding", "label": "Corner rounding", "type": "boolean", "required": False},
            ]
        )
    elif product_key == "flyer":
        fields.extend(
            [
                {"key": "folding", "label": "Folding", "type": "select", "required": False, "options": [{"value": "none", "label": "No folding"}, {"value": "half-fold", "label": "Half fold"}, {"value": "tri-fold", "label": "Tri-fold"}]},
                {"key": "lamination", "label": "Lamination", "type": "select", "required": False, "options": [{"value": "none", "label": "No lamination"}, {"value": "gloss-lamination", "label": "Gloss lamination"}, {"value": "matt-lamination", "label": "Matt lamination"}]},
            ]
        )
    elif product_key == "label_sticker":
        fields.extend(
            [
                {"key": "shape", "label": "Shape", "type": "select", "required": True, "options": SHAPES},
                {"key": "cut_type", "label": "Cut type", "type": "select", "required": True, "options": CUT_TYPES},
                {"key": "lamination", "label": "Lamination", "type": "select", "required": False, "options": [{"value": "none", "label": "No lamination"}, {"value": "gloss-lamination", "label": "Gloss lamination"}, {"value": "matt-lamination", "label": "Matt lamination"}]},
            ]
        )
    return fields


def get_calculator_config() -> dict[str, Any]:
    material_types = _aggregate_material_types()
    finishings = _aggregate_finishings()
    products = []
    for product_key, definition in PRODUCT_DEFINITIONS.items():
        fields = _field_definitions(product_key, definition, material_types)
        products.append(
            {
                "key": product_key,
                "label": definition["label"],
                "required_fields": definition["required_fields"],
                "optional_fields": definition["optional_fields"],
                "defaults": definition["defaults"],
                "allowed_paper_categories": definition.get("allowed_paper_categories", []),
                "allowed_cover_categories": definition.get("allowed_cover_categories", []),
                "allowed_insert_categories": definition.get("allowed_insert_categories", []),
                "allowed_finishings": definition["allowed_finishings"],
                "allowed_print_sides": definition["allowed_print_sides"],
                "sizes": SIZE_LIBRARY[product_key],
                "fields": fields,
                "size_options": _size_options_for_product(product_key),
                "allow_custom_size": True,
                "allow_custom_paper_request": product_key != "large_format",
                "color_mode_options": BOOKLET_COLOR_MODES if product_key == "booklet" else ([] if product_key == "large_format" else COLOR_MODES),
            }
        )
    return {
        "products": products,
        "paper_categories": _paper_categories(),
        "finishings": finishings,
        "sizes": SIZE_LIBRARY,
        "print_sides": PRINT_SIDES,
        "color_modes": COLOR_MODES,
        "preview_endpoint": "/api/calculator/public-preview/",
    }


def get_product_definition(product_type: str) -> dict[str, Any] | None:
    return PRODUCT_DEFINITIONS.get(product_type)


def resolve_finished_size(product_type: str, finished_size: str | None) -> dict[str, Any] | None:
    value = (finished_size or "").strip()
    if not value:
        return None
    for option in SIZE_LIBRARY.get(product_type, []):
        if option["value"] == value:
            return option
    return None


# Tokens a client may send to mean "price the dimensions I supplied". The
# authoritative frontend sends finished_size="custom" together with
# width_mm/height_mm; these are the tolerated spellings of the same intent.
CUSTOM_SIZE_TOKENS = {"custom", "custom_size", "custom-size", "customsize"}

# A dimension string is a finished size expressed as millimetres, not a library
# size: "123x217mm", "106.9x148.9", "106.9 x 148.9 mm". The pattern is anchored on
# both ends so an arbitrary label ("Premium Matt Card") can never be read as a
# dimension, and only "x"/"X"/multiplication-sign separators are accepted.
_DIMENSION_STRING = re.compile(
    r"^(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:mm)?$",
    re.IGNORECASE,
)


def _positive_mm(value: Any) -> Decimal | None:
    """Coerce a millimetre value to a positive Decimal, keeping its precision."""
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (ArithmeticError, InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    return parsed


def parse_custom_size_dimensions(value: Any) -> tuple[Decimal, Decimal] | None:
    """Parse a "123x217mm" style string into (width_mm, height_mm).

    Returns None when the string is not a dimension string, so callers can tell
    "not a custom dimension" apart from "a custom dimension with bad numbers".
    """
    text = str(value or "").strip()
    if not text:
        return None
    match = _DIMENSION_STRING.match(text)
    if not match:
        return None
    width = _positive_mm(match.group(1))
    height = _positive_mm(match.group(2))
    if width is None or height is None:
        return None
    return width, height


def looks_like_dimension_string(value: Any) -> bool:
    """True when the string is dimension-shaped, valid numbers or not.

    Lets "0x0mm" be reported as a bad custom size rather than as an unknown
    finished size, without accepting it as a usable dimension.
    """
    return bool(_DIMENSION_STRING.match(str(value or "").strip()))


def resolve_request_finished_size(
    product_type: str,
    finished_size: str | None,
    *,
    width_mm: Any = None,
    height_mm: Any = None,
    size_mode: str | None = None,
) -> tuple[dict[str, Any] | None, bool]:
    """Resolve a calculator size request to (size, is_custom).

    This is the single explicit contract for "which finished size did the buyer
    ask for", so callers never have to guess whether a string is a library size
    or a custom dimension:

    1. A string that is a real entry in ``SIZE_LIBRARY`` is that predefined size.
    2. Otherwise explicit ``width_mm``/``height_mm`` win, which is the intended
       custom representation (``finished_size="custom"``, ``size_mode="custom"``,
       or the dimension string that matching synthesises from those dimensions).
    3. Otherwise a dimension string is parsed as custom millimetres.
    4. Otherwise the request names a custom size it did not supply dimensions
       for -- return (None, True) so the caller can ask for the dimensions.
    5. Anything else is not a size at all -- return (None, False) so the caller
       reports the size as unavailable rather than inventing a size.

    Dimensions stay ``Decimal`` so no precision is lost on the way to the
    imposition maths.
    """
    value = (finished_size or "").strip()
    mode = (size_mode or "").strip().lower()

    # 1. A genuine library size always wins, so "A5"/"85x55mm" keep working even
    #    when the payload also carries dimensions.
    if value:
        predefined = resolve_finished_size(product_type, value)
        if predefined:
            return predefined, False

    # 2. Explicit dimensions.
    explicit_width = _positive_mm(width_mm)
    explicit_height = _positive_mm(height_mm)
    if explicit_width is not None and explicit_height is not None:
        return {"width_mm": explicit_width, "height_mm": explicit_height}, True

    # 3. A dimension string supplied instead of separate fields.
    parsed = parse_custom_size_dimensions(value)
    if parsed:
        return {"width_mm": parsed[0], "height_mm": parsed[1]}, True

    # 4. Custom was requested but no usable dimensions arrived. A
    #    dimension-shaped string with unusable numbers ("0x0mm") counts here too,
    #    so it is reported as a bad custom size rather than an unknown size.
    #    ``size_mode`` is only trusted when no size string was sent at all: it is
    #    frequently inferred as "custom" from an absent ``size_label``, so an
    #    unrecognised non-empty string must not be mistaken for a custom request.
    if value.lower() in CUSTOM_SIZE_TOKENS or looks_like_dimension_string(value):
        return None, True
    if not value and mode == "custom":
        return None, True

    # 5. Not a size we recognise. Never coerce this into a library size.
    return None, False


def resolve_stock_option(stock_key: str | None, usage: str = "") -> dict[str, Any] | None:
    key = (stock_key or "").strip()
    if not key:
        return None
    for option in _aggregate_paper_stocks():
        if option["key"] == key:
            if usage == "cover" and not option["is_cover_stock"]:
                return None
            if usage == "insert" and not option["is_insert_stock"]:
                return None
            if usage == "sticker" and not option["is_sticker_stock"]:
                return None
            return option
    return None
