"""
WhatsApp-ready summary generation for quote requests and shop quote responses.

Reusable by frontend, API, and future integrations (e.g. WhatsApp Business API).
"""
from decimal import Decimal
from typing import List, Optional

from .draft_files import build_dashboard_quote_file_payload
from .models import QuoteDraftFile, QuoteItem, QuoteRequest, ShopQuote


def _format_price(value) -> str:
    """Format decimal/None as KES string."""
    if value is None:
        return "0"
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


def _item_product_label(item: QuoteItem) -> str:
    """Product name or custom title."""
    if item.item_type == "PRODUCT" and item.product_id and item.product:
        return item.product.name
    return item.title or "Item"


def _item_size_label(item: QuoteItem) -> str:
    """Product size: e.g. 90×55mm or 600×900mm for large format."""
    if item.pricing_mode == "LARGE_FORMAT" and item.chosen_width_mm and item.chosen_height_mm:
        return f"{item.chosen_width_mm}×{item.chosen_height_mm}mm"
    if item.product_id and item.product:
        w = item.product.default_finished_width_mm
        h = item.product.default_finished_height_mm
        if w and h:
            return f"{w}×{h}mm"
    return ""


def _item_paper_label(item: QuoteItem) -> str:
    """Paper spec: e.g. SRA3 300gsm Gloss."""
    if not item.paper_id or not item.paper:
        return ""
    p = item.paper
    paper_type = p.get_paper_type_display() if hasattr(p, "get_paper_type_display") else (p.paper_type or "")
    parts = [p.sheet_size or "", f"{p.gsm}gsm" if p.gsm else "", paper_type]
    return " ".join(str(x) for x in parts if x).strip()


def _item_colour_label(item: QuoteItem) -> str:
    """Colour mode: Black & White or Color."""
    if not item.color_mode:
        return ""
    return item.get_color_mode_display() if hasattr(item, "get_color_mode_display") else (item.color_mode or "")


def _item_finishing_label(item: QuoteItem) -> str:
    """Comma-separated finishing names."""
    names = []
    for qif in item.finishings.select_related("finishing_rate").all():
        if qif.finishing_rate:
            names.append(qif.finishing_rate.name)
    return ", ".join(names) if names else ""


def _get_location_label(quote_request: QuoteRequest) -> str:
    """Delivery location or address."""
    if quote_request.delivery_location_id and quote_request.delivery_location:
        return quote_request.delivery_location.name
    if quote_request.delivery_address:
        return quote_request.delivery_address[:80] + ("..." if len(quote_request.delivery_address) > 80 else "")
    return ""


def _format_item_line(
    item: QuoteItem,
    *,
    include_price: bool = True,
) -> str:
    """Single line for one quote item."""
    product_label = _item_product_label(item)
    size_label = _item_size_label(item)
    qty = item.quantity or 1
    paper_label = _item_paper_label(item)
    colour_label = _item_colour_label(item)
    finishing_label = _item_finishing_label(item)

    parts = [f"• {product_label}"]
    if size_label:
        parts.append(f"({size_label})")
    parts.append(f"× {qty} pcs")
    if paper_label:
        parts.append(f"— {paper_label}")
    if colour_label:
        parts.append(f"— {colour_label}")
    if finishing_label:
        parts.append(f"— {finishing_label}")
    if include_price and item.line_total is not None:
        parts.append(f"= KES {_format_price(item.line_total)}")
    elif include_price:
        parts.append("(quote pending)")

    return " ".join(parts)


def format_quote_request_summary(
    quote_request: QuoteRequest,
    *,
    items: Optional[List[QuoteItem]] = None,
    include_price: bool = False,
) -> str:
    """
    Generate WhatsApp-ready summary for a customer's quote request.

    Use when customer sends a request (before or without shop quote).
    Specs only by default; set include_price=True if items have line_total.

    Args:
        quote_request: The quote request.
        items: Optional list of items (default: quote_request.items).
        include_price: Include line_total when available.

    Returns:
        Plain text, concise, business-ready.
    """
    if items is None:
        items = list(
            quote_request.items.select_related("product", "paper", "material").prefetch_related(
                "finishings__finishing_rate"
            ).all()
        )

    lines = []
    shop_name = quote_request.shop.name if quote_request.shop_id else ""

    lines.append(f"Quote Request #{quote_request.id}" + (f" — {shop_name}" if shop_name else ""))
    lines.append("")
    if quote_request.customer_name:
        lines.append(f"From: {quote_request.customer_name}")
    lines.append("")

    for item in items:
        lines.append(_format_item_line(item, include_price=include_price))

    location = _get_location_label(quote_request)
    if location:
        lines.append("")
        lines.append(f"Location: {location}")

    if quote_request.notes:
        lines.append("")
        lines.append(f"Notes: {quote_request.notes[:200]}" + ("..." if len(quote_request.notes) > 200 else ""))

    # Attachments
    att_count = quote_request.attachments.count()
    if att_count:
        lines.append("")
        lines.append(f"Attachments: {att_count} file(s)")

    return "\n".join(lines).strip()


