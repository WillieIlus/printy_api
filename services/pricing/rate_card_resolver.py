"""The single rate-card resolver (law 11.3).

Every derivation rule for the printer rate card lives in this module and
nowhere else. Preview, save and the pricing engine all consume the values
produced here so the table a printer sees is exactly what a quote costs.

Rules (law 11.3):
- Master inputs: one-side print price (preset 15); both-sides print price
  (blank means 2 x one-side); heavy-stock surcharge (preset 10) with a
  heavy-stock threshold (preset 250 gsm); gloss/matt lamination first-side
  prices (5 / 15); lamination second-side multiplier (2).
- Per paper row: paper cost, gsm, double-sided availability and optional
  overrides for one-side print, both-sides print and surcharge. Precedence:
  row override, then master, then default.
- Surcharge: row override, else the master surcharge when gsm is at or
  above the threshold, else 0.
- total one side = paper cost + one-side print + applied surcharge;
  total both sides = paper cost + both-sides print + applied surcharge, or
  None (N/A) when double-sided is not available.
- Finishing: per-unit services charge the greater of (quantity x rate) and
  the minimum; flat services charge the flat rate once when quantity is
  above zero; "no minimum" means 0. Lamination one side equals the master
  first-side price; lamination both sides equals first side x (1 +
  multiplier). Each row can be overridden.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from rest_framework.exceptions import ValidationError


DEFAULT_MASTER_SETTINGS: dict[str, Any] = {
    "single_print": "15.00",
    "double_print": None,
    "surcharge": "10.00",
    "surcharge_threshold_gsm": 250,
    "gloss_first_side": "5.00",
    "matt_first_side": "15.00",
    "lamination_second_multiplier": "2",
}

# Per-row default print/surcharge values shipped with the preset table. A
# stored row value equal to the preset (or to the resolved master value) is
# treated as "still automatic"; only a value that differs from both is kept
# as a deliberate override. This keeps every pre-existing saved rate card
# resolving exactly as before while master edits flow through.
DEFAULT_ROW_SINGLE_PRINT = Decimal("15.00")
DEFAULT_ROW_DOUBLE_PRINT = Decimal("30.00")
DEFAULT_ROW_SURCHARGE = Decimal("10.00")

LAMINATION_ROW_KINDS: dict[str, tuple[str, bool]] = {
    "gloss_lamination_single": ("gloss", False),
    "gloss_lamination_double": ("gloss", True),
    "matte_lamination_single": ("matt", False),
    "matte_lamination_double": ("matt", True),
}


def _decimal(value: Any, *, allow_null: bool = False, label: str = "value") -> Decimal | None:
    if value in (None, ""):
        if allow_null:
            return None
        raise ValidationError(f"A {label} is required.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError(f"Enter a valid {label}.") from exc
    if amount < 0:
        raise ValidationError(f"{label.capitalize()} cannot be negative.")
    return amount.quantize(Decimal("0.01"))


def _decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01")))


def _text(value: Any) -> str:
    return str(value or "").strip()


def resolve_master_settings(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalise master settings, filling every missing input with the preset."""
    source = raw or {}
    single_print = _decimal(
        source.get("single_print", DEFAULT_MASTER_SETTINGS["single_print"]),
        label="print price",
    )
    raw_double = source.get("double_print")
    double_print: Decimal | None
    if raw_double in (None, ""):
        double_print = None
    else:
        double_print = _decimal(raw_double, label="both-sides print price")
    threshold_raw = source.get(
        "surcharge_threshold_gsm",
        DEFAULT_MASTER_SETTINGS["surcharge_threshold_gsm"],
    )
    try:
        threshold = int(threshold_raw) if threshold_raw not in (None, "") else DEFAULT_SURCHARGE_THRESHOLD()
    except (TypeError, ValueError) as exc:
        raise ValidationError({"master_settings": {"surcharge_threshold_gsm": ["Enter a whole GSM value."]}}) from exc
    if threshold < 1:
        raise ValidationError({"master_settings": {"surcharge_threshold_gsm": ["GSM must be at least 1."]}})
    multiplier_raw = source.get(
        "lamination_second_multiplier",
        DEFAULT_MASTER_SETTINGS["lamination_second_multiplier"],
    )
    try:
        multiplier = Decimal(str(multiplier_raw if multiplier_raw not in (None, "") else "2"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"master_settings": {"lamination_second_multiplier": ["Enter a valid multiplier."]}}) from exc
    if multiplier < 0:
        raise ValidationError({"master_settings": {"lamination_second_multiplier": ["Multiplier cannot be negative."]}})
    return {
        "single_print": _decimal_string(single_print),
        "double_print": _decimal_string(double_print),
        "surcharge": _decimal_string(
            _decimal(source.get("surcharge", DEFAULT_MASTER_SETTINGS["surcharge"]), label="surcharge")
        ),
        "surcharge_threshold_gsm": threshold,
        "gloss_first_side": _decimal_string(
            _decimal(
                source.get("gloss_first_side", DEFAULT_MASTER_SETTINGS["gloss_first_side"]),
                label="gloss first-side price",
            )
        ),
        "matt_first_side": _decimal_string(
            _decimal(
                source.get("matt_first_side", DEFAULT_MASTER_SETTINGS["matt_first_side"]),
                label="matt first-side price",
            )
        ),
        "lamination_second_multiplier": _decimal_string(multiplier),
    }


