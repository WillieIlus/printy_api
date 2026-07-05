from __future__ import annotations

from urllib.parse import quote

from quotes.choices import QuoteOfferStatus
from api.visibility import CLIENT_ACTOR, project_identity


def _string(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _title_case(value: str) -> str:
    if not value:
        return ""
    return value.replace("_", " ").replace("-", " ").title()


def _list(value) -> list[str]:
    if isinstance(value, list):
        return [_string(item) for item in value if _string(item)]
    if isinstance(value, str):
        return [_string(item) for item in value.split(",") if _string(item)]
    return []


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _first(*values) -> str:
    for value in values:
        text = _string(value)
        if text:
            return text
    return ""


def _normalize_phone(value: str) -> str:
    raw = _string(value)
    if not raw:
        return ""
    prefix = "+" if raw.startswith("+") else ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    return f"{prefix}{digits}" if digits else ""


def _whatsapp_url(phone: str, message: str) -> str:
    digits = _normalize_phone(phone).lstrip("+")
    if not digits:
        return ""
    return f"https://wa.me/{digits}?text={quote(message)}"


def _request_snapshot(quote_request) -> dict:
    return _dict(getattr(quote_request, "request_snapshot", None))


def _calculator_inputs(quote_request) -> dict:
    return _dict(_request_snapshot(quote_request).get("calculator_inputs"))


def _request_details(quote_request) -> dict:
    return _dict(_request_snapshot(quote_request).get("request_details"))


def _selected_shop_preview(quote_request) -> dict:
    return _dict(_request_snapshot(quote_request).get("selected_shop_preview"))


def _production_preview(quote_request) -> dict:
    return _dict(_request_snapshot(quote_request).get("production_preview_snapshot"))


def _custom_snapshot(quote_request) -> dict:
    return _dict(_request_snapshot(quote_request).get("custom_product_snapshot"))


def _job_type_label(quote_request) -> str:
    calculator_inputs = _calculator_inputs(quote_request)
    return _title_case(_first(calculator_inputs.get("product_type"), calculator_inputs.get("product_family"), "custom print job"))


def _quantity_label(quote_request) -> str:
    calculator_inputs = _calculator_inputs(quote_request)
    quantity = calculator_inputs.get("quantity")
    if isinstance(quantity, (int, float)):
        return f"{int(quantity):,} pcs"
    text = _string(quantity)
    return text or "Quantity not specified"


def _size_label(quote_request) -> str:
    calculator_inputs = _calculator_inputs(quote_request)
    selected_shop_preview = _selected_shop_preview(quote_request)
    return _first(
        calculator_inputs.get("finished_size"),
        calculator_inputs.get("size_label"),
        selected_shop_preview.get("size_label"),
        "Size not specified",
    )


def _paper_label(quote_request) -> str:
    calculator_inputs = _calculator_inputs(quote_request)
    selected_shop_preview = _selected_shop_preview(quote_request)
    return _first(
        calculator_inputs.get("paper_stock"),
        calculator_inputs.get("material_type"),
        selected_shop_preview.get("paper_label"),
        selected_shop_preview.get("material_label"),
        "Paper or material not specified",
    )


def _finishing_labels(quote_request) -> list[str]:
    calculator_inputs = _calculator_inputs(quote_request)
    production_preview = _production_preview(quote_request)
    selected_shop_preview = _selected_shop_preview(quote_request)
    finishings = []
    finishings.extend(_list(production_preview.get("selected_finishings")))
    finishings.extend(_list(selected_shop_preview.get("matched_specs")))
    for key in ("lamination", "cover_lamination", "binding_type", "folding", "corner_rounding", "cut_type"):
        value = calculator_inputs.get(key)
        if isinstance(value, bool):
            if value:
                finishings.append(_title_case(key))
        else:
            text = _string(value)
            if text:
                finishings.append(_title_case(text))
    deduped = []
    seen = set()
    for item in finishings:
        normalized = item.lower()
        if item and normalized not in seen:
            seen.add(normalized)
            deduped.append(item)
    return deduped


def _needs_confirmation(quote_request) -> list[str]:
    request_snapshot = _request_snapshot(quote_request)
    selected_shop_preview = _selected_shop_preview(quote_request)
    values = _list(request_snapshot.get("needs_confirmation"))
    values.extend(_list(selected_shop_preview.get("needs_confirmation")))
    deduped = []
    seen = set()
    for item in values:
        normalized = item.lower()
        if item and normalized not in seen:
            seen.add(normalized)
            deduped.append(item)
    return deduped


def _artwork_files(quote_request) -> list[dict]:
    custom_snapshot = _custom_snapshot(quote_request)
    request_details = _request_details(quote_request)
    artwork_files = []

    artwork_name = _first(custom_snapshot.get("artwork_file_name"), request_details.get("artwork_file_name"))
    if artwork_name:
        artwork_files.append({"name": artwork_name, "url": ""})

    for ref in _list(custom_snapshot.get("artwork_refs")):
        artwork_files.append({"name": ref, "url": ""})

    for attachment in quote_request.attachments.all():
        url = ""
        try:
            url = attachment.file.url if attachment.file else ""
        except Exception:
            url = ""
        artwork_files.append({"name": attachment.name or attachment.file.name.rsplit("/", 1)[-1], "url": url})

    deduped = []
    seen = set()
    for item in artwork_files:
        key = (item.get("name") or "", item.get("url") or "")
        if key not in seen and item.get("name"):
            seen.add(key)
            deduped.append(item)
    return deduped


def _matched_shops(quote_request) -> list[dict]:
    requests = []
    if quote_request.source_draft_id:
        requests = list(
            quote_request.source_draft.generated_requests.select_related("shop").order_by("created_at", "id")
        )
    matched = []
    seen = set()
    for request in requests or [quote_request]:
        shop = getattr(request, "shop", None)
        if not shop:
            continue
        key = shop.slug or f"shop-{shop.id}"
        if key in seen:
            continue
        seen.add(key)
        matched.append({
            "id": shop.id,
            "name": shop.name,
            "slug": shop.slug,
            "is_public": bool(shop.is_public),
        })
    return matched


def _production_summary(quote_request) -> dict:
    production_preview = _production_preview(quote_request)
    pieces_per_sheet = production_preview.get("pieces_per_sheet")
    sheets_required = production_preview.get("sheets_required")
    layout = f"{pieces_per_sheet}-up" if pieces_per_sheet else "Layout not calculated"
    sheets = f"{int(sheets_required):,} sheets" if isinstance(sheets_required, (int, float)) else _first(
        production_preview.get("parent_sheet"),
        "Waiting on review",
    )
    detail = _first(
        production_preview.get("imposition_label"),
        production_preview.get("layout_note"),
        "Production preview will stay in Printy as the source of truth.",
    )
    return {
        "layout": layout,
        "sheets": sheets,
        "detail": detail,
    }


def format_quote_request_whatsapp_message(quote_request) -> str:
    needs_confirmation = _needs_confirmation(quote_request)
    lines = [
        f"Printy quote request #{quote_request.id}",
        f"Job type: {_job_type_label(quote_request)}",
        f"Quantity: {_quantity_label(quote_request)}",
        f"Size: {_size_label(quote_request)}",
        f"Paper/material: {_paper_label(quote_request)}",
        f"Finishing: {', '.join(_finishing_labels(quote_request)) or 'None specified'}",
        f"Needs confirmation: {', '.join(needs_confirmation) or 'None'}",
        "Please keep final pricing and status updates in Printy.",
    ]
    return "\n".join(lines)


def _safe_shop_whatsapp_phone(quote_request) -> str:
    shop = getattr(quote_request, "shop", None)
    if not shop or not shop.is_public:
        return ""
    return _normalize_phone(getattr(shop, "phone_number", ""))


def build_quote_request_whatsapp_handoff(quote_request, *, viewer_role: str) -> dict:
    latest_response = quote_request.get_latest_quote()
    base_message = format_quote_request_whatsapp_message(quote_request)

    if not latest_response or latest_response.status not in (
        QuoteOfferStatus.SENT,
        QuoteOfferStatus.REVISED,
        QuoteOfferStatus.ACCEPTED,
    ):
        return {
            "available": False,
            "label": "WhatsApp available after shop responds",
            "reason": "awaiting_shop_response",
            "message": base_message,
            "url": "",
            "phone": "",
        }

    if viewer_role == "shop":
        buyer_phone = _normalize_phone(getattr(quote_request, "customer_phone", ""))
        if not buyer_phone:
            return {
                "available": False,
                "label": "Buyer phone unavailable",
                "reason": "buyer_phone_missing",
                "message": base_message,
                "url": "",
                "phone": "",
            }
        return {
            "available": True,
            "label": "Continue on WhatsApp",
            "reason": "",
            "message": base_message,
            "url": _whatsapp_url(buyer_phone, base_message),
            "phone": buyer_phone,
        }

    shop_phone = _safe_shop_whatsapp_phone(quote_request)
    if not shop_phone:
        return {
            "available": False,
            "label": "Connect WhatsApp",
            "reason": "shop_whatsapp_not_public",
            "message": base_message,
            "url": "",
            "phone": "",
        }
    return {
        "available": True,
        "label": "Continue on WhatsApp",
        "reason": "",
        "message": base_message,
        "url": _whatsapp_url(shop_phone, base_message),
        "phone": shop_phone,
    }


def format_quote_request_brief_text(brief: dict) -> str:
    buyer = brief.get("buyer") or {}
    production = brief.get("production_preview") or {}
    artwork_files = brief.get("artwork_files") or []
    matched_shops = brief.get("matched_shops") or []

    lines = [
        f"Printy Request #{brief['request_id']}",
        f"Created: {brief['created_label']}",
    ]
    if buyer.get("name"):
        lines.append(f"Buyer: {buyer['name']}")
    if buyer.get("email"):
        lines.append(f"Buyer email: {buyer['email']}")
    if buyer.get("phone"):
        lines.append(f"Buyer phone: {buyer['phone']}")

    lines.extend(
        [
            f"Job type: {brief['job_type']}",
            f"Quantity: {brief['quantity']}",
            f"Size: {brief['size']}",
            f"Paper/material: {brief['paper_material']}",
            f"Finishing: {', '.join(brief['finishing']) or 'None specified'}",
            f"Needs confirmation: {', '.join(brief['needs_confirmation']) or 'None'}",
            f"Notes: {brief['notes'] or 'None'}",
            f"Artwork: {', '.join(item['name'] for item in artwork_files) or 'None attached'}",
            f"Production preview: {production.get('layout', 'N/A')} / {production.get('sheets', 'N/A')}",
            f"Matched shops: {', '.join(shop['name'] for shop in matched_shops) or 'Current shop only'}",
        ]
    )
    return "\n".join(lines)


def build_quote_request_brief(quote_request, *, include_buyer_contact: bool, viewer_role: str) -> dict:
    buyer = {
        "name": _string(quote_request.customer_name) if include_buyer_contact else "",
        "email": _string(quote_request.customer_email) if include_buyer_contact else "",
        "phone": _string(quote_request.customer_phone) if include_buyer_contact else "",
    }
    
    actor = CLIENT_ACTOR if viewer_role == "buyer" else "ops"
    matched_shops = _matched_shops(quote_request)
    for shop in matched_shops:
        shop["name"] = project_identity(shop["name"], actor=actor)
        if actor == CLIENT_ACTOR:
            shop["slug"] = "partner" # Hide slug too if client
            
    created_label = quote_request.created_at.strftime("%d %b %Y, %H:%M")
    brief = {
        "request_id": quote_request.id,
        "request_reference": _first(quote_request.request_reference, f"Request #{quote_request.id}"),
        "created_at": quote_request.created_at.isoformat(),
        "created_label": created_label,
        "buyer": buyer,
        "job_type": _job_type_label(quote_request),
        "quantity": _quantity_label(quote_request),
        "size": _size_label(quote_request),
        "paper_material": _paper_label(quote_request),
        "finishing": _finishing_labels(quote_request),
        "notes": _first(_request_details(quote_request).get("notes"), quote_request.notes),
        "artwork_files": _artwork_files(quote_request),
        "production_preview": _production_summary(quote_request),
        "matched_shops": matched_shops,
        "needs_confirmation": _needs_confirmation(quote_request),
        "whatsapp": build_quote_request_whatsapp_handoff(quote_request, viewer_role=viewer_role),
        "download_filename": f"quote-request-{quote_request.id}.pdf",
    }
    brief["summary"] = format_quote_request_brief_text(brief)
    return brief
