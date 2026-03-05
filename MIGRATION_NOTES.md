# Migration Notes — printy_api

Scaffolding for incremental feature migration from the old monorepo. **Do not implement features yet** — use this checklist to track what exists and what needs to be migrated.

---

## Shops & Geo (lat/lng + nearby)

- [x] **Shop model**: Add `latitude` and `longitude` (DecimalField, null=True) for geo queries
- [x] **Migration**: Create migration for Shop lat/lng fields + index
- [x] **Serializer**: Expose lat/lng in PublicShopListSerializer and ShopSerializer
- [x] **Shops nearby endpoint**: Implement `GET /api/shops/nearby/?lat=&lng=&radius=10` (bounding box)
- [ ] **Frontend**: `shopsNearby` API path exists in printy_ui; wire to new endpoint
- [x] **Admin**: Add lat/lng to Shop admin for manual entry

---

## Product/Gallery (ex-templates)

- [ ] **Product vs Template**: Catalog uses `Product` (shop-owned); gallery = `public/products/` from all shops
- [ ] **Public products**: `GET /api/public/products/` — PUBLISHED products from pricing-ready shops
- [ ] **Shop catalog**: `GET /api/public/shops/{slug}/catalog/` — products for one shop
- [ ] **Product options**: `GET /api/public/products/{pk}/options/` — tweaking options (paper, finishing, etc.)
- [ ] **Tweak-and-add**: `POST /api/quote-drafts/{id}/tweak-and-add/` — add tweaked product to quote draft
- [ ] **Product status**: `PUBLISHED` / `DRAFT` — ensure migration applied
- [ ] **Price range**: `price_range_est`, `price_hint` on product serializers for gallery cards

---

## Subscription & Payments (M-Pesa STK push)

- [ ] **M-Pesa settings**: All env vars in place (see `docs/env_vars.md`); no views yet
- [ ] **STK push endpoint**: `POST /api/shops/{slug}/payments/mpesa/stk-push/` — initiate payment
- [ ] **Callback endpoint**: `POST /api/payments/mpesa/callback/` — M-Pesa result/timeout webhook (no auth)
- [ ] **Callback logging**: Ensure `payments` logger captures full request body + response (see settings)
- [ ] **Payment model**: Create Payment/Transaction model to store M-Pesa results
- [ ] **Subscription model**: Link shop to plan; FREE_TRIAL_DAYS, DEFAULT_SUBSCRIPTION_PLAN in settings
- [ ] **Plan enforcement**: Middleware or permission to block paid features for expired trials

---

## Sharing/WhatsApp Summary (frontend integration points)

- [ ] **JobShare WhatsApp**: `POST /api/job-requests/{id}/whatsapp-share/` — returns `{ message, public_view_url }`
- [ ] **Job formatter**: `jobs.formatter.format_job_for_whatsapp_share()` — safe, public message
- [ ] **Quote WhatsApp preview**: `POST /api/quotes/{id}/whatsapp-preview/` — returns `{ message }` (staff-only)
- [ ] **Quote send**: `POST /api/quotes/{id}/send/` — marks SENT, stores `whatsapp_message`, locks pricing
- [ ] **Quote formatter**: `quotes.whatsapp_formatter.format_quote_for_whatsapp()` — items, totals, payment terms
- [ ] **Frontend**: Use message + `public_view_url` to build WhatsApp share link (e.g. `wa.me/?text=...`)

---

## General

- [ ] **CORS**: printy.ke, localhost:3000, localhost:5173 (Vite) in `CORS_ALLOWED_ORIGINS`
- [ ] **JWT**: SimpleJWT with access + refresh; blacklist on logout
- [ ] **Logging**: API errors and payment callbacks logged to `api` and `payments` loggers
