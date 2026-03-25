from decimal import Decimal

from quotes.models import QuoteDraftFile, QuoteRequest


def resolve_quote_request_customer_fields(draft: QuoteRequest) -> dict:
    """Resolve the best available customer/company fields from real model data."""
    customer = getattr(draft, "customer", None)
    if customer is not None:
        name = (customer.name or "").strip()
        email = (customer.email or "").strip()
        phone = (customer.phone or "").strip()
    else:
        name = (draft.customer_name or "").strip()
        email = (draft.customer_email or "").strip()
        phone = (draft.customer_phone or "").strip()

    return {
        "company_name": name or "Untitled Company",
        "contact_name": (draft.customer_name or "").strip(),
        "contact_email": email,
        "contact_phone": phone,
    }


def sync_quote_request_from_file(draft_file: QuoteDraftFile, draft: QuoteRequest) -> QuoteRequest:
    """Copy grouped file customer details into the shop-specific draft."""
    contact_name = (draft_file.contact_name or "").strip()
    company_name = (draft_file.company_name or "").strip() or "Untitled Company"

    draft.quote_draft_file = draft_file
    draft.customer_name = contact_name or company_name
    draft.customer_email = draft_file.contact_email or ""
    draft.customer_phone = draft_file.contact_phone or ""
    draft.save(
        update_fields=[
            "quote_draft_file",
            "customer_name",
            "customer_email",
            "customer_phone",
            "updated_at",
        ]
    )
    return draft


def ensure_quote_draft_file(
    *,
    user,
    draft_file: QuoteDraftFile | None = None,
    company_name: str = "",
) -> QuoteDraftFile:
    """Resolve the current open quote draft file for a user."""
    if draft_file is not None:
        return draft_file

    existing = QuoteDraftFile.objects.filter(created_by=user, status=QuoteDraftFile.OPEN).order_by("-updated_at", "-created_at").first()
    if existing:
        return existing

    return QuoteDraftFile.objects.create(
        created_by=user,
        company_name=(company_name or "").strip() or "Untitled Company",
    )


def ensure_quote_draft_file_for_request(
    *,
    user,
    draft: QuoteRequest,
    draft_file: QuoteDraftFile | None = None,
) -> QuoteDraftFile:
    """Get or create the canonical file for a quote request using real customer fields."""
    if draft_file is not None:
        return draft_file

    if draft.quote_draft_file_id:
        return draft.quote_draft_file

    customer_fields = resolve_quote_request_customer_fields(draft)
    existing = (
        QuoteDraftFile.objects.filter(
            created_by=user,
            company_name__iexact=customer_fields["company_name"],
            contact_email__iexact=customer_fields["contact_email"],
            contact_phone=customer_fields["contact_phone"],
        )
        .order_by("-updated_at", "-created_at")
        .first()
    )
    if existing:
        if not existing.contact_name and customer_fields["contact_name"]:
            existing.contact_name = customer_fields["contact_name"]
            existing.save(update_fields=["contact_name", "updated_at"])
        return existing

    return QuoteDraftFile.objects.create(
        created_by=user,
        company_name=customer_fields["company_name"],
        contact_name=customer_fields["contact_name"],
        contact_email=customer_fields["contact_email"],
        contact_phone=customer_fields["contact_phone"],
    )