def format_shop_quote_summary(
    shop_quote: ShopQuote,
    *,
    company_name: str = "",
    company_phone: str = "",
    share_url: Optional[str] = None,
) -> str:
    """
    Generate WhatsApp-ready summary for a shop's quote response.

    Use when shop sends or revises a quote. Includes prices and turnaround.

    Args:
        shop_quote: The shop quote (sent or revised).
        company_name: Shop display name.
        company_phone: Contact phone.
        share_url: Optional shareable URL.

    Returns:
        Plain text, concise, business-ready.
    """
    qr = shop_quote.quote_request
    items = list(
        shop_quote.items.select_related("product", "paper", "material").prefetch_related(
            "finishings__finishing_rate"
        ).all()
    )
    if not items:
        items = list(
            qr.items.select_related("product", "paper", "material").prefetch_related(
                "finishings__finishing_rate"
            ).all()
        )

    lines = []

    if qr.customer_name:
        lines.append(f"Hi {qr.customer_name},")
    lines.append("")
    lines.append("Here is your quote:")
    lines.append("")

    for item in items:
        lines.append(_format_item_line(item, include_price=True))

    total = shop_quote.total
    if total is None and items:
        total = sum((item.line_total or 0) for item in items)
    if total is not None:
        lines.append("")
        lines.append(f"Total: KES {_format_price(total)}")
    lines.append("")

    turnaround = "Turnaround on request"
    if shop_quote.turnaround_hours is not None:
        turnaround = f"{shop_quote.turnaround_hours} working hour(s)"
    elif shop_quote.turnaround_days is not None:
        d = shop_quote.turnaround_days
        turnaround = f"{d} business day(s)" if d == 1 else f"{d} business days"
    lines.append(f"Turnaround: {turnaround}")
    if shop_quote.human_ready_text:
        lines.append(shop_quote.human_ready_text)
    lines.append("")

    if shop_quote.note:
        lines.append(f"Note: {shop_quote.note[:150]}" + ("..." if len(shop_quote.note) > 150 else ""))
        lines.append("")

    location = _get_location_label(qr)
    if location:
        lines.append(f"Delivery: {location}")
        lines.append("")

    if share_url:
        lines.append(f"View full quote: {share_url}")
        lines.append("")

    if company_name:
        lines.append("Best regards,")
        lines.append(company_name)
        if company_phone:
            lines.append(company_phone)

    return "\n".join(lines).strip()


def get_quote_request_summary_text(quote_request: QuoteRequest) -> str:
    """
    Convenience: summary for API/serializer. Uses latest shop quote if available.
    """
    latest = quote_request.get_latest_shop_quote()
    if latest and latest.total is not None:
        return format_shop_quote_summary(
            latest,
            company_name=quote_request.shop.name if quote_request.shop_id else "",
            company_phone=quote_request.shop.phone_number or "" if quote_request.shop_id else "",
        )
    return format_quote_request_summary(quote_request, include_price=False)


def get_shop_quote_summary_text(shop_quote: ShopQuote, share_url: Optional[str] = None) -> str:
    """
    Convenience: summary for API/serializer with optional share URL.
    """
    return format_shop_quote_summary(
        shop_quote,
        company_name=shop_quote.shop.name if shop_quote.shop_id else "",
        company_phone=shop_quote.shop.phone_number or "" if shop_quote.shop_id else "",
        share_url=share_url,
    )


def get_quote_draft_file_summary_text(draft_file: QuoteDraftFile) -> str:
    """Grouped WhatsApp-ready summary for a quote file across one or more shops."""
    payload = build_dashboard_quote_file_payload(draft_file)
    lines: list[str] = []

    customer_name = payload.get("customer_name") or payload.get("company_name") or "Customer"
    lines.append(f"Quote File - {customer_name}")

    if payload.get("contact_email"):
        lines.append(f"Email: {payload['contact_email']}")
    if payload.get("contact_phone"):
        lines.append(f"Phone: {payload['contact_phone']}")

    for index, group in enumerate(payload["shop_groups"], start=1):
        lines.append("")
        lines.append(f"{index}. {group['shop_name']}")
        lines.append(f"Status: {str(group['status']).title()}")

        for item in group["items"]:
            title = item.get("product_name") or item.get("title") or "Item"
            qty = item.get("quantity") or 0
            mode = item.get("pricing_mode") or "Custom"
            line_total = item.get("line_total") or "Pending"
            lines.append(f"• {title} × {qty} ({mode}) = {group['shop_currency']} {line_total}")

        latest_sent_quote = group.get("latest_sent_quote")
        if latest_sent_quote and latest_sent_quote.get("total"):
            lines.append(f"Shop total: {group['shop_currency']} {latest_sent_quote['total']}")
            if latest_sent_quote.get("turnaround_hours"):
                lines.append(f"Turnaround: {latest_sent_quote['turnaround_hours']} working hour(s)")
            elif latest_sent_quote.get("turnaround_days"):
                lines.append(f"Turnaround: {latest_sent_quote['turnaround_days']} business day(s)")
            if latest_sent_quote.get("human_ready_text"):
                lines.append(latest_sent_quote["human_ready_text"])
        else:
            lines.append(f"Shop subtotal: {group['shop_currency']} {group['subtotal']}")

    return "\n".join(lines).strip()


def get_quote_draft_file_summary_payload(draft_file: QuoteDraftFile) -> dict:
    """Structured WhatsApp preview payload for grouped quote files."""
    payload = build_dashboard_quote_file_payload(draft_file)
    return {
        "message": get_quote_draft_file_summary_text(draft_file),
        "customer": payload.get("customer", {}),
        "shop_count": payload.get("shop_count", 0),
        "item_count": payload.get("item_count", 0),
        "status": payload.get("status"),
    }
