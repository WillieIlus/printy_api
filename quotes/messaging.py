from decimal import Decimal, InvalidOperation
import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from api.visibility import CLIENT_ACTOR, project_identity
from quotes.models import QuoteRequestMessage

logger = logging.getLogger(__name__)


def _safe_email_error(exc: Exception) -> str:
    message = str(exc).strip()
    return (message or "Email delivery failed.")[:255]


def _frontend_base_url() -> str:
    return getattr(settings, "FRONTEND_URL", "").rstrip("/")


def _default_action_url(*, quote_request, quote=None, recipient_role: str) -> str:
    if recipient_role == QuoteRequestMessage.RecipientRole.SHOP_OWNER:
        if quote:
            return f"/dashboard/shop/requests/{quote_request.id}/quote/{quote.id}"
        return f"/dashboard/shop/requests/{quote_request.id}"
    if quote:
        return f"/dashboard/client/requests/{quote_request.id}/quote/{quote.id}"
    return f"/dashboard/client/requests/{quote_request.id}"


def _absolute_url(path: str) -> str:
    if not path:
        return _frontend_base_url()
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return f"{_frontend_base_url()}{path}"


def _default_subject(*, quote_request, message_type: str, recipient_role: str = "") -> str:
    raw_shop_name = getattr(getattr(quote_request, "shop", None), "name", "") or "Shop"
    
    # Project shop name if recipient is client
    is_client_recipient = recipient_role == QuoteRequestMessage.RecipientRole.CLIENT
    actor = CLIENT_ACTOR if is_client_recipient else "ops" # Ops or Shop see raw name
    shop_name = project_identity(raw_shop_name, actor=actor)
    
    client_name = quote_request.customer_name or getattr(getattr(quote_request, "created_by", None), "email", "") or "client"
    if message_type == QuoteRequestMessage.MessageType.QUOTE_REQUEST_CREATED:
        return f"New quote request from {client_name}"
    if message_type == QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT:
        return f"{shop_name} sent you a quote"
    if message_type == QuoteRequestMessage.MessageType.QUOTE_QUESTION:
        return f"Question about request #{quote_request.id}"
    if message_type == QuoteRequestMessage.MessageType.QUOTE_ACCEPTED:
        return f"Quote accepted by {client_name}"
    if message_type == QuoteRequestMessage.MessageType.QUOTE_REJECTED:
        return f"{shop_name} declined this quote request"
    if message_type == QuoteRequestMessage.MessageType.EMAIL_DELIVERY_FAILED:
        return "Email delivery failed"
    return f"Printy update for request #{quote_request.id}"


def _first_item(quote_request):
    return quote_request.items.select_related("product", "paper").prefetch_related("finishings__finishing_rate").order_by("id").first()


def _job_type(quote_request):
    item = _first_item(quote_request)
    if not item:
        return "Print job"
    if item.product_id:
        return item.product.name
    return item.title or "Custom print job"


def _size_text(quote_request):
    item = _first_item(quote_request)
    if not item:
        return "To be confirmed"
    if item.chosen_width_mm and item.chosen_height_mm:
        return f"{item.chosen_width_mm} × {item.chosen_height_mm} mm"
    return item.spec_text or "To be confirmed"


def _quantity_text(quote_request):
    item = _first_item(quote_request)
    if not item or not item.quantity:
        return "To be confirmed"
    return str(item.quantity)


def _paper_text(quote_request):
    item = _first_item(quote_request)
    if not item or not item.paper_id:
        return "To be confirmed"
    paper = item.paper
    return f"{paper.sheet_size} {paper.gsm}gsm {paper.get_paper_type_display()}"


def _finishing_text(quote_request):
    item = _first_item(quote_request)
    if not item:
        return "None listed"
    names = [fin.finishing_rate.name for fin in item.finishings.select_related("finishing_rate").all() if fin.finishing_rate_id]
    return ", ".join(names) if names else "None listed"


def _artwork_text(quote_request):
    if quote_request.attachments.exists():
        return "Artwork attached"
    item = _first_item(quote_request)
    if item and item.attachments.exists():
        return "Artwork available"
    return "No artwork attached"


def _normalize_decimal(value):
    if value in (None, "", []):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _format_money(value, currency="KES"):
    amount = _normalize_decimal(value)
    if amount is None:
        return "To be confirmed"
    return f"{currency} {amount:,.2f}"


def _quote_price_text(quote):
    snapshot = quote.response_snapshot or {}
    if quote.total is not None:
        return _format_money(quote.total, getattr(quote.shop, "currency", "KES") or "KES")
    price_min = snapshot.get("price_min")
    price_max = snapshot.get("price_max")
    if price_min is not None or price_max is not None:
        if price_min is not None and price_max is not None and str(price_min) != str(price_max):
            currency = getattr(quote.shop, "currency", "KES") or "KES"
            return f"{_format_money(price_min, currency)} - {_format_money(price_max, currency)}"
    return "To be confirmed"


