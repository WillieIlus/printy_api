from __future__ import annotations

from typing import Any

from jobs.models import ManagedJob
from services.pricing.spec_normalization import normalize_color_mode, normalize_print_sides
from services.pricing.finishing_normalization import normalize_finishing_slug


def build_job_title(job: ManagedJob, *, long_form: bool = False) -> str:
    """Build a human-readable job title from the stored spec.

    One function used by the buyer tracker, the buyer ``TrackedJob`` strip and any
    dashboard/job card that needs a readable label instead of ``job.title``.
    """
    qs = getattr(job, "source_quote_request", None)
    request_snapshot = getattr(qs, "request_snapshot", None) if qs is not None else None
    calculator_inputs = (
        request_snapshot.get("calculator_inputs") if isinstance(request_snapshot, dict) else None
    )
    raw = dict(calculator_inputs or {})

    product_key = str(raw.get("product_type") or "").strip().lower()
    product_label = _product_label(product_key, _first(job.title, raw.get("product_name"), raw.get("product_label")))

    quantity = _int(raw.get("quantity") if raw else None)
    if not quantity and getattr(job, "quantity", None):
        quantity = _int(getattr(job, "quantity"))

    size_raw = raw.get("size") or raw.get("finished_size") or raw.get("size_label")
    size_label = _size_label(size_raw, product_key)

    paper_raw = raw.get("paper") or raw.get("paper_stock") or raw.get("paper_name") or ""
    paper_label = _paper_label(str(paper_raw or "").strip(), product_key)

    sides_raw = raw.get("sides") or raw.get("print_sides")
    sides = normalize_print_sides(sides_raw) if sides_raw is not None else None

    color_raw = raw.get("color_mode") or raw.get("color_mode_label") or raw.get("ink_option")
    color = normalize_color_mode(color_raw) if color_raw is not None else None

    lamination_raw = raw.get("lamination") or raw.get("lamination_mode") or raw.get("finishings")
    finishing_label = _finishing_label(lamination_raw, product_key)

    pieces_timestamp = raw.get("pieces_timestamp") or getattr(job, "pieces_timestamp", None)
    rush_label = _rush_label(raw, job)

    title = _compose(
        product=product_label,
        quantity=quantity,
        size=size_label,
        paper=paper_label,
        sides=sides,
        color=color,
        finishing=finishing_label,
        rush=rush_label,
        long_form=long_form,
    )
    return title or "Print job"


