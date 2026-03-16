# Quote Marketplace Domain – Implementation Notes

## Overview

The backend domain structure clearly separates:
- **QuoteRequest** – customer-initiated quote requests
- **ShopQuote** – shop's priced offers in response
- **ProductionOrder (Job)** – accepted orders/jobs

---

## Final Model Fields & Rationale

### QuoteRequest

| Field | Type | Why it exists |
|-------|------|---------------|
| **shop** | FK (required) | Selected shop for the quote. (Optional shop for "direct/broadcast" requests can be added later.) |
| **created_by** | FK User | Requester (buyer) who created the request. |
| **customer_name**, **customer_email**, **customer_phone** | Char/Email | Customer contact; used when no unified Customer record exists. |
| **customer** | FK Customer | Optional link to unified customer (repeat customers). |
| **customer_inquiry** | FK | Optional link to pre-quote inquiry. |
| **status** | Char | Lifecycle: draft → submitted → viewed → quoted → accepted/closed/cancelled. |
| **notes** | Text | Free-form notes from the customer. |
| **delivery_address** | Text | Full delivery address (street, building). |
| **delivery_location** | FK Location | Area/neighborhood (e.g. Westlands, Kilimani) for delivery pricing. |
| **delivery_preference** | Char | Customer choice: pickup or delivery. |
| **created_at**, **updated_at** | DateTime | Timestamps. |

**Derived (no duplication):**
- **Product, quantity, size, gsm, colour mode, print sides** → `QuoteItem` (per line item; paper has gsm; product links to catalog).
- **Finishing services** → `QuoteItemFinishing` + `pricing.FinishingRate` (reused).
- **Delivery/pickup pricing** → `QuoteRequestService` with `ServiceRate` (code=DELIVERY, is_selected=True for delivery).

**Attachments:** `QuoteRequestAttachment` (file, name) – artwork, spec docs.

---

### ShopQuote

| Field | Type | Why it exists |
|-------|------|---------------|
| **quote_request** | FK | Related customer request. |
| **shop** | FK | Shop that sent the quote. |
| **created_by** | FK User | Shop user who created/sent. |
| **status** | Char | sent, revised, accepted, declined, expired. |
| **total** | Decimal | Total price. |
| **pricing_locked_at** | DateTime | When pricing was locked. |
| **sent_at** | DateTime | When quote was sent to customer. |
| **whatsapp_message** | Text | Message sent via WhatsApp. |
| **note** | Text | Shop's note to customer (conditions, clarifications). |
| **turnaround_days** | PositiveInteger | Expected turnaround (e.g. ready in 3 days). |
| **revision_number** | PositiveInteger | Revision count (1 = first, 2 = first revision). |
| **created_at**, **updated_at** | DateTime | Timestamps. |

**Attachments:** `ShopQuoteAttachment` (file, name) – proofs, revised specs.

---

### ProductionOrder (Job)

| Field | Type | Why it exists |
|-------|------|---------------|
| **shop_quote** | FK | Originating accepted quote. |
| **shop** | FK | Shop fulfilling the order. |
| **customer** | FK | Customer for the order. |
| **product** | FK ProductionProduct | Product type (optional link to catalog). |
| **order_number** | Char | Human-readable order ID. |
| **title** | Char | Job title/description. |
| **quantity** | PositiveInteger | Total quantity. |
| **status** | Char | Production: pending, in_progress, ready, completed, cancelled. |
| **delivery_status** | Char | pending, ready_for_pickup, shipped, delivered, n_a (pickup). |
| **delivered_at** | DateTime | When delivered to customer. |
| **due_date**, **completed_at** | Date/DateTime | Scheduling. |
| **notes** | Text | Internal notes. |
| **created_by** | FK User | User who created the order. |

---

## Migrations

| Migration | Purpose |
|-----------|---------|
| `quotes.0006` | Create ShopQuote; add shop_quote FK to QuoteItem and QuoteShareLink |
| `quotes.0007` | Data migration: create ShopQuote from QuoteRequest, map statuses |
| `quotes.0008` | Remove legacy QuoteRequest fields; migrate QuoteShareLink |
| `quotes.0009` | Add delivery_address, delivery_location, delivery_preference, QuoteRequestAttachment, ShopQuoteAttachment; add note, turnaround_days, revision_number to ShopQuote |
| `production.0004` | Add shop_quote FK; migrate status values |
| `production.0005` | Add delivery_status, delivered_at to ProductionOrder |
| `notifications.0001` | Create Notification model |

---

## Relationships

```
QuoteRequest 1──* QuoteItem (product, quantity, size, gsm, sides, color via paper/material)
QuoteRequest 1──* QuoteItemFinishing (via QuoteItem) → pricing.FinishingRate
QuoteRequest 1──* QuoteRequestService → pricing.ServiceRate (DELIVERY, etc.)
QuoteRequest 1──* QuoteRequestAttachment
QuoteRequest 1──* ShopQuote

ShopQuote 1──* QuoteItem (shop_quote, nullable)
ShopQuote 1──* ShopQuoteAttachment
ShopQuote 1──* QuoteShareLink
ShopQuote 1──* ProductionOrder (shop_quote, nullable)
```

---

## Future Work

- **API/views**: Update for new statuses; add ShopQuoteViewSet; move price/send logic to ShopQuote
- **QuoteSharePublicView**: Resolve via shop_quote
- **WhatsApp formatter**: Use ShopQuote for whatsapp_message, sent_at
- **Tests**: Update for new statuses and fields
- **Optional shop**: For "direct/broadcast" requests, make QuoteRequest.shop nullable when that flow is implemented
