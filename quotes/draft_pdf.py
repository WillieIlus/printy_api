from io import BytesIO

from quotes.draft_files import (
    build_dashboard_quote_file_payload,
    build_quote_draft_file_payload,
    build_quote_draft_group_payload,
)
from quotes.models import QuoteDraftFile, QuoteRequest


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _render_simple_pdf(lines: list[str]) -> bytes:
    """Fallback PDF renderer when reportlab is unavailable."""
    sanitized = [line if line else " " for line in lines]
    content_lines = ["BT", "/F1 12 Tf", "50 780 Td", "14 TL"]
    for index, line in enumerate(sanitized):
        if index == 0:
            content_lines.append(f"({_escape_pdf_text(line)}) Tj")
        else:
            content_lines.append("T*")
            content_lines.append(f"({_escape_pdf_text(line)}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
        f"5 0 obj << /Length {len(content)} >> stream\n".encode("latin-1") + content + b"\nendstream endobj",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
        pdf.extend(b"\n")
    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("latin-1"))
    pdf.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF"
        ).encode("latin-1")
    )
    return bytes(pdf)


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="PrintyHeading", parent=styles["Heading1"], fontSize=18, leading=22, textColor=colors.HexColor("#101828"), spaceAfter=8))
    styles.add(ParagraphStyle(name="PrintySection", parent=styles["Heading2"], fontSize=13, leading=16, textColor=colors.HexColor("#E13515"), spaceAfter=6))
    styles.add(ParagraphStyle(name="PrintyBody", parent=styles["BodyText"], fontSize=9.5, leading=13, textColor=colors.HexColor("#334155")))
    styles.add(ParagraphStyle(name="PrintyMeta", parent=styles["BodyText"], fontSize=8.5, leading=11, textColor=colors.HexColor("#64748B")))
    return styles


def _draft_group_story(group: dict, styles) -> list:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    story = [
        Paragraph(group["shop_name"], styles["PrintySection"]),
        Paragraph(f"Status: {group['status'].title()}", styles["PrintyMeta"]),
        Paragraph(f"Subtotal: {group['shop_currency']} {group['subtotal']}", styles["PrintyBody"]),
        Spacer(1, 3 * mm),
    ]

    table_data = [["Item", "Qty", "Mode", "Total"]]
    for item in group["items"]:
        title = item["product_name"] or item["title"] or "Draft item"
        table_data.append([
            title,
            str(item["quantity"]),
            item.get("pricing_mode") or "-",
            item.get("line_total") or "Pending",
        ])

    table = Table(table_data, colWidths=[90 * mm, 18 * mm, 28 * mm, 30 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F8FAFC")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#0F172A")),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#CBD5E1")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F8FAFC")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(table)
    latest_sent_quote = group.get("latest_sent_quote")
    if latest_sent_quote:
        turnaround = latest_sent_quote.get("turnaround_hours")
        note = latest_sent_quote.get("note")
        story.append(Spacer(1, 3 * mm))
        story.append(
            Paragraph(
                f"Latest sent quote: {group['shop_currency']} {latest_sent_quote.get('total') or group['subtotal']}",
                styles["PrintyBody"],
            )
        )
        if turnaround:
            story.append(Paragraph(f"Turnaround: {turnaround} working hour(s)", styles["PrintyMeta"]))
        if latest_sent_quote.get("human_ready_text"):
            story.append(Paragraph(latest_sent_quote["human_ready_text"], styles["PrintyMeta"]))
        if note:
            story.append(Paragraph(f"Note: {note}", styles["PrintyMeta"]))
    story.append(Spacer(1, 6 * mm))
    return story


