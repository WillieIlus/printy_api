# Quote Marketplace — Naming Conventions

Clear naming aligned with marketplace workflow concepts.

---

## Concepts

| Concept | Description | API / Code |
|---------|-------------|------------|
| **Quote Requests** | Customer-initiated requests for a quote | `/quote-requests/`, `QuoteRequest` |
| **Incoming Requests** | Quote requests received by a shop | `/shops/<slug>/incoming-requests/` |
| **Sent Quotes** | Quotes a shop has sent to customers | `/sent-quotes/`, `ShopQuote` (model) |
| **Jobs** | Production orders from accepted quotes | `/jobs/` |

---

## Renamed (API Response Fields)

| Old | New | Context |
|-----|-----|---------|
| `latest_quote` | `latest_sent_quote` | Customer: latest quote the shop sent for this request |
| `shop_quotes` | `sent_quotes` | Shop: all quotes sent for this incoming request |
| `has_quote` | `has_sent_quote` | Shop list: whether a quote has been sent |

---

## Renamed (Routes)

| Old | New |
|-----|-----|
| `/shop-quotes/` | `/sent-quotes/` |

---

## Renamed (Accept Action)

| Old | New | Notes |
|-----|-----|-------|
| `shop_quote_id` | `sent_quote_id` | Preferred. `shop_quote_id` still accepted for backwards compat. |

---

## Unchanged (Models / ORM)

| Name | Reason |
|------|--------|
| `QuoteRequest` | Clear; matches "Quote Requests" |
| `ShopQuote` | Model name; DB/ORM unchanged |
| `shop_quotes` (related_name) | ORM; changing would require migrations |
| `shop_quote` (FK on QuoteItem, ProductionOrder) | ORM; unchanged |

---

## Error Messages

| Old | New |
|-----|-----|
| "Not your quote." | "Not your quote request." |
| "Not your quote item." | "Not your quote request item." |
| "shop_quote_id is required." | "sent_quote_id is required." |