def DEFAULT_SURCHARGE_THRESHOLD() -> int:
    return int(DEFAULT_MASTER_SETTINGS["surcharge_threshold_gsm"])


def _master_decimals(master: dict[str, Any]) -> dict[str, Any]:
    resolved = resolve_master_settings(master)
    return {
        "single_print": Decimal(resolved["single_print"]),
        "double_print": Decimal(resolved["double_print"]) if resolved["double_print"] is not None else None,
        "surcharge": Decimal(resolved["surcharge"]),
        "surcharge_threshold_gsm": int(resolved["surcharge_threshold_gsm"]),
        "gloss_first_side": Decimal(resolved["gloss_first_side"]),
        "matt_first_side": Decimal(resolved["matt_first_side"]),
        "lamination_second_multiplier": Decimal(resolved["lamination_second_multiplier"]),
    }


def _legacy_override(candidates: list[Any], *, preset: Decimal, master_value: Decimal | None) -> Decimal | None:
    """Keep a legacy stored value only when it differs from preset AND master."""
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        try:
            amount = Decimal(str(candidate)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if amount != preset and (master_value is None or amount != master_value):
            return amount
        return None
    return None


def resolve_paper_row(row: dict[str, Any], master: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve one paper row under law 11.3 and return the flat table values.

    The returned dict keeps the caller's row data and adds the resolved
    columns the table MUST show (law 11.3): ``paper_cost``, ``gsm``,
    ``double_sided_available``, ``printing_alone_one_side``,
    ``printing_alone_both_sides``, ``surcharge_rate``, ``surcharge_applies``,
    ``total_one_side``, ``total_both_sides`` plus the resolved override
    flags (``override_single_print`` / ``override_double_print`` /
    ``override_surcharge``, null when automatic).
    """
    master_values = _master_decimals(master)
    resolved = deepcopy(row)

    paper_cost = _decimal(row.get("paper_base_price") or "0.00", label="paper cost")
    gsm = int(row.get("gsm") or 0)

    sticker = bool(row.get("is_sticker_stock")) or any(
        token in " ".join(
            [
                _text(row.get("label")),
                _text(row.get("paper_name")),
                _text(row.get("paper_type")),
                _text(row.get("category")),
            ]
        ).lower()
        for token in ("sticker", "tictac", "tic tac")
    )
    supports_double = row.get("supports_double_side")
    if supports_double is None:
        supports_double = row.get("double_sided_enabled")
    if supports_double is None:
        supports_double = True
    double_sided_available = bool(supports_double) and not sticker

    # --- one-side print -------------------------------------------------
    explicit_single = _decimal(row.get("override_single_print"), allow_null=True, label="one-side override")
    if explicit_single is not None:
        single_print = explicit_single
    else:
        legacy_single = _legacy_override(
            [row.get("single_print_base")],
            preset=DEFAULT_ROW_SINGLE_PRINT,
            master_value=master_values["single_print"],
        )
        single_print = legacy_single if legacy_single is not None else master_values["single_print"]

    # --- both-sides print ----------------------------------------------
    explicit_double = _decimal(row.get("override_double_print"), allow_null=True, label="both-sides override")
    derived_double = master_values["double_print"] if master_values["double_print"] is not None else single_print * Decimal("2")
    if explicit_double is not None:
        double_print = explicit_double
    else:
        legacy_double = _legacy_override(
            [row.get("double_print_base")],
            preset=DEFAULT_ROW_DOUBLE_PRINT,
            master_value=derived_double,
        )
        double_print = legacy_double if legacy_double is not None else derived_double

    # --- surcharge ------------------------------------------------------
    explicit_surcharge = _decimal(row.get("override_surcharge"), allow_null=True, label="surcharge override")
    legacy_surcharge = _legacy_override(
        [row.get("heavy_paper_surcharge")],
        preset=DEFAULT_ROW_SURCHARGE,
        master_value=master_values["surcharge"],
    )
    if explicit_surcharge is not None:
        surcharge_rate = explicit_surcharge
        surcharge_applies = surcharge_rate > 0
    elif legacy_surcharge is not None:
        surcharge_rate = legacy_surcharge
        surcharge_applies = surcharge_rate > 0 and gsm >= master_values["surcharge_threshold_gsm"]
    else:
        surcharge_rate = master_values["surcharge"]
        surcharge_applies = surcharge_rate > 0 and gsm >= master_values["surcharge_threshold_gsm"]
    applied_surcharge = surcharge_rate if surcharge_applies else Decimal("0.00")

    total_one_side = paper_cost + single_print + applied_surcharge
    total_both_sides = paper_cost + double_print + applied_surcharge if double_sided_available else None

    resolved.update(
        {
            "paper_base_price": _decimal_string(paper_cost),
            "gsm": gsm,
            "paper_cost": _decimal_string(paper_cost),
            "double_sided_available": double_sided_available,
            "double_sided_enabled": double_sided_available,
            "supports_double_side": double_sided_available,
            "override_single_print": _decimal_string(explicit_single),
            "override_double_print": _decimal_string(explicit_double),
            "override_surcharge": _decimal_string(explicit_surcharge),
            "printing_alone_one_side": _decimal_string(single_print),
            "printing_alone_both_sides": _decimal_string(double_print) if double_sided_available else None,
            "surcharge_rate": _decimal_string(surcharge_rate),
            "surcharge_applies": surcharge_applies,
            "surcharge_threshold_gsm": master_values["surcharge_threshold_gsm"],
            "total_one_side": _decimal_string(total_one_side),
            "total_both_sides": _decimal_string(total_both_sides),
            # Legacy resolved fields kept for existing consumers
            # (market guides, readiness checks, quote builder views).
            "single_print_base": _decimal_string(single_print),
            "double_print_base": _decimal_string(double_print),
            "heavy_paper_surcharge": _decimal_string(surcharge_rate),
            "single_side_price": _decimal_string(total_one_side),
            "double_side_price": _decimal_string(total_both_sides),
        }
    )
    return resolved


def lamination_first_side(master: dict[str, Any], kind: str) -> Decimal:
    master_values = _master_decimals(master)
    return master_values["gloss_first_side"] if kind == "gloss" else master_values["matt_first_side"]


def lamination_both_sides(master: dict[str, Any], kind: str) -> Decimal:
    master_values = _master_decimals(master)
    first = master_values["gloss_first_side"] if kind == "gloss" else master_values["matt_first_side"]
    return (first * (Decimal("1") + master_values["lamination_second_multiplier"])).quantize(Decimal("0.01"))


def resolve_finishing_row(row: dict[str, Any], master: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve one finishing row (lamination derives from the master inputs)."""
    resolved = deepcopy(row)
    kind_info = LAMINATION_ROW_KINDS.get(_text(row.get("key")))
    override_rate = _decimal(row.get("override_rate"), allow_null=True, label="rate override")
    override_minimum = _decimal(row.get("override_minimum"), allow_null=True, label="minimum override")

    if kind_info is not None:
        tone, both_sides = kind_info
        derived_rate = lamination_both_sides(master, tone) if both_sides else lamination_first_side(master, tone)
        stored_rate = _decimal(row.get("price"), allow_null=True, label="rate")
        if override_rate is not None:
            rate = override_rate
        elif stored_rate is not None and stored_rate != derived_rate and stored_rate != _default_finishing_rate(row):
            rate = stored_rate
        else:
            rate = derived_rate
    else:
        rate = _decimal(row.get("price") or "0.00", label="rate") if override_rate is None else override_rate

    minimum_charge = _decimal(row.get("minimum_charge"), allow_null=True, label="minimum charge")
    if override_minimum is not None:
        minimum_charge = override_minimum

    resolved.update(
        {
            "price": _decimal_string(rate),
            "minimum_charge": _decimal_string(minimum_charge),
            "override_rate": _decimal_string(override_rate),
            "override_minimum": _decimal_string(override_minimum),
            "derived_from_master": kind_info is not None and override_rate is None,
        }
    )
    return resolved


def _default_finishing_rate(row: dict[str, Any]) -> Decimal | None:
    from services.pricing.mvp_rate_card import FINISHING_DEFINITION_BY_KEY

    definition = FINISHING_DEFINITION_BY_KEY.get(_text(row.get("key")))
    if not definition:
        return None
    try:
        return _decimal(definition.get("price"), allow_null=True, label="rate")
    except ValidationError:
        return None


def finishing_charge(row: dict[str, Any], *, quantity: int, units: int | None = None) -> Decimal:
    """Law 11.3 finishing charge: max(qty x rate, minimum) or flat once."""
    rate = _decimal(row.get("price") or "0.00", label="rate")
    minimum_raw = row.get("minimum_charge")
    minimum_charge = _decimal(minimum_raw, allow_null=True, label="minimum charge")
    mode = _text(row.get("pricing_mode")).lower()
    if mode in ("flat_per_job", "per_job", "flat"):
        if quantity > 0:
            return rate
        return Decimal("0.00")
    billable_units = units if units is not None else quantity
    total = rate * Decimal(max(billable_units, 0))
    if minimum_charge is not None and total < minimum_charge:
        return minimum_charge
    return total


def resolve_rate_card(
    *,
    paper_rows: list[dict[str, Any]],
    finishing_rows: list[dict[str, Any]],
    master_settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the whole rate card. THE function (law 11.3, law 11.5)."""
    master = resolve_master_settings(master_settings)
    return {
        "master_settings": master,
        "paper_rows": [resolve_paper_row(row, master) for row in paper_rows],
        "finishing_rows": [resolve_finishing_row(row, master) for row in finishing_rows],
    }
