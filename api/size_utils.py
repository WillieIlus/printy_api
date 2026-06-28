from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from rest_framework import serializers


SIZE_PRESETS = {
    "Business Card": {"width_mm": 85, "height_mm": 55},
    "A6": {"width_mm": 105, "height_mm": 148},
    "A5": {"width_mm": 148, "height_mm": 210},
    "A4": {"width_mm": 210, "height_mm": 297},
    "A3": {"width_mm": 297, "height_mm": 420},
    "Letter": {"width_mm": 216, "height_mm": 279},
    "Legal": {"width_mm": 216, "height_mm": 356},
}

_PRESET_NAME_MAP = {label.lower(): label for label in SIZE_PRESETS}
_UNIT_FACTORS = {
    "mm": Decimal("1"),
    "cm": Decimal("10"),
    "m": Decimal("1000"),
    "in": Decimal("25.4"),
}


def _parse_decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _to_mm_integer(value, unit: str) -> int | None:
    parsed = _parse_decimal(value)
    factor = _UNIT_FACTORS.get(unit or "mm", _UNIT_FACTORS["mm"])
    if parsed is None:
        return None
    millimetres = (parsed * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(millimetres) if millimetres > 0 else None


def normalize_size_payload(
    data,
    *,
    legacy_width_keys: tuple[str, ...] = (),
    legacy_height_keys: tuple[str, ...] = (),
):
    if not isinstance(data, dict):
        return data

    normalized = dict(data)

    for key in legacy_width_keys:
        if key in normalized and "width_mm" not in normalized:
            normalized["width_mm"] = normalized[key]
            break
    for key in legacy_height_keys:
        if key in normalized and "height_mm" not in normalized:
            normalized["height_mm"] = normalized[key]
            break

    size_label = str(normalized.get("size_label") or "").strip()
    canonical_label = _PRESET_NAME_MAP.get(size_label.lower(), "") if size_label else ""
    if canonical_label:
        normalized["size_label"] = canonical_label

    size_mode = str(normalized.get("size_mode") or "").strip().lower()
    if size_mode not in {"standard", "custom"}:
        size_mode = "standard" if canonical_label else "custom"
    normalized["size_mode"] = size_mode

    input_unit = str(normalized.get("input_unit") or "mm").strip().lower()
    if input_unit not in _UNIT_FACTORS:
        input_unit = "mm"
    normalized["input_unit"] = input_unit

    preset = SIZE_PRESETS.get(canonical_label) if canonical_label else None
    if size_mode == "standard" and preset:
        if normalized.get("width_mm") in (None, ""):
            normalized["width_mm"] = preset["width_mm"]
        if normalized.get("height_mm") in (None, ""):
            normalized["height_mm"] = preset["height_mm"]
    else:
        if normalized.get("width_mm") in (None, ""):
            converted_width = _to_mm_integer(normalized.get("width_input"), input_unit)
            if converted_width is not None:
                normalized["width_mm"] = converted_width
        if normalized.get("height_mm") in (None, ""):
            converted_height = _to_mm_integer(normalized.get("height_input"), input_unit)
            if converted_height is not None:
                normalized["height_mm"] = converted_height

    return normalized


def validate_size_selection(attrs):
    size_mode = attrs.get("size_mode") or "custom"
    size_label = attrs.get("size_label") or ""

    if size_mode == "standard" and size_label and size_label not in SIZE_PRESETS:
        raise serializers.ValidationError({"size_label": ["Choose a supported standard size."]})

    return attrs
