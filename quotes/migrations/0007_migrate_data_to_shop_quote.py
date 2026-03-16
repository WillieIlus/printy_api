# Data migration: create ShopQuote from QuoteRequest, migrate statuses, link QuoteShareLink and QuoteItems

from django.db import migrations


def create_shop_quotes_and_migrate(apps, schema_editor):
    QuoteRequest = apps.get_model("quotes", "QuoteRequest")
    ShopQuote = apps.get_model("quotes", "ShopQuote")
    QuoteItem = apps.get_model("quotes", "QuoteItem")
    QuoteShareLink = apps.get_model("quotes", "QuoteShareLink")

    # Map old QuoteRequest status -> new QuoteRequest status
    REQUEST_STATUS_MAP = {
        "DRAFT": "draft",
        "SUBMITTED": "submitted",
        "PRICED": "quoted",
        "SENT": "quoted",
        "ACCEPTED": "accepted",
        "REJECTED": "closed",
        "EXPIRED": "closed",
    }

    # Map old QuoteRequest status -> ShopQuote status
    SHOP_QUOTE_STATUS_MAP = {
        "PRICED": "sent",
        "SENT": "sent",
        "ACCEPTED": "accepted",
        "REJECTED": "declined",
        "EXPIRED": "expired",
    }

    needs_shop_quote = {"PRICED", "SENT", "ACCEPTED", "REJECTED", "EXPIRED"}

    for qr in QuoteRequest.objects.all():
        old_status = qr.status
        new_status = REQUEST_STATUS_MAP.get(old_status, "draft")
        qr.status = new_status
        qr.save(update_fields=["status"])

        shop_quote = None
        if old_status in needs_shop_quote:
            sq_status = SHOP_QUOTE_STATUS_MAP.get(old_status, "sent")
            shop_quote = ShopQuote.objects.create(
                quote_request=qr,
                shop=qr.shop,
                created_by=qr.created_by,
                status=sq_status,
                total=getattr(qr, "total", None),
                pricing_locked_at=getattr(qr, "pricing_locked_at", None),
                sent_at=getattr(qr, "sent_at", None),
                whatsapp_message=getattr(qr, "whatsapp_message", "") or "",
            )
            # Link QuoteItems to ShopQuote
            QuoteItem.objects.filter(quote_request=qr).update(shop_quote=shop_quote)

        # Migrate QuoteShareLinks: point to shop_quote
        links = QuoteShareLink.objects.filter(quote_request=qr)
        if links.exists() and shop_quote is None:
            # Share link exists but no ShopQuote yet (e.g. draft with share link - rare)
            shop_quote = ShopQuote.objects.create(
                quote_request=qr,
                shop=qr.shop,
                created_by=qr.created_by,
                status="sent",
                total=getattr(qr, "total", None),
                pricing_locked_at=getattr(qr, "pricing_locked_at", None),
                sent_at=getattr(qr, "sent_at", None),
                whatsapp_message=getattr(qr, "whatsapp_message", "") or "",
            )
            QuoteItem.objects.filter(quote_request=qr).update(shop_quote=shop_quote)
        if shop_quote:
            links.update(shop_quote=shop_quote)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("quotes", "0006_add_shop_quote_model"),
    ]

    operations = [
        migrations.RunPython(create_shop_quotes_and_migrate, noop),
    ]
