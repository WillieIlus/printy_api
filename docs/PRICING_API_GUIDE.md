# Pricing & Shop API Guide

This document explains the purpose of each pricing-related API endpoint and how to use them in the frontend.

---

## Quick Reference: Which URL for What?

| Use Case | Endpoint | Frontend Usage |
|----------|----------|----------------|
| **Add/edit printing rates** (per machine) | `machines/<machine_id>/printing-rates/` | Pricing setup → Printing tab |
| **Add/edit finishing rates** | `shops/<slug>/finishing-rates/` | Setup → Finishing tab, Products |
| **Public rate card** (buyer view) | `shops/<slug>/rate-card/` | Shop public page, quote calculator |
| **Buyer rates a shop** (stars + comment) | `shops/<shop_id>/rate/` | Post-quote rating form |

---

## Endpoint Details

### 1. `machines/<int:machine_id>/printing-rates/` (CRUD)

**Purpose:** Per-machine printing rates (single/double price per sheet size and color mode).

- **GET** – List printing rates for a machine
- **POST** – Create a new rate (sheet_size, color_mode, single_price, double_price)
- **PUT/PATCH/DELETE** – Update or delete a rate

**Frontend usage:**
- **Pricing page** (`/dashboard/shops/[slug]/pricing`) – Printing tab: list machines, then for each machine manage printing rates via `API.sellerMachinePrintingRates(machineId)`
- Used by `printy_ui/app/services/seller.ts` – `listMachinePrintingRates`, `createMachinePrintingRate`

**Note:** The unified pricing store also uses `shops/<slug>/pricing/printing-prices/` for a different structure. Prefer `machines/.../printing-rates/` when editing machine-specific rates.

---

### 2. `shops/<slug:shop_slug>/finishing-rates/` (CRUD)

**Purpose:** Shop-level finishing rates (lamination, cutting, binding, etc.).

- **GET** – List finishing rates (optionally filter by `?category=<slug>`)
- **POST** – Create finishing rate (category, name, price, setup_fee)
- **PUT/PATCH/DELETE** – Update or delete

**Frontend usage:**
- **Setup → Finishing tab** – Add/edit finishing services
- **Products** – When creating a product, attach finishing options from these rates
- Used by `SetupFinishing.vue`, `SetupProducts.vue`, `ProductTweakModal.vue`
- API path: `API.shopFinishingRates(slug)`

---

### 3. `shops/<slug:shop_slug>/rate-card/` (GET, public)

**Purpose:** Public rate card for buyers. No auth required.

Returns:
- **Printing** – sheet size, color mode, single/double price
- **Paper** – combined paper + printing per sheet (buyers see single/double; owners see breakdown)
- **Finishing** – finishing services with prices

**Frontend usage:**
- **Shop public page** – Display rate card for buyers
- **Quote calculator** – Fetch prices when building a quote
- Used by `pricingStore` – `API.shopRateCard(slug)`
- Component: `RateCardDisplay`

---

### 4. `shops/<int:shop_id>/rate/` (POST, buyer)

**Purpose:** Buyer rates a shop (stars 1–5 + optional comment).

**Requirements:** User must have at least one QuoteRequest for that shop with status `SENT` or `ACCEPTED`.

**Frontend usage:**
- **Post-quote rating** – After a buyer receives a quote, show a “Rate this shop” form
- Call `API.shopRate(shopId)` with `{ stars, comment }`
- Use `shop_id` (integer), not slug

---

## Slug vs ID

| Endpoint | Uses slug | Uses ID |
|----------|-----------|---------|
| `shops/<slug>/finishing-rates/` | ✓ | |
| `shops/<slug>/rate-card/` | ✓ | |
| `shops/<id>/finishing-rates/` | | ✓ (alternative) |
| `shops/<id>/rate/` | | ✓ (required) |
| `machines/<id>/printing-rates/` | | ✓ (machine_id) |

**Recommendation:** Prefer slug-based URLs (`shops/<slug>/...`) for shop-scoped resources. Use ID only when the API requires it (e.g. `shop-rate`).

---

## Unified Pricing APIs (alternative structure)

The app also has a newer pricing layer:

| Endpoint | Purpose |
|----------|---------|
| `shops/<slug>/pricing/printing-prices/` | Unified printing prices (shop-level) |
| `shops/<slug>/pricing/finishing/` | Finishing services (pricing layer) |
| `shops/<slug>/pricing/material-prices/` | Large-format material prices |
| `shops/<slug>/paper/` | Paper stock + selling prices |
| `shops/<slug>/pricing/status/` | `has_pricing`, `pricing_ready` |
| `shops/<slug>/pricing/seed-defaults/` | Load starter defaults |

The **rate-card** endpoint aggregates data from both the legacy (`finishing-rates`, `printing-rates`) and unified pricing layers for public display.
