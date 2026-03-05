"""
WhatsAppQuoteFormatter — generates plain-text WhatsApp messages for quotes.

Input: Quote (QuoteRequest), QuoteItems, company settings (name, phone, turnaround).
Output: whatsapp_message string (short, professional, no internal costs).
"""
from decimal import Decimal
from typing import Optional

from quotes.models import QuoteItem, QuoteRequest


def _format_price(value) -> str:
    """Format decimal/None as KES string."""
    if value is None:
        return "0"
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    return str(value)


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


def _item_product_label(item: QuoteItem) -> str:
    """Product name or custom title."""
    if item.item_type == "PRODUCT" and item.product_id:
        return item.product.name
    return item.title or "Item"


def _item_paper_label(item: QuoteItem) -> str:
    """Paper spec: e.g. SRA3 300gsm Gloss."""
    if not item.paper_id:
        return ""
    p = item.paper
    if not p:
        return ""
    parts = [p.sheet_size or "", f"{p.gsm}gsm" if p.gsm else "", p.paper_type or ""]
    return " ".join(x for x in parts if x).strip()


def _item_finishing_label(item: QuoteItem) -> str:
    """Comma-separated finishing names."""
    names = []
    for qif in item.finishings.select_related("finishing_rate").all():
        if qif.finishing_rate:
            names.append(qif.finishing_rate.name)
    return ", ".join(names) if names else ""


def format_quote_for_whatsapp(
    quote: QuoteRequest,
    items: Optional[list] = None,
    *,
    company_name: str = "",
    company_phone: str = "",
    turnaround: str = "2-3 business days",
    payment_terms: Optional[str] = None,
    share_url: Optional[str] = None,
) -> str:
    """
    Generate a plain-text WhatsApp message for a quote.

    Args:
        quote: QuoteRequest (header).
        items: List of QuoteItem (default: quote.items.all()).
        company_name: Shop/company display name.
        company_phone: Contact phone.
        turnaround: Turnaround time (e.g. "2-3 business days").
        payment_terms: Optional line (e.g. "50% deposit, balance on delivery").
        share_url: Optional shareable URL to append (e.g. for share links).

    Returns:
        Plain text message, short and professional. No internal costs.
    """
    if items is None:
        items = list(quote.items.select_related("product", "paper", "material").prefetch_related(
            "finishings__finishing_rate"
        ).all())

    lines = []

    # Greeting
    if quote.customer_name:
        lines.append(f"Hi {quote.customer_name},")
    lines.append("")
    lines.append("Here is your quote:")
    lines.append("")

    # Items
    for item in items:
        product_label = _item_product_label(item)
        size_label = _item_size_label(item)
        qty = item.quantity or 1
        paper_label = _item_paper_label(item)
        finishing_label = _item_finishing_label(item)
        line_total = item.line_total

        parts = [f"• {product_label}"]
        if size_label:
            parts.append(f"({size_label})")
        parts.append(f"× {qty} pcs")
        if paper_label:
            parts.append(f"— {paper_label}")
        if finishing_label:
            parts.append(f"— {finishing_label}")
        parts.append(f"= KES {_format_price(line_total)}")
        lines.append(" ".join(parts))

    # Total (use quote.total or sum of item line_totals)
    total = quote.total
    if total is None and items:
        total = sum((item.line_total or 0) for item in items)
    if total is not None:
        lines.append("")
        lines.append(f"Total: KES {_format_price(total)}")
    lines.append("")

    # Turnaround
    lines.append(f"Turnaround: {turnaround}")
    lines.append("")

    # Optional payment terms
    if payment_terms:
        lines.append(f"Payment: {payment_terms}")
        lines.append("")

    # Share URL (for shareable quotes)
    if share_url:
        lines.append("")
        lines.append(f"View full quote: {share_url}")

    # Sign-off
    if company_name:
        lines.append("")
        lines.append(f"Best regards,")
        lines.append(company_name)
        if company_phone:
            lines.append(company_phone)

    return "\n".join(lines).strip()
