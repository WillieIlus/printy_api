# Quote Marketplace Serializers — Responsibilities

## Overview

Serializers are split by **customer** vs **shop** to avoid exposing shop internals to customers and to give shops the data they need to respond quickly.

---

## QuoteRequest

### Customer-facing

| Serializer | Use | Fields |
|------------|-----|--------|
| **QuoteRequestCustomerCreateSerializer** | POST create draft | shop, customer_name, customer_email, customer_phone, notes, delivery_preference, delivery_address, delivery_location |
| **QuoteRequestCustomerUpdateSerializer** | PATCH draft | customer_name, customer_email, customer_phone, notes, delivery_preference, delivery_address, delivery_location |
| **QuoteRequestCustomerListSerializer** | GET list (own requests) | id, shop, shop_name, shop_slug, shop_currency, status, items_count, latest_sent_quote, created_at |
| **QuoteRequestCustomerDetailSerializer** | GET detail (own request) | Full request + items (no pricing_snapshot) + services + latest_sent_quote summary |

**Validation:** Only draft can be updated. Shop must be active.

### Shop-facing

| Serializer | Use | Fields |
|------------|-----|--------|
| **QuoteRequestShopListSerializer** | GET list (incoming) | id, shop, customer_name, status, delivery_preference, items_count, has_sent_quote, created_at |
| **QuoteRequestShopDetailSerializer** | GET detail (incoming) | Full request + items (with pricing_snapshot, needs_review) + services + sent_quotes |

**Validation:** Ownership enforced in view (shop.owner == user).

---

## ShopQuote

| Serializer | Use | Fields |
|------------|-----|--------|
| **ShopQuoteCreateSerializer** | POST create/send | total, note, turnaround_days (quote_request from context) |
| **ShopQuoteUpdateSerializer** | PATCH revise | note, turnaround_days, total |
| **ShopQuoteListSerializer** | GET list | id, quote_request_id, shop, status, total, turnaround_days, revision_number, sent_at |
| **ShopQuoteDetailSerializer** | GET detail | Full quote + items + quote_request summary |

**Validation:** Only sent/revised quotes can be updated. Revision number auto-incremented on create.

---

## ProductionOrder (Job)

| Serializer | Use | Fields |
|------------|-----|--------|
| **ProductionOrderListSerializer** | GET list | id, shop_quote, customer, order_number, title, quantity, status, delivery_status, due_date, completed_at, delivered_at |
| **ProductionOrderSerializer** | GET detail | Full order + processes + shop_quote_total |
| **ProductionOrderWriteSerializer** | POST/PATCH | customer, product, order_number, title, quantity, status, delivery_status, delivered_at, due_date, completed_at, notes |

---

## Quote Share (Public)

| Serializer | Use | Fields |
|------------|-----|--------|
| **QuoteSharePublicSerializer** | GET /share/{token}/ | id, shop_name, customer_name, status, total, turnaround_days, note, items (no internal IDs) |

Works with **ShopQuote** (share links point to shop_quote). No auth required.

---

## View Selection Logic

- **QuoteRequestViewSet**: List uses `QuoteRequestCustomerListSerializer`. Detail uses `QuoteRequestCustomerDetailSerializer` when `created_by == user`, else `QuoteRequestShopDetailSerializer`.
- **QuoteRequestCreateSerializer** = `QuoteRequestCustomerCreateSerializer`
- **QuoteRequestPatchSerializer** = `QuoteRequestCustomerUpdateSerializer`
- **QuoteDetailSerializer** (staff) = `QuoteRequestShopDetailSerializer`