def _list_text(value):
    if not value:
        return "None listed"
    if isinstance(value, str):
        return value
    return ", ".join(str(entry) for entry in value if entry)


def _build_email_payload(message):
    quote_request = message.quote_request
    quote = message.quote
    default_action_url = _absolute_url((message.metadata or {}).get("action_url", ""))
    
    raw_shop_name = getattr(getattr(quote_request, "shop", None), "name", "") or "Print shop"
    is_client_recipient = message.recipient_role == QuoteRequestMessage.RecipientRole.CLIENT
    actor = CLIENT_ACTOR if is_client_recipient else "ops"
    shop_name = project_identity(raw_shop_name, actor=actor)
    
    common = {
        "brand_name": "Printy",
        "preheader": message.subject,
        "subject": message.subject,
        "greeting_name": quote_request.customer_name or "there",
        "shop_name": shop_name,
        "client_name": quote_request.customer_name or "Client",
        "request_id": quote_request.id,
        "request_reference": quote_request.request_reference or f"QR-{quote_request.id}",
        "cta_url": default_action_url,
        "footer_note": "Generated by Printy",
    }
    if message.message_type == QuoteRequestMessage.MessageType.QUOTE_REQUEST_CREATED and message.recipient_role == QuoteRequestMessage.RecipientRole.SHOP_OWNER:
        common["cta_url"] = f"{_frontend_base_url()}/dashboard/shop/requests/{quote_request.id}"
        common.update(
            {
                "headline": message.subject,
                "greeting": f"Hello {common['shop_name']},",
                "intro": "A new buyer request is waiting in Printy.",
                "summary_rows": [
                    {"label": "Buyer", "value": common["client_name"]},
                    {"label": "Job type", "value": _job_type(quote_request)},
                    {"label": "Quantity", "value": _quantity_text(quote_request)},
                    {"label": "Size", "value": _size_text(quote_request)},
                    {"label": "Paper / grammage", "value": _paper_text(quote_request)},
                    {"label": "Finishing", "value": _finishing_text(quote_request)},
                    {"label": "Artwork", "value": _artwork_text(quote_request)},
                    {"label": "Notes", "value": quote_request.notes or "No extra notes"},
                ],
                "cta_label": "View request",
                "support_copy": "Respond inside Printy so the client can compare your quote clearly.",
            }
        )
        return {
            "subject": message.subject,
            "html_template": "emails/quotes/quote_request_created.html",
            "text_template": "emails/quotes/quote_request_created.txt",
            "context": common,
        }
    if message.message_type == QuoteRequestMessage.MessageType.QUOTE_RESPONSE_SENT and message.recipient_role == QuoteRequestMessage.RecipientRole.CLIENT and quote:
        snapshot = quote.response_snapshot or {}
        common["cta_url"] = f"{_frontend_base_url()}/dashboard/client/requests/{quote_request.id}"
        common.update(
            {
                "headline": message.subject,
                "greeting": f"Hello {common['client_name']},",
                "intro": "A shop responded to your request in Printy.",
                "summary_rows": [
                    {"label": "Shop", "value": common["shop_name"]},
                    {"label": "Quoted amount", "value": _quote_price_text(quote)},
                    {"label": "Turnaround", "value": quote.turnaround_label or quote.human_ready_text or "To be confirmed"},
                    {"label": "Included specs", "value": _list_text(snapshot.get("confirmed_specs"))},
                    {"label": "Needs confirmation", "value": _list_text(snapshot.get("needs_confirmation"))},
                    {"label": "Shop note", "value": quote.note or "No extra note"},
                ],
                "cta_label": "View quote",
                "support_copy": "Nothing is final until you accept a quote.",
            }
        )
        return {
            "subject": message.subject,
            "html_template": "emails/quotes/quote_response_sent.html",
            "text_template": "emails/quotes/quote_response_sent.txt",
            "context": common,
        }
    if message.message_type == QuoteRequestMessage.MessageType.QUOTE_ACCEPTED and message.recipient_role == QuoteRequestMessage.RecipientRole.SHOP_OWNER and quote:
        common["cta_url"] = f"{_frontend_base_url()}/dashboard/shop/requests/{quote_request.id}"
        common.update(
            {
                "headline": message.subject,
                "greeting": f"Hello {common['shop_name']},",
                "intro": "A client accepted your quote in Printy.",
                "summary_rows": [
                    {"label": "Client", "value": common["client_name"]},
                    {"label": "Job", "value": _job_type(quote_request)},
                    {"label": "Accepted quote", "value": _quote_price_text(quote)},
                    {"label": "Turnaround", "value": quote.turnaround_label or quote.human_ready_text or "To be confirmed"},
                    {"label": "Next step", "value": "Open the request in Printy and move the work into production."},
                ],
                "cta_label": "Open job",
                "support_copy": "The request is still tracked in Printy as the source of truth.",
            }
        )
        return {
            "subject": message.subject,
            "html_template": "emails/quotes/quote_accepted.html",
            "text_template": "emails/quotes/quote_accepted.txt",
            "context": common,
        }
    return None


