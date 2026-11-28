# API Contract — printy_api

Key endpoints and request/response examples. Public API origin: `https://api.printy.ke`. Base path: `/api/`.

> **Route check (audit tier3, 2026-11).** Every `/api/...` path in this file
> was resolved against the live URLconf (`django.urls.resolve`). Paths that
> do not resolve are marked **NOT ROUTED**: calling them returns 404. They
> are kept only as a record of earlier designs; do not build against them.
> `tests/test_audit_documentation.py` fails if an unmarked path in this file
> stops resolving.

---

## Auth (JWT)

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| POST | `/api/auth/token/` | No | Obtain access + refresh token |
| POST | `/api/auth/token/refresh/` | No | Refresh access token |
| GET | `/api/auth/me/` | Bearer | Current user |
| POST | `/api/auth/register/` | No | Register new user |
| POST | `/api/auth/social/google/` | No | Google ID-token login |

### Account security rules (audit #3 / #4)

- `role`, `partner_profile_enabled` and `capability_overrides` are read-only on
  `PATCH /api/auth/me/` and `/api/users/me/` (ignored if sent). Only admin
  surfaces change them.
- Registration accepts `role` only from the self-service allowlist: `client`
  or `partner` (a manager *applicant*, unverified until staff approve it).
  `production`, `super_admin` etc. return 400.
  `/api/auth/register/production/` registers a printer applicant (client role,
  no shop, `signup_intent: "printer_applicant"`); the shop is created later
  with `POST /api/shops/` and stays private until approved.
- Printer-facing marketplace endpoints (`/api/public/shops/`,
  `/api/shops/<slug>/rate-card/`, shop previews, nearby search) require a
  verified, active manager or super_admin; a shop owner sees only its own shop.
- Google login requires `GOOGLE_CLIENT_ID` on the server (503 otherwise),
  verifies the token locally, requires `email_verified`, matches emails
  case-insensitively (409 if legacy duplicates exist), refuses inactive users
  (403) and sets `role` only when creating a new account.
- Credential endpoints are rate-limited per client IP (429 + `Retry-After`).

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
| GET | `/api/public/shops/` | Bearer (verified manager / super_admin) | List active shops |
| GET | `/api/public/shops/{slug}/` | Bearer (verified manager / super_admin) | Shop detail |
| GET | `/api/public/shops/{slug}/catalog/` | Bearer (verified manager / super_admin) | Shop products |
| GET | `/api/public/shops/{slug}/rating-summary/` | — | **NOT ROUTED** |
| GET | `/api/public/products/` | No | All PUBLISHED global catalog products (no shop data) |
| GET | `/api/public/products/{pk}/options/` | No | Product tweaking options |
| GET | `/api/shops/nearby/` | Bearer (verified manager / super_admin) | Shops within radius (Haversine, sorted by distance) |

Despite the `/public/` prefix, the shop endpoints above are **not public**.
Signed-out visitors and clients never see shops; they find managers
(`PrintManagerMarketplaceAccess`). Anonymous callers and clients get 401/403,
and unverified, pending or suspended managers get 403. A printer owner sees
only its own shop.

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

Verified managers and super_admin only (see above).

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

Global catalog products. The response carries **no shop identity**.

