# Quote Summary Service

WhatsApp-ready summary generation for quote requests and shop quote responses. Reusable by frontend, API, and future integrations (e.g. WhatsApp Business API).

---

## Service Location

`quotes/summary_service.py`

---

## Functions

| Function | Use |
|----------|-----|
| `format_quote_request_summary(quote_request, items=None, include_price=False)` | Customer quote request summary (specs only by default) |
| `format_shop_quote_summary(shop_quote, company_name="", company_phone="", share_url=None)` | Shop quote response summary (prices, turnaround, location) |
| `get_quote_request_summary_text(quote_request)` | Convenience: uses latest shop quote if available, else request-only |
| `get_shop_quote_summary_text(shop_quote, share_url=None)` | Convenience: shop quote summary with shop name/phone |

---

## Example Outputs

### Customer Quote Request Summary

```
Quote Request #42 — PrintPro Nairobi

From: Jane Doe

• Business Cards (90×55mm) × 500 pcs — SRA3 300gsm Gloss — Color — Lamination
• Flyers A5 × 1000 pcs — 128gsm Matt — Color

Location: Nairobi CBD

Notes: Need by Friday. Logo attached.

Attachments: 2 file(s)
```

### Shop Quote Response Summary

```
Hi Jane Doe,

Here is your quote:

• Business Cards (90×55mm) × 500 pcs — SRA3 300gsm Gloss — Color — Lamination = KES 3,500.00
• Flyers A5 × 1000 pcs — 128gsm Matt — Color = KES 8,200.00

Total: KES 11,700.00

Turnaround: 2-3 business days

Delivery: Nairobi CBD

Best regards,
PrintPro Nairobi
+254 700 123 456
```

---

## Included Fields

| Field | Request Summary | Shop Quote Summary |
|-------|-----------------|--------------------|
| Product | ✓ | ✓ |
| Quantity | ✓ | ✓ |
| Size | ✓ | ✓ |
| GSM / Paper | ✓ | ✓ |
| Colour | ✓ | ✓ |
| Finishing | ✓ | ✓ |
| Location | ✓ | ✓ |
| Price | Optional | ✓ |
| Turnaround | — | ✓ |

---

## Integration Points

### Serializers

- **QuoteRequestCustomerDetailSerializer** — `whatsapp_summary` (via `get_quote_request_summary_text`)
- **QuoteRequestShopDetailSerializer** — `whatsapp_summary`
- **ShopQuoteDetailSerializer** — `whatsapp_summary` (uses stored `whatsapp_message` if set, else `get_shop_quote_summary_text`)

### Views

- **send_quote** — On send, `get_shop_quote_summary_text()` is called and stored in `ShopQuote.whatsapp_message`
- **ShopQuoteViewSet.partial_update** — On revise, `whatsapp_message` is regenerated and saved

---

## Attachment Reuse

Attachments are **not** embedded in the summary text. The summary mentions attachment count only (e.g. `Attachments: 2 file(s)`). To reuse attachments for sharing or PDF generation:

### Quote Request Attachments

```python
# All attachments on a quote request
for att in quote_request.attachments.all():
    # att.file — FileField (e.g. artwork, spec PDF)
    # att.name — Optional display name
    url = att.file.url  # or att.file.path for local
```

Storage path: `quote_requests/%Y/%m/` (e.g. `quote_requests/2025/03/filename.pdf`)

### Shop Quote Attachments

```python
# All attachments on a shop quote
for att in shop_quote.attachments.all():
    # att.file — FileField (e.g. proof, revised spec)
    # att.name — Optional display name
    url = att.file.url
```

Storage path: `shop_quotes/%Y/%m/`

### Combining Summary + Attachments

For WhatsApp or email sharing, you can:

1. Generate summary text via `get_shop_quote_summary_text(shop_quote)`
2. Attach files from `shop_quote.attachments.all()` or `quote_request.attachments.all()`
3. Send as message body + file attachments

---

## Optional Preview Endpoint

To preview the summary before sharing, you can add:

- `GET /quote-requests/{id}/whatsapp-preview/` — returns `{"summary": "..."}` for the request (or latest shop quote)
- `GET /sent-quotes/{id}/whatsapp-preview/` — returns `{"summary": "..."}` for a specific shop quote

The serializers already expose `whatsapp_summary` on detail responses, so a dedicated preview endpoint is optional.