def _send_email_for_message(message):
    payload = _build_email_payload(message)
    if payload is None:
        email = EmailMultiAlternatives(
            subject=message.subject or "Printy quote update",
            body=message.body or message.subject or "Printy quote update",
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=[message.recipient_email],
        )
        email.send(fail_silently=False)
        return

    text_body = render_to_string(payload["text_template"], payload["context"])
    html_body = render_to_string(payload["html_template"], payload["context"])
    email = EmailMultiAlternatives(
        subject=payload["subject"],
        body=text_body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[message.recipient_email],
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)


def create_quote_message(
    *,
    quote_request,
    sender=None,
    recipient=None,
    sender_role=QuoteRequestMessage.SenderRole.SYSTEM,
    recipient_role=QuoteRequestMessage.RecipientRole.SYSTEM,
    message_kind=QuoteRequestMessage.MessageKind.NOTE,
    message_type=QuoteRequestMessage.MessageType.SYSTEM_NOTICE,
    direction=QuoteRequestMessage.Direction.INBOUND,
    subject="",
    body="",
    quote=None,
    recipient_email="",
    metadata=None,
    send_email_copy=False,
    create_failure_notice=False,
):
    payload = dict(metadata or {})
    payload.setdefault(
        "action_url",
        _default_action_url(
            quote_request=quote_request,
            quote=quote,
            recipient_role=recipient_role,
        ),
    )
    message = QuoteRequestMessage.objects.create(
        quote_request=quote_request,
        quote=quote,
        sender=sender,
        recipient=recipient,
        shop=getattr(quote_request, "shop", None),
        recipient_email=recipient_email or getattr(recipient, "email", "") or "",
        sender_role=sender_role,
        recipient_role=recipient_role,
        message_kind=message_kind,
        message_type=message_type,
        direction=direction,
        subject=subject or _default_subject(quote_request=quote_request, message_type=message_type, recipient_role=recipient_role),
        body=body or "",
        sent_at=timezone.now(),
        metadata=payload,
    )
    if send_email_copy and message.recipient_email:
        try:
            _send_email_for_message(message)
        except Exception as exc:
            error_text = _safe_email_error(exc)
            logger.warning(
                "Quote message email failed for quote_request=%s recipient=%s type=%s: %s",
                quote_request.id,
                message.recipient_email,
                message.message_type,
                error_text,
            )
            message.email_status = QuoteRequestMessage.EmailStatus.FAILED
            message.email_error = error_text
            message.email_sent = False
            message.save(update_fields=["email_status", "email_error", "email_sent", "updated_at"])
            if create_failure_notice:
                QuoteRequestMessage.objects.create(
                    quote_request=quote_request,
                    quote=quote,
                    sender=None,
                    recipient=recipient,
                    shop=getattr(quote_request, "shop", None),
                    sender_role=QuoteRequestMessage.SenderRole.SYSTEM,
                    recipient_role=recipient_role,
                    message_kind=QuoteRequestMessage.MessageKind.STATUS,
                    message_type=QuoteRequestMessage.MessageType.EMAIL_DELIVERY_FAILED,
                    direction=direction,
                    subject="Email delivery failed",
                    body="Email delivery failed, but the quote request is saved in Printy.",
                    sent_at=timezone.now(),
                    metadata={
                        "delivery_error": error_text,
                        "action_url": payload.get("action_url", ""),
                    },
                )
            return message
        message.email_status = QuoteRequestMessage.EmailStatus.SENT
        message.email_error = ""
        message.email_sent = True
        message.save(update_fields=["email_status", "email_error", "email_sent", "updated_at"])
    return message


def mark_message_read(message):
    if message.read_at is None:
        message.read_at = timezone.now()
        message.save(update_fields=["read_at", "updated_at"])
    return message


def mark_messages_read(queryset):
    unread_queryset = queryset.filter(read_at__isnull=True)
    timestamp = timezone.now()
    return unread_queryset.update(read_at=timestamp, updated_at=timestamp)
