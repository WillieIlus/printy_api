# API Contract — printy_api

Key endpoints and request/response examples. Base URL: `/api/` (e.g. `https://printy.ke/api/`).

---

## Auth (JWT)

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| POST | `/api/auth/token/` | No | Obtain access + refresh token |
| POST | `/api/auth/token/refresh/` | No | Refresh access token |
| GET | `/api/auth/me/` | Bearer | Current user |
| POST | `/api/auth/register/` | No | Register new user |

### POST /api/auth/token/

**Request:**
```json
{
  "email": "user@example.com",
  "password": "secret"
}
```

**Response:**
```json
{
  "access": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
}
```

**Headers for authenticated requests:**
```
Authorization: Bearer <access_token>
```

---

## Public Shops & Products

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/public/shops/` | No | List active shops |
| GET | `/api/public/shops/{slug}/` | No | Shop detail |
| GET | `/api/public/shops/{slug}/catalog/` | No | Shop products |
| GET | `/api/public/shops/{slug}/rating-summary/` | No | Rating average + count |
| GET | `/api/public/products/` | No | All PUBLISHED products (gallery) |
| GET | `/api/public/products/{pk}/options/` | No | Product tweaking options |
| GET | `/api/shops/nearby/` | No | Shops within radius (Haversine, sorted by distance) |

### GET /api/shops/nearby/

**Query params:** `lat` (required), `lng` (required), `radius` (optional, default 10, max 500 km).

Bounding box pre-filter, then Haversine distance. Results sorted by distance ascending. Exact radius filter applied.

**Response:**
```json
{
  "results": [
    {
      "id": 1,
      "name": "Print Shop Nairobi",
      "slug": "print-shop-nairobi",
      "currency": "KES",
      "latitude": "-1.292066",
      "longitude": "36.821945",
      "distance_km": 0.12
    }
  ]
}
```

Returns only active shops with non-null lat/lng within the exact radius. Missing or invalid params return `{"results": []}`.

### GET /api/public/shops/

**Response (paginated):**
```json
{
  "count": 10,
  "next": "https://.../api/public/shops/?page=2",
  "previous": null,
  "results": [
    {
      "id": 1,
      "name": "Print Shop Nairobi",
      "slug": "print-shop-nairobi",
      "currency": "KES",
      "latitude": "-1.292066",
      "longitude": "36.821945"
    }
  ]
}
```

### GET /api/public/shops/{slug}/catalog/

**Response:**
```json
{
  "shop": { "id": 1, "name": "...", "slug": "...", "currency": "KES" },
  "products": [
    {
      "id": 1,
      "name": "Business Card",
      "category": "Business Cards",
      "pricing_mode": "SHEET",
      "default_finished_width_mm": 90,
      "default_finished_height_mm": 55,
      "min_quantity": 100,
      "price_hint": { "can_calculate": true, "min_price": 5000 },
      "price_range_est": { "lowest": { "total": 5000 }, "price_display": "From KES 5,000" },
      "finishing_options": [...],
      "images": [...]
    }
  ]
}
```

### GET /api/public/products/

**Response:**
```json
{
  "products": [
    {
      "id": 1,
      "name": "Business Card",
      "shop": { "id": 1, "name": "...", "slug": "...", "currency": "KES" },
      "price_hint": {...},
      "price_range_est": {...},
      ...
    }
  ]
}
```

---

## Product Gallery

Product Gallery (ported from templates): categories and products for browsing. Supports global (`shop=null`) and shop-scoped items.

### Public

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/products/gallery/` | No | Categories with active products, grouped |

**Response:**
```json
{
  "categories": [
    {
      "category": {
        "id": 1,
        "name": "Business Cards",
        "slug": "business-cards",
        "icon_svg_path": "",
        "description": ""
      },
      "products": [
        {
          "id": 1,
          "title": "Premium Business Card",
          "slug": "premium-business-card",
          "description": "",
          "preview_image": null,
          "dimensions_label": "90 × 55 mm",
          "weight_label": "350gsm",
          "is_popular": false,
          "is_best_value": false,
          "is_new": false
        }
      ]
    }
  ]
}
```

Only active products are included. Categories with no active products are omitted.

### Shop-scoped (Seller)

