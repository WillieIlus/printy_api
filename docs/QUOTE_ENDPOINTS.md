# Quote Marketplace API — Endpoint Map

REST-friendly endpoints with clear separation between customer and shop flows. All require JWT auth unless noted.

---

## A. Customer Actions (`/quote-requests/`)

| Method | Endpoint | Action |
|--------|----------|--------|
| POST | `/quote-requests/` | Create quote request (draft) |
| GET | `/quote-requests/` | List my quote requests (customer) |
| GET | `/quote-requests/{id}/` | View one quote request |
| PATCH | `/quote-requests/{id}/` | Update draft (only when status=draft) |
| POST | `/quote-requests/{id}/submit/` | Submit draft → submitted |
| POST | `/quote-requests/{id}/accept/` | Accept sent quote (body: `{"sent_quote_id": <id>}`) |
| POST | `/quote-requests/{id}/cancel/` | Cancel request (draft or submitted) |

**Permissions:** `IsAuthenticated`, `IsQuoteRequestBuyer`. Queryset filtered by `created_by=request.user`.

---

## B. Shop Actions

### Incoming Quote Requests (`/shops/<slug>/incoming-requests/`)

| Method | Endpoint | Action |
|--------|----------|--------|
| GET | `/shops/<slug>/incoming-requests/` | List incoming quote requests |
| GET | `/shops/<slug>/incoming-requests/{id}/` | View quote request detail |
| POST | `/shops/<slug>/incoming-requests/{id}/send-quote/` | Send shop quote (body: `{"total", "note", "turnaround_days"}`) |
| POST | `/shops/<slug>/incoming-requests/{id}/mark-viewed/` | Mark request as viewed |
| POST | `/shops/<slug>/incoming-requests/{id}/decline/` | Decline request |

**Permissions:** `IsAuthenticated`, `IsQuoteRequestSeller`. Shop owner only. Staff can access any shop.

### Sent Quotes (`/sent-quotes/`)

| Method | Endpoint | Action |
|--------|----------|--------|
| GET | `/sent-quotes/` | List shop's sent quotes |
| GET | `/sent-quotes/{id}/` | View sent quote detail |
| PATCH | `/sent-quotes/{id}/` | Revise quote (note, turnaround_days, total) |
| POST | `/sent-quotes/{id}/create-job/` | Create production job from accepted quote |

**Permissions:** `IsAuthenticated`, `IsShopQuoteOwner`. Shop owner only. Staff can access any.

---

## C. Shared / Job Actions (`/jobs/`)

| Method | Endpoint | Action |
|--------|----------|--------|
| GET | `/jobs/` | List jobs: shop via `?shop=<slug>` or owned shop; customer via `?as_customer=1` |
| GET | `/jobs/{id}/` | View job detail (shop or customer from accepted quote) |
| POST | `/jobs/` | Create job (shop only; optionally with `shop_quote` for create-from-quote) |
| PATCH | `/jobs/{id}/` | Update job (shop only) |
| GET | `/jobs/{id}/processes/` | List job processes |
| POST | `/jobs/{id}/processes/` | Add job process (shop only) |

**Permissions:** `IsJobCustomerOrShopOwner`. Shop: full CRUD. Customer: read-only via `?as_customer=1`.

---

## Supporting Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/quote-requests/{id}/items/` | List/create quote request items (customer only) |
| `/quote-requests/{id}/items/{item_id}/` | Get/update/delete item (customer only) |
| `/quote-requests/{id}/attachments/` | List/create attachments (customer: add when draft; shop: list) |
| `/quote-requests/{id}/attachments/{pk}/` | Get/delete attachment |
| `/sent-quotes/{id}/attachments/` | List/create shop quote attachments (shop owner) |
| `/sent-quotes/{id}/attachments/{pk}/` | Get/delete shop quote attachment |
| `/quote-drafts/` | Active-draft UX (get-or-create per shop) |
| `/quote-drafts/active/?shop=<slug>` | Get or create active draft |

---

## Auth

- JWT required for all quote/job endpoints
- Use `Authorization: Bearer <token>` header
- Obtain token via `/api/auth/token/` (or configured auth endpoint)