def build_quote_draft_group_payload(draft: QuoteRequest) -> dict:
    subtotal = Decimal("0")
    items = []

    for item in draft.items.select_related("product").prefetch_related("finishings__finishing_rate").all():
        line_total = item.line_total or Decimal("0")
        subtotal += line_total
        items.append({
            "id": item.id,
            "item_type": item.item_type,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "product": item.product_id,
            "product_name": item.product.name if item.product_id and item.product else "",
            "product_slug": getattr(item.product, "slug", "") if item.product_id and item.product else "",
            "title": item.title,
            "spec_text": item.spec_text,
            "quantity": item.quantity,
            "pricing_mode": item.pricing_mode,
            "chosen_width_mm": item.chosen_width_mm,
            "chosen_height_mm": item.chosen_height_mm,
            "paper": item.paper_id,
            "material": item.material_id,
            "machine": item.machine_id,
            "sides": item.sides,
            "color_mode": item.color_mode,
            "special_instructions": item.special_instructions,
            "finishings": [
                {
                    "finishing_rate": finishing.finishing_rate_id,
                    "finishing_rate_name": finishing.finishing_rate.name if finishing.finishing_rate_id else "",
                }
                for finishing in item.finishings.all()
            ],
            "has_artwork": item.has_artwork,
            "unit_price": str(item.unit_price) if item.unit_price is not None else None,
            "line_total": str(item.line_total) if item.line_total is not None else None,
        })

    latest_shop_quote = draft.get_latest_shop_quote()
    total = latest_shop_quote.total if latest_shop_quote and latest_shop_quote.total is not None else subtotal

    return {
        "draft_id": draft.id,
        "quote_request_id": draft.id,
        "shop_id": draft.shop_id,
        "shop_name": draft.shop.name,
        "shop_slug": draft.shop.slug,
        "shop_currency": draft.shop.currency,
        "status": draft.status,
        "item_count": len(items),
        "items": items,
        "subtotal": str(subtotal.quantize(Decimal("0.01"))),
        "total": str(total.quantize(Decimal("0.01"))) if isinstance(total, Decimal) else str(total),
        "can_recalculate": draft.status == QuoteRequest.DRAFT,
        "can_submit": draft.status == QuoteRequest.DRAFT,
        "latest_sent_quote": (
            {
                "id": latest_shop_quote.id,
                "status": latest_shop_quote.status,
                "total": str(latest_shop_quote.total) if latest_shop_quote.total is not None else None,
                "turnaround_days": latest_shop_quote.turnaround_days,
                "note": latest_shop_quote.note,
                "sent_at": latest_shop_quote.sent_at.isoformat() if latest_shop_quote.sent_at else None,
                "revision_number": latest_shop_quote.revision_number,
                "whatsapp_message": latest_shop_quote.whatsapp_message,
            }
            if latest_shop_quote
            else None
        ),
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
        "created_at": draft.created_at.isoformat() if draft.created_at else None,
    }


def _build_quote_draft_file_payload(draft_file: QuoteDraftFile, *, statuses: list[str] | None = None) -> dict:
    drafts = (
        draft_file.drafts.filter(status__in=statuses) if statuses else draft_file.drafts.all()
    )
    drafts = (
        drafts
        .select_related("shop")
        .prefetch_related("items__product", "items__finishings__finishing_rate", "shop_quotes")
        .order_by("shop__name", "-updated_at", "-created_at")
    )
    shop_groups = [build_quote_draft_group_payload(draft) for draft in drafts]
    customer_label = (draft_file.contact_name or "").strip() or (draft_file.company_name or "").strip() or "Untitled Company"
    total_value = sum(Decimal(group["total"]) for group in shop_groups if group.get("total") is not None)
    customer = {
        "label": customer_label,
        "company_name": draft_file.company_name,
        "contact_name": draft_file.contact_name,
        "contact_email": draft_file.contact_email,
        "contact_phone": draft_file.contact_phone,
    }

    return {
        "id": draft_file.id,
        "file_kind": "grouped_quote_file",
        "customer": customer,
        "customer_name": customer_label,
        "company_name": draft_file.company_name,
        "contact_name": draft_file.contact_name,
        "contact_email": draft_file.contact_email,
        "contact_phone": draft_file.contact_phone,
        "notes": draft_file.notes,
        "status": draft_file.status,
        "shop_count": len(shop_groups),
        "draft_count": len(shop_groups),
        "quote_count": len(shop_groups),
        "item_count": sum(group["item_count"] for group in shop_groups),
        "total_value": str(total_value.quantize(Decimal("0.01"))),
        "has_draft": any(group["status"] == QuoteRequest.DRAFT for group in shop_groups),
        "shop_groups": shop_groups,
        "created_at": draft_file.created_at.isoformat() if draft_file.created_at else None,
        "updated_at": draft_file.updated_at.isoformat() if draft_file.updated_at else None,
    }


def build_quote_draft_file_payload(draft_file: QuoteDraftFile) -> dict:
    return _build_quote_draft_file_payload(draft_file, statuses=[QuoteRequest.DRAFT])


def build_dashboard_quote_file_payload(draft_file: QuoteDraftFile) -> dict:
    return _build_quote_draft_file_payload(
        draft_file,
        statuses=[
            QuoteRequest.DRAFT,
            QuoteRequest.SUBMITTED,
            QuoteRequest.VIEWED,
            QuoteRequest.QUOTED,
            QuoteRequest.ACCEPTED,
        ],
    )