Shop owners/managers manage their gallery categories and products. All require Bearer auth and shop ownership.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET, POST | `/api/shops/{shop_slug}/products/categories/` | List, create categories |
| GET, PUT, PATCH, DELETE | `/api/shops/{shop_slug}/products/categories/{slug}/` | Category detail |
| GET, POST | `/api/shops/{shop_slug}/gallery/products/` | List, create products |
| GET, PUT, PATCH, DELETE | `/api/shops/{shop_slug}/gallery/products/{slug}/` | Product detail |
| POST | `/api/shops/{shop_slug}/gallery/products/{slug}/calculate-price/` | Calculate price (stub) |

**Note:** Gallery products use `/gallery/products/` to avoid clashing with catalog products at `/shops/{slug}/products/` (catalog uses integer `pk`).

### POST calculate-price (stub)

**Request:** JSON body (validated as object; specific fields TBD).

**Response:**
```json
{
  "product_id": 1,
  "product_slug": "business-card",
  "breakdown": {
    "material": 0,
    "printing": 0,
    "finishing": 0,
    "total": 0
  },
  "message": "Calculate-price stub. Implement with pricing logic."
}
```

Invalid payload (e.g. non-object) returns `400 Bad Request`.

---

## Quote Drafts (Buyer)

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/quote-drafts/active/?shop={slug}` | Bearer | Get/create active draft |
| GET | `/api/quote-drafts/{id}/items/` | Bearer | List items |
| POST | `/api/quote-drafts/{id}/tweak-and-add/` | Bearer | Add tweaked product |
| PATCH | `/api/tweaked-items/{id}/` | Bearer | Update tweaked item |
| POST | `/api/quote-drafts/{id}/request-quote/` | Bearer | Submit quote request |

### POST /api/quote-drafts/{id}/tweak-and-add/

**Request:**
```json
{
  "product": 1,
  "quantity": 500,
  "paper": 2,
  "sides": "DUPLEX",
  "finishings": [{"finishing_rate": 1, "coverage_qty": 1}]
}
```

---

## Quote Requests

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/quote-requests/` | Bearer | List (buyer or seller) |
| GET | `/api/quote-requests/{id}/` | Bearer | Detail |
| POST | `/api/quote-requests/{id}/price/` | Bearer (seller) | Calculate & lock prices |

---

## Staff Quotes

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/quotes/` | Bearer (staff) | List quotes |
| POST | `/api/quotes/{id}/whatsapp-preview/` | Bearer (staff) | Get WhatsApp message |
| POST | `/api/quotes/{id}/send/` | Bearer (staff) | Mark SENT, store message |

### POST /api/quotes/{id}/whatsapp-preview/

**Response:**
```json
{
  "message": "Hi John,\n\nHere is your quote:\n\n..."
}
```

### POST /api/quotes/{id}/share/

Create a shareable link for a quote. Returns URL and WhatsApp-ready message.

**Optional request body:**
```json
{
  "expires_at": "2025-12-31T23:59:59Z"
}
```

**Response:**
```json
{
  "share_url": "https://printy.ke/share/abc123...",
  "whatsapp_text": "Hi John,\n\nHere is your quote:\n\n• Business Card (90×55mm) × 500 pcs — SRA3 300gsm Gloss — Lamination = KES 12,500\n\nTotal: KES 12,500\n\nTurnaround: 2-3 business days\n\nView full quote: https://printy.ke/share/abc123...\n\nBest regards,\nPrint Shop"
}
```

### GET /api/share/{token}/

Public quote summary (no auth). Token must be valid and not expired.

**Response:**
```json
{
  "id": 1,
  "shop_name": "Print Shop Nairobi",
  "customer_name": "John Doe",
  "status": "SENT",
  "total": "12500.00",
  "items": [
    {
      "product_name": "Business Card",
      "title": "",
      "quantity": 500,
      "size_label": "90×55mm",
      "sides": "DUPLEX",
      "finishing_label": "Lamination",
      "line_total": "12500.00"
    }
  ]
}
```

Expired links return `410 Gone`.

---

## Job Share (Overflow Work)

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/job-requests/` | Bearer | List (filter: status, created_by) |
| POST | `/api/job-requests/` | Bearer | Create job request |
| GET | `/api/job-requests/{id}/` | Bearer | Detail |
| POST | `/api/job-requests/{id}/claims/` | Bearer | Claim job |
| POST | `/api/job-requests/{id}/whatsapp-share/` | Bearer | Get share message + URL |
| GET | `/api/job-claims/?claimed_by=me` | Bearer | My claims |
| POST | `/api/job-claims/{id}/accept/` | Bearer (owner) | Accept claim |
| POST | `/api/job-claims/{id}/reject/` | Bearer (owner) | Reject claim |
| GET | `/api/public/job/{token}/` | No | Public job view (token) |