**Response:**
```json
{
  "products": [
    {
      "id": 1,
      "name": "Business Card",
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
| GET | `/api/products/gallery/` | — | **NOT ROUTED** (the gallery app was removed; see results.txt Prompt 6 Section B). Use `/api/public/products/`. |

**Former response (endpoint not routed):**
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

**NOT ROUTED:** none of the shop-scoped gallery endpoints below resolve
(the `gallery` stub app was removed in Prompt 6 Section B). Shop catalog
products are managed under `/api/shops/{slug}/products/` (see "Seller").

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET, POST | `/api/shops/{shop_slug}/products/categories/` | List, create categories **NOT ROUTED** |
| GET, PUT, PATCH, DELETE | `/api/shops/{shop_slug}/products/categories/{slug}/` | Category detail **NOT ROUTED** |
| GET, POST | `/api/shops/{shop_slug}/gallery/products/` | List, create products **NOT ROUTED** |
| GET, PUT, PATCH, DELETE | `/api/shops/{shop_slug}/gallery/products/{slug}/` | Product detail **NOT ROUTED** |
| POST | `/api/shops/{shop_slug}/gallery/products/{slug}/calculate-price/` | Calculate price (stub) **NOT ROUTED** |

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
| GET | `/api/quote-drafts/active/?shop={slug}` | Bearer | **Disabled:** always 400 ("Shop-scoped quote drafts are disabled"). Clients use `/api/calculator/drafts/` and `/api/intake/submit/`. |
| GET | `/api/quote-drafts/{id}/items/` | Bearer | List items |
| POST | `/api/quote-drafts/{id}/tweak-and-add/` | Bearer | Add tweaked product |
| PATCH | `/api/tweaked-items/{id}/` | Bearer | Update tweaked item |
| POST | `/api/quote-drafts/{id}/request-quote/` | Bearer | **Disabled:** always 400 (client shop routing is off). |

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
| POST | `/api/quote-requests/{id}/price/` | — | **NOT ROUTED** |

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
| — | — | — | **NOT ROUTED:** every `/api/job-requests/...` and `/api/job-claims/...` row below. |
| GET | `/api/job-requests/` | Bearer | List (filter: status, created_by) **NOT ROUTED** |
| POST | `/api/job-requests/` | Bearer | Create job request **NOT ROUTED** |
| GET | `/api/job-requests/{id}/` | Bearer | Detail **NOT ROUTED** |
| POST | `/api/job-requests/{id}/claims/` | Bearer | Claim job **NOT ROUTED** |
| POST | `/api/job-requests/{id}/whatsapp-share/` | Bearer | Get share message + URL **NOT ROUTED** |
| GET | `/api/job-claims/?claimed_by=me` | Bearer | My claims **NOT ROUTED** |
| POST | `/api/job-claims/{id}/accept/` | Bearer (owner) | Accept claim **NOT ROUTED** |
| POST | `/api/job-claims/{id}/reject/` | Bearer (owner) | Reject claim **NOT ROUTED** |
| GET | `/api/public/job/{token}/` | No | Public job view (token API) |

### POST /api/job-requests/{id}/whatsapp-share/

**Response:**
```json
{
  "message": "📋 *Business Cards*\n\n• Quantity: 500\n\nInterested? Claim this job on Printy.",
  "public_view_url": "https://printy.ke/track-job/abc123..."
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

**NOT ROUTED** (the `FinishingCategory` model no longer exists).

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/finishing-categories/` | No | List categories **NOT ROUTED** |
| GET | `/api/finishing-categories/{slug}/` | No | Category detail **NOT ROUTED** |

---

## Favorites & Ratings

**NOT ROUTED:** none of these endpoints resolve.

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/me/favorites/` | Bearer | List favorites **NOT ROUTED** |
| POST | `/api/me/favorites/` | Bearer | Add favorite `{ "shop": 1 }` **NOT ROUTED** |
| DELETE | `/api/me/favorites/{shop_id}/` | Bearer | Remove favorite **NOT ROUTED** |
| POST | `/api/shops/{id}/rate/` | Bearer | Rate shop (requires eligible QuoteRequest) **NOT ROUTED** |

---

## Setup

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| GET | `/api/setup/status/` | — | **NOT ROUTED** |

---

---

## Subscription (NOT ROUTED)

These are **NOT ROUTED**: `/api/subscription/plans/`, `/api/shops/{shop_slug}/subscription/`,
`/api/shops/{shop_slug}/payments/mpesa/stk-push/` (**NOT ROUTED**). There
is no shop-subscription billing in this codebase. The former documentation
for them was removed in audit tier3.

---

## Payments (M-Pesa)

Canonical lifecycle and operations: `docs/DARAJA_PRODUCTION_CHECKLIST.md`.
URL list, response changes and `checkout_request_id` exposure:
`docs/API_URL_AUDIT.md`.

| Method | Endpoint | Auth | Description |
|--------|----------|------|--------------|
| POST | `/api/quotes/{quote_id}/accept/` | Bearer (client allowed to accept) | Accept the quote and create the `Payment` (amount = `QuoteFinancialSplit.client_total`) |
| POST | `/api/payments/stk-push/` | Bearer (payer) | STK push for a quote's canonical `Payment` |
| GET | `/api/payments/{pk}/` | Bearer (payer / platform staff) | Payment detail |
| POST | `/api/managed-jobs/{pk}/payments/mpesa/stk-push/` | Bearer (the job's client) | STK push for a managed job |
| POST | `/api/managed-jobs/{pk}/payments/mpesa/query/` | Bearer (the job's client) | Local STK status by `checkout_request_id` (does not call Daraja) |
| POST | `/api/payments/mpesa/stk-push/` | Bearer | Cashier STK push: `managed_job_id`, `quote_id` or an ad-hoc `amount` |
| GET | `/api/payments/mpesa/{pk}/` | Bearer | Cashier payment detail (no `checkout_request_id`) |
| POST | `/api/payments/mpesa/callback/<secret>/` | Secret + IP allowlist (webhook) | Daraja STK callback |
| POST | `/api/payments/mpesa/result/<secret>/` | Secret + IP allowlist (webhook) | Transaction Status Query result |
| POST | `/api/payments/mpesa-callback/<secret>/` | Secret + IP allowlist (webhook) | Legacy STK callback (same gate) |
| GET | `/api/admin/payments/needs-review/` | Bearer (super-admin) | Payments awaiting a decision |
| POST | `/api/admin/payments/{id}/resolve-review/` | Bearer (super-admin) | `confirm_paid` / `mark_failed` a canonical `Payment` |
| POST | `/api/admin/mpesa-payments/{id}/resolve-review/` | Bearer (super-admin) | Same for a cashier `MpesaPayment` |
| POST | `/api/admin/payments/{id}/recover/` | Bearer (super-admin) | Query Daraja (STK Query) for a canonical `Payment` whose callback was lost; body `{reason?}`; returns `{payment_id, outcome, payment_status, detail, checkout_request_id}`; 502 on Daraja error (no change) |

Phone accepts `254712345678`, `0712345678` and `712345678`; the backend
normalizes to `2547XXXXXXXX`.

### Callbacks (Daraja webhooks)

There is no user authentication and no CSRF check. Instead, every callback
must pass all three checks:

1. **Secret:** the last path segment (or `?token=`) equals
   `MPESA_CALLBACK_SECRET`.
2. **Source IP:** the source is on the allowlist (`MPESA_CALLBACK_ALLOWED_IPS`,
   or the built-in Safaricom list in production). The IP is resolved through
   `MPESA_TRUSTED_PROXIES`; a raw `X-Forwarded-For` is never trusted.
3. **Known transaction:** the body references a `CheckoutRequestID` (or
   receipt) we persisted.

| Response | When |
| --- | --- |
| 403 `{"ResultCode": 1, "ResultDesc": "Forbidden"}` | bad or missing secret, or IP not allowlisted |
| 404 | unknown `CheckoutRequestID` |
| 400 | missing `CheckoutRequestID`, or non-integer `ResultCode` |
| 200 | applied, duplicate, or already final |

A success is applied only with a receipt **and** an `Amount` equal to the
server-stored expected amount. Otherwise the payment becomes `needs_review`
and is never `paid`. Duplicate callbacks change nothing. A late failure for an
old prompt never touches a newer one.

### Error responses on payment initiation

| Status | Meaning |
| --- | --- |
| 400 `artwork_payment_block_reason` | artwork not approved yet (the artwork gate) |
| 400 | payment under review or already paid; `quote_request_id` sent (pay the quote with `quote_id`) |
| 403 | caller is not the payer (not the job's client, or cannot accept the quote) |
| 409 | the job is already paid, or has no confirmed price |

`checkout_request_id` / `merchant_request_id` are **not** returned by the
cashier serializer, the signed-out client-link payment, the client dashboard
or the partner send-to-client payload. They are still returned to the
authenticated payer by `/api/payments/stk-push/`, `/api/payments/{pk}/` and
the managed-job STK push.

**Payments callback logging:** uses `logging.getLogger("payments")` and logs
request identifiers and the source IP, never the callback secret. The secret
does appear in proxy access logs; mask it there (see `docs/DEPLOY_NOTES.md`).