def _first(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


PRODUCT_LABELS: dict[str, str] = {
    "business_card": "business cards",
    "businesscards": "business cards",
    "business_card_mat": "business cards",
    "business_cards_mat": "business cards",
    "flyer_single": "flyers",
    "flyer": "flyers",
    "flyers": "flyers",
    "brochure": "brochures",
    "brochures": "brochures",
    "leaflet": "leaflets",
    "leaflets": "leaflets",
    "pamphlet": "pamphlets",
    "pamphlets": "pamphlets",
    "poster": "posters",
    "posters": "posters",
    "billboard": "billboards",
    "billboards": "billboards",
    "banner": "banners",
    "banners": "banners",
    "envelope": "envelopes",
    "envelopes": "envelopes",
    "letterhead": "letterheads",
    "letterheads": "letterheads",
    "notepad": "notepads",
    "notepads": "notepads",
    "calendar": "calendars",
    "calendars": "calendars",
    "diary": "diaries",
    "diaries": "diaries",
    "sticker": "stickers",
    "stickers": "stickers",
    "label": "labels",
    "labels": "labels",
    "packaging": "packaging",
    "packagings": "packaging",
    "cardboard_box": "cardboard boxes",
    "cardboard_boxes": "cardboard boxes",
    "correx_sign": "corrugated signs",
    "correx_signs": "corrugated signs",
    "corrugated_sign": "corrugated signs",
    "corrugated_signs": "corrugated signs",
    "foam_board_sign": "foam board signs",
    "foam_board_signs": "foam board signs",
    "foamboard_sign": "foam board signs",
    "foamboard_signs": "foam board signs",
    "double_sided_flyer": "double-sided flyers",
    "double_sided_flyers": "double-sided flyers",
    "booklet_cover": "booklet covers",
    "booklet_c overs": "booklet covers",
    "booklet_covers": "booklet covers",
    "c_booklet_cover": "booklet covers",
    "c_booklet_c overs": "booklet covers",
    "c_booklet_covers": "booklet covers",
    "sticker_rolls": "sticker rolls",
    "sticker_rolls_arrival": "sticker rolls",
    "sticker_rolls_rollout": "sticker rolls",
    "sticker_roll": "sticker rolls",
    "sticker_rollout": "sticker rolls",
    "sticker_rollout_arrival": "sticker rolls",
}


def _product_label(product_key: str, fallback: str) -> str:
    if product_key in PRODUCT_LABELS:
        return PRODUCT_LABELS[product_key]
    lowered = fallback.lower().strip()
    if lowered in PRODUCT_LABELS:
        return PRODUCT_LABELS[lowered]
    if lowered.endswith("s") and lowered[:-1] in PRODUCT_LABELS:
        return PRODUCT_LABELS[lowered[:-1]]
    if lowered:
        if lowered.endswith("ies"):
            base = lowered[:-3] + "y"
        elif lowered.endswith("ves"):
            base = lowered[:-3] + "f"
        elif lowered.endswith("s") and not lowered.endswith("ss"):
            base = lowered[:-1]
        else:
            base = lowered
        plural = f"{base}s"
        if len(plural) > 2:
            return plural
    return ""


SIZE_LABELS: dict[str, str] = {
    "a4": "A4",
    "a3": "A3",
    "a2": "A2",
    "a1": "A1",
    "a0": "A0",
    "letter": "Letter",
    "legal": "Legal",
    "sra3": "SRA3",
    "sra2": "SRA2",
    "b5": "B5",
    "b4": "B4",
    "b3": "B3",
    "b2": "B2",
    "b1": "B1",
    "b0": "B0",
}


def _finishing_label(raw: Any, product_key: str) -> str:
    """Readable labels for the stored finishing values (slug or list of them)."""
    entries = raw if isinstance(raw, (list, tuple)) else [raw]
    labels: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            value = entry.get("label") or entry.get("name") or entry.get("value") or entry.get("slug")
        else:
            value = entry
        slug = normalize_finishing_slug(value) if value not in (None, "") else ""
        if not slug:
            continue
        words = slug.replace("_", " ").replace("-", " ").split()
        if not words:
            continue
        text = " ".join(words)
        if text not in labels:
            labels.append(text)
    return ", ".join(labels)


def _rush_label(raw: dict[str, Any], job: ManagedJob) -> str:
    """Rush wording from the stored speed tier, when the job was a rush job."""
    value = _first(raw.get("speed_tier"), raw.get("rush"), raw.get("turnaround")).lower()
    if not value:
        return ""
    if "fastest" in value or value in {"same_day", "same-day", "2", "3"}:
        return "fastest turnaround"
    if "fast" in value or value in {"1"}:
        return "faster turnaround"
    return ""


def _compose(
    *,
    product: str,
    quantity: int,
    size: str,
    paper: str,
    sides: str | None,
    color: str | None,
    finishing: str,
    rush: str,
    long_form: bool,
) -> str:
    """Join the spec parts into one label; long form adds the print details."""
    head = f"{quantity} {product}".strip() if quantity and product else product
    if not long_form:
        return head
    parts = [head] if head else []
    if size:
        parts.append(size)
    if paper:
        parts.append(paper)
    if sides == "DUPLEX":
        parts.append("double-sided")
    elif sides == "SIMPLEX":
        parts.append("single-sided")
    if color == "COLOR":
        parts.append("full colour")
    elif color == "BW":
        parts.append("black & white")
    if finishing:
        parts.append(finishing)
    if rush:
        parts.append(rush)
    return ", ".join(part for part in parts if part)


def _size_label(raw: Any, product_key: str) -> str:
    label = _first(raw) if raw else ""
    if not label:
        return ""
    normalized = str(label).strip().lower()
    if normalized in SIZE_LABELS:
        return SIZE_LABELS[normalized]
    cleaned = normalized.replace("x", " ").replace("×", " ").replace("*", " ").strip()
    parts = cleaned.split()
    if len(parts) == 2:
        try:
            w = float(parts[0])
            h = float(parts[1])
            if (w >= 1 and h >= 1):
                unit = raw if isinstance(raw, str) else ""
                unit_lower = str(unit).lower()
                if "mm" in unit_lower or w < 100:
                    return f"{round(w)}×{round(h)} mm"
                return f"{round(w)}×{round(h)} cm" if max(w, h) < 2000 else f"{round(w)}×{round(h)} mm"
        except (ValueError, OverflowError):
            return str(label)
    return str(label) if label else ""


def _paper_label(raw: str, product_key: str) -> str:
    """The printer-friendly paper label for a stored paper key."""
    label = _first(raw)
    if not label:
        return ""
    key = label.strip().lower()
    for candidate in (key, key.replace(" ", "_"), key.replace("-", "_")):
        if candidate in PAPER_LABELS:
            return PAPER_LABELS[candidate]
    if key.replace(" ", "").replace("_", "") in {"", "none", "no"}:
        return ""
    # Unknown stock: show the raw value with separators folded, never a guess.
    return " ".join(part for part in label.replace("_", " ").replace("-", " ").split() if part)


PAPER_LABELS: dict[str, str] = {
    "115gsm": "115gsm",
    "130gsm": "130gsm",
    "135gsm": "135gsm",
    "150gsm": "150gsm",
    "170gsm": "170gsm",
    "170gsm_silk": "170gsm silk",
    "170gsm_mat": "170gsm matte",
    "170gsm_gloss": "170gsm gloss",
    "190gsm": "190gsm",
    "200gsm_silk": "200gsm silk",
    "200gsm": "200gsm",
    "200gsm_matte": "200gsm matte",
    "200gsm_glossy": "200gsm glossy",
    "200gsm_mat": "200gsm matte",
    "250gsm": "250gsm",
    "250gsm_matte": "250gsm matte",
    "250gsm_glossy": "250gsm glossy",
    "250gsm_silk": "250gsm silk",
    "250gsm_mat": "250gsm matte",
    "270gsm": "270gsm",
    "300gsm": "300gsm",
    "300gsm_matte": "300gsm matte",
    "300gsm_glossy": "300gsm glossy",
    "300gsm_silk": "300gsm silk",
    "300gsm_mat": "300gsm matte",
    "350gsm": "350gsm",
    "350gsm_matte": "350gsm matte",
    "350gsm_silk": "350gsm silk",
    "350gsm_glossy": "350gsm glossy",
    "350gsm_coated": "350gsm coated",
    "400gsm": "400gsm",
    "400gsm_matte": "400gsm matte",
    "400gsm_glossy": "400gsm glossy",
    "400gsm_silk": "400gsm silk",
    "400gsm_adj": "400gsm",
    "artboard": "artboard",
    "kpm_artboard": "artboard",
    "uncoated": "uncoated",
    "uncoated_paper": "uncoated",
    "uncoated_130gsm": "130gsm uncoated",
    "uncoated_150gsm": "150gsm uncoated",
    "uncoated_170gsm": "170gsm uncoated",
    "uncoated_200gsm": "200gsm uncoated",
    "uncoated_250gsm": "250gsm uncoated",
    "uncoated_300gsm": "300gsm uncoated",
    "uncoated_350gsm": "350gsm uncoated",
    "uncoated_400gsm": "400gsm uncoated",
    "laminated_silk_gloss": "silk laminated",
    "laminated_gloss": "gloss laminated",
    "laminated_matte": "matte laminated",
    "laminated_hard": "hard laminated",
    "laminated_soft": "soft laminated",
    "spot_uv": "spot UV",
    "aqueous": "aqueous coating",
    "holographic_foil": "holographic foil",
    "hot_foil": "hot foil",
    "metallic_foil": "metallic foil",
    "gold_foil": "gold foil",
    "silver_foil": "silver foil",
    "rose_gold_foil": "rose gold foil",
    "black_foil": "black foil",
    "blue_foil": "blue foil",
    "purple_foil": "purple foil",
    "green_foil": "green foil",
    "pink_foil": "pink foil",
    "red_foil": "red foil",
    "rainbow_holographic_foil": "rainbow holographic foil",
    "stripe_foil": "stripe foil",
    "patterned_foil": "patterned foil",
    "pvc_receipt_book": "PVC receipt book",
    "pvc": "PVC",
    "pvc_basic": "PVC basic",
    "pvc_laminated_basic": "PVC laminated basic",
    "pvc_matte_laminated_basic": "PVC matte laminated basic",
    "pvc_gloss_laminated_basic": "PVC gloss laminated basic",
    "pvc_receipt_book_laminated": "PVC receipt book laminated",
    "pvc_receipt_book_matte_laminated": "PVC receipt book matte laminated",
    "pvc_receipt_book_gloss_laminated": "PVC receipt book gloss laminated",
    "pvc_book_laminated": "PVC book laminated",
    "pvc_book_matte_laminated": "PVC book matte laminated",
    "pvc_book_gloss_laminated": "PVC book gloss laminated",
    "leatherette": "leatherette",
    "leatherette_black": "leatherette black",
    "leatherette_brown": "leatherette brown",
    "leatherette_blue": "leatherette blue",
    "leatherette_red": "leatherette red",
    "leatherette_green": "leatherette green",
    "leatherette_purple": "leatherette purple",
    "leatherette_gold": "leatherette gold",
    "leatherette_silver": "leatherette silver",
    "leatherette_rose": "leatherette rose gold",
    "pvc_bin_bag": "PVC bin bag",
    "pvc_bin_bags": "PVC bin bags",
    "pvc_bin_bag_laminated": "PVC bin bag laminated",
    "pvc_bin_bags_laminated": "PVC bin bags laminated",
    "pvc_bin_bag_matte_laminated": "PVC bin bag matte laminated",
    "pvc_bin_bags_matte_laminated": "PVC bin bags matte laminated",
    "pvc_bin_bag_gloss_laminated": "PVC bin bag gloss laminated",
    "pvc_bin_bags_gloss_laminated": "PVC bin bags gloss laminated",
    "paper_bin_bag": "paper bin bag",
    "paper_bin_bags": "paper bin bags",
    "paper_bin_bag_laminated": "paper bin bag laminated",
    "paper_bin_bags_laminated": "paper bin bags laminated",
    "paper_bin_bag_matte_laminated": "paper bin bag matte laminated",
    "paper_bin_bags_matte_laminated": "paper bin bags matte laminated",
    "paper_bin_bag_gloss_laminated": "paper bin bag gloss laminated",
    "paper_bin_bags_gloss_laminated": "paper bin bags gloss laminated",
    "report_cover": "report cover",
    "report_covers": "report covers",
    "pvc_report_cover": "PVC report cover",
    "pvc_report_covers": "PVC report covers",
    "pvc_report_cover_laminated": "PVC report cover laminated",
    "pvc_report_covers_laminated": "PVC report covers laminated",
    "pvc_report_cover_matte_laminated": "PVC report cover matte laminated",
    "pvc_report_covers_matte_laminated": "PVC report covers matte laminated",
    "pvc_report_cover_gloss_laminated": "PVC report cover gloss laminated",
    "pvc_report_covers_gloss_laminated": "PVC report covers gloss laminated",
    "leatherette_report_cover": "leatherette report cover",
    "leatherette_report_covers": "leatherette report covers",
    "leatherette_report_cover_black": "leatherette report cover black",
    "leatherette_report_covers_black": "leatherette report covers black",
    "leatherette_report_cover_brown": "leatherette report cover brown",
    "leatherette_report_covers_brown": "leatherette report covers brown",
    "leatherette_report_cover_blue": "leatherette report cover blue",
    "leatherette_report_covers_blue": "leatherette report covers blue",
    "leatherette_report_cover_red": "leatherette report cover red",
    "leatherette_report_covers_red": "leatherette report covers red",
    "leatherette_report_cover_green": "leatherette report cover green",
    "leatherette_report_covers_green": "leatherette report covers green",
    "leatherette_report_cover_purple": "leatherette report cover purple",
    "leatherette_report_covers_purple": "leatherette report covers purple",
    "leatherette_report_cover_gold": "leatherette report cover gold",
    "leatherette_report_covers_gold": "leatherette report covers gold",
    "leatherette_report_cover_silver": "leatherette report cover silver",
    "leatherette_report_covers_silver": "leatherette report covers silver",
    "leatherette_report_cover_rose": "leatherette report cover rose gold",
    "leatherette_report_covers_rose": "leatherette report covers rose gold",
    "spiral_bound": "spiral bound",
    "spiral_bound_report_cover": "spiral bound report cover",
    "spiral_bound_report_covers": "spiral bound report covers",
    "hardbound": "hardbound",
    "hardbound_report_cover": "hardbound report cover",
    "hardbound_report_covers": "hardbound report covers",
    "xl_report_cover_80micron": "XL report cover 80 micron",
    "xl_report_covers_80micron": "XL report covers 80 micron",
    "xl_report_cover_100micron": "XL report cover 100 micron",
    "xl_report_covers_100micron": "XL report covers 100 micron",
    "xl_report_cover_120micron": "XL report cover 120 micron",
    "xl_report_covers_120micron": "XL report covers 120 micron",
    "xl_report_cover_150micron": "XL report cover 150 micron",
    "xl_report_covers_150micron": "XL report covers 150 micron",
    "xl_report_cover_200micron": "XL report cover 200 micron",
    "xl_report_covers_200micron": "XL report covers 200 micron",
    "cardboard_box_custom": "cardboard box custom",
    "cardboard_boxes_custom": "cardboard boxes custom",
    "corrugated_sign_custom": "corrugated sign custom",
    "corrugated_signs_custom": "corrugated signs custom",
    "foam_board_sign_custom": "foam board sign custom",
    "foam_board_signs_custom": "foam board signs custom",
    "foamboard_sign_custom": "foam board sign custom",
    "foamboard_signs_custom": "foam board signs custom",
    "reusable_masking_tape": "reusable masking tape",
    "custom_masking_tape": "custom masking tape",
    "custom_masking_tapes": "custom masking tape",
    "reusable_masking_tapes": "reusable masking tape",
    "yellow_masking_tape": "yellow masking tape",
    "red_masking_tape": "red masking tape",
    "blue_masking_tape": "blue masking tape",
    "green_masking_tape": "green masking tape",
    "black_masking_tape": "black masking tape",
    "white_masking_tape": "white masking tape",
    "cream_masking_tape": "cream masking tape",
    "custom_sticker_tablet": "custom sticker tablet",
    "custom_stickers_tablet": "custom sticker tablet",
    "sticker_tablet": "sticker tablet",
    "sticker_tablets": "sticker tablet",
    "custom_sticker_rectangle": "custom sticker rectangle",
    "custom_stickers_rectangle": "custom sticker rectangle",
}