### POST /api/job-requests/{id}/whatsapp-share/

**Response:**
```json
{
  "message": "📋 *Business Cards*\n\n• Quantity: 500\n\nInterested? Claim this job on Printy.",
  "public_view_url": "https://printy.ke/public/job/abc123..."
}
```

---

## Seller (Shop-Scoped)

All under `/api/shops/{slug}/` or `/api/shops/{id}/`:

| Resource | Methods | Description |
|----------|---------|--------------|
| `machines/` | GET, POST | Printing machines |
| `papers/` | GET, POST | Paper stock |
| `finishing-rates/` | GET, POST | Finishing services |
| `materials/` | GET, POST | Large-format materials |
| `products/` | GET, POST | Catalog products |
| `products/{pk}/images/` | GET, POST | Product images |

---

## Finishing Categories

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/finishing-categories/` | No | List categories |
| GET | `/api/finishing-categories/{slug}/` | No | Category detail |

---

## Favorites & Ratings

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/me/favorites/` | Bearer | List favorites |
| POST | `/api/me/favorites/` | Bearer | Add favorite `{ "shop": 1 }` |
| DELETE | `/api/me/favorites/{shop_id}/` | Bearer | Remove favorite |
| POST | `/api/shops/{id}/rate/` | Bearer | Rate shop (requires eligible QuoteRequest) |

---

## Setup

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/setup/status/` | Bearer | Onboarding status |

---

---

## Subscription & Payments

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/subscription/plans/` | No | List subscription plans |
| GET | `/api/shops/{shop_slug}/subscription/` | Bearer (shop owner) | Shop's subscription (creates TRIAL if none) |
| POST | `/api/shops/{shop_slug}/payments/mpesa/stk-push/` | Bearer (shop owner) | Initiate M-Pesa STK push |
| POST | `/api/payments/mpesa/callback/` | No (webhook) | Daraja STK callback |

### GET /api/subscription/plans/

**Response (paginated):**
```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 1,
      "name": "Starter",
      "price": "500.00",
      "billing_period": "MONTHLY",
      "days_in_period": 30
    }
  ]
}
```

### GET /api/shops/{shop_slug}/subscription/

**Response:**
```json
{
  "id": 1,
  "shop": 1,
  "plan": { "id": 1, "name": "Starter", "price": "500.00", "billing_period": "MONTHLY", "days_in_period": 30 },
  "status": "ACTIVE",
  "period_start": "2025-03-02",
  "period_end": "2025-04-01",
  "next_billing_date": "2025-04-01",
  "last_payment_date": "2025-03-02"
}
```

Status: `TRIAL`, `ACTIVE`, `PAST_DUE`, `CANCELLED`.

### POST /api/shops/{shop_slug}/payments/mpesa/stk-push/

**Request:**
```json
{
  "phone": "254712345678",
  "plan_id": 1
}
```

Phone accepts: `254712345678`, `0712345678`, `712345678` — normalized to `2547XXXXXXXX`.

**Response:**
```json
{
  "checkout_request_id": "ws_CO_123456",
  "message": "Payment request sent. Complete on your phone."
}
```

### POST /api/payments/mpesa/callback/

Daraja webhook (no auth, CSRF exempt). Callback behavior:

- `ResultCode == 0`: Mark STK request SUCCESS, extract `MpesaReceiptNumber`, activate subscription, set period dates, create Payment row.
- `ResultCode != 0`: Mark STK request FAILED.
- Idempotent: duplicate callbacks (same `CheckoutRequestID`) do not create duplicate Payments.

**Payments callback logging:** Uses `logging.getLogger("payments")` to log the full request body.