def render_quote_draft_pdf(draft: QuoteRequest) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        group = build_quote_draft_group_payload(draft)
        lines = [
            "Printy Quote Draft",
            f"Shop: {group['shop_name']}",
            f"Currency: {group['shop_currency']}",
            "",
        ]
        for item in group["items"]:
            title = item["product_name"] or item["title"] or "Draft item"
            lines.append(f"{title} x {item['quantity']} = {item.get('line_total') or 'Pending'}")
        lines.append("")
        lines.append(f"Subtotal: {group['shop_currency']} {group['subtotal']}")
        return _render_simple_pdf(lines)

    buffer = BytesIO()
    styles = _styles()
    group = build_quote_draft_group_payload(draft)

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [
        Paragraph("Printy Quote Draft", styles["PrintyHeading"]),
        Paragraph(f"Shop: {group['shop_name']}", styles["PrintyMeta"]),
        Paragraph(f"Currency: {group['shop_currency']}", styles["PrintyMeta"]),
        Spacer(1, 6 * mm),
    ]
    story.extend(_draft_group_story(group, styles))
    doc.build(story)
    return buffer.getvalue()


def render_quote_draft_file_pdf(draft_file: QuoteDraftFile) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        payload = build_quote_draft_file_payload(draft_file)
        lines = [payload["company_name"], f"Quote file with {payload['shop_count']} shop section(s)", ""]
        for group in payload["shop_groups"]:
            lines.append(group["shop_name"])
            lines.append(f"Subtotal: {group['shop_currency']} {group['subtotal']}")
            for item in group["items"]:
                title = item["product_name"] or item["title"] or "Draft item"
                lines.append(f"- {title} x {item['quantity']} = {item.get('line_total') or 'Pending'}")
            lines.append("")
        return _render_simple_pdf(lines)

    buffer = BytesIO()
    styles = _styles()
    payload = build_quote_draft_file_payload(draft_file)

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [
        Paragraph(payload["company_name"], styles["PrintyHeading"]),
        Paragraph(f"Quote file with {payload['shop_count']} shop section(s)", styles["PrintyMeta"]),
        Spacer(1, 6 * mm),
    ]

    for group in payload["shop_groups"]:
        story.extend(_draft_group_story(group, styles))

    doc.build(story)
    return buffer.getvalue()


def render_dashboard_quote_file_pdf(draft_file: QuoteDraftFile) -> bytes:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        payload = build_dashboard_quote_file_payload(draft_file)
        lines = [payload["customer_name"], f"Quote file with {payload['shop_count']} shop section(s)"]
        if payload.get("contact_email"):
            lines.append(f"Email: {payload['contact_email']}")
        if payload.get("contact_phone"):
            lines.append(f"Phone: {payload['contact_phone']}")
        lines.append("")
        for group in payload["shop_groups"]:
            lines.append(group["shop_name"])
            lines.append(f"Status: {group['status']}")
            lines.append(f"Subtotal: {group['shop_currency']} {group['subtotal']}")
            for item in group["items"]:
                title = item["product_name"] or item["title"] or "Item"
                lines.append(f"- {title} x {item['quantity']} = {item.get('line_total') or 'Pending'}")
            latest_sent_quote = group.get("latest_sent_quote") or {}
            if latest_sent_quote.get("total"):
                lines.append(f"Latest sent quote total: {group['shop_currency']} {latest_sent_quote['total']}")
            lines.append("")
        return _render_simple_pdf(lines)

    buffer = BytesIO()
    styles = _styles()
    payload = build_dashboard_quote_file_payload(draft_file)

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [
        Paragraph(payload["customer_name"], styles["PrintyHeading"]),
        Paragraph(f"Quote file with {payload['shop_count']} shop section(s)", styles["PrintyMeta"]),
    ]

    if payload.get("contact_email"):
        story.append(Paragraph(f"Email: {payload['contact_email']}", styles["PrintyMeta"]))
    if payload.get("contact_phone"):
        story.append(Paragraph(f"Phone: {payload['contact_phone']}", styles["PrintyMeta"]))
    story.append(Spacer(1, 6 * mm))

    for group in payload["shop_groups"]:
        story.extend(_draft_group_story(group, styles))

    doc.build(story)
    return buffer.getvalue()
