# Quote Attachment Audit

## Summary

**Models exist.** `QuoteRequestAttachment` and `ShopQuoteAttachment` are implemented with `FileField` and `name`. **API exposure was missing.** This audit adds minimal REST endpoints and serializer integration.

---

## Existing Infrastructure

### Models (quotes/models.py)

| Model | Parent | Fields | Storage |
|-------|--------|--------|---------|
| **QuoteRequestAttachment** | QuoteRequest (FK) | `file`, `name` | `quote_requests/%Y/%m/` |
| **ShopQuoteAttachment** | ShopQuote (FK) | `file`, `name` | `shop_quotes/%Y/%m/` |

- `file`: `FileField` — accepts PDF, images, documents
- `name`: optional display name (max 255)
- CASCADE delete when parent is deleted

### Admin

- `QuoteRequestAttachmentInline` on QuoteRequest admin
- `ShopQuoteAttachmentInline` on ShopQuote admin

### Summary Service

- `format_quote_request_summary()` includes `Attachments: N file(s)` when `quote_request.attachments.count() > 0`

---

## What Was Missing

| Layer | Status |
|-------|--------|
| Serializers | ❌ Not exposed |
| API views | ❌ No endpoints |
| URL routes | ❌ None |
| Detail responses | ❌ Attachments not included |

---

## Implementation (Minimal Extension)

### Endpoints

| Method | Endpoint | Who | Notes |
|--------|----------|-----|-------|
| GET | `/quote-requests/{id}/attachments/` | Customer (own) or Shop (incoming) | List attachments |
| POST | `/quote-requests/{id}/attachments/` | Customer only | Draft only; multipart/form-data |
| DELETE | `/quote-requests/{id}/attachments/{pk}/` | Customer only | Draft only |
| GET | `/sent-quotes/{id}/attachments/` | Shop owner | List attachments |
| POST | `/sent-quotes/{id}/attachments/` | Shop owner | multipart/form-data |
| DELETE | `/sent-quotes/{id}/attachments/{pk}/` | Shop owner | — |

### Security

- **Quote request attachments**: Customer = `created_by`; Shop = `shop.owner`. Write (POST/DELETE) only when draft.
- **Shop quote attachments**: Shop owner only (`shop.owner`).

### Serializer Fields

- Read: `id`, `file`, `name`, `created_at`
- Upload: `file` (required), `name` (optional)

---

## Next-Step: PDF Generation

For "quote summary + optional attachment" sharing (e.g. WhatsApp):

1. **Use existing summary**: `get_shop_quote_summary_text(shop_quote)` → plain text body.
2. **Attach files**: `shop_quote.attachments.all()` or `quote_request.attachments.all()` → send as multipart.
3. **Optional PDF**: If you need a single PDF (summary + embedded files):
   - Use `reportlab` or `weasyprint` to render summary text.
   - Append existing PDFs from attachments (e.g. `PyPDF2`/`pypdf`).
   - Store result in `ShopQuoteAttachment` or a transient file; serve via signed URL.
   - Keep PDF generation in a separate service — do not extend attachment models for it.
