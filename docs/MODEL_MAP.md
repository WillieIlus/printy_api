# Printy Model Map

Clean model structure for shop discovery, product/location SEO, and print quoting context.

**Architectural rule:** Store real entities in the database. Generate SEO combinations from real entities. Avoid redundant models.

---

## A. Discovery

| Model | App | Purpose |
|-------|-----|---------|
| **Shop** | shops | Print shop (seller's business). Owns all shop-scoped resources. FK to Location for SEO. |
| **ShopBranch** | shops | *(Optional, future)* Multi-branch shops. For now, Shop has single address. |
| **Location** | locations | Geographic area for SEO (neighborhood, city, county). Hierarchical via parent FK. |
| **ProductCategory** | catalog | Product category. `shop=null` = global; `shop` set = shop-specific. |
| **Product** | catalog | Product in shop catalog. Shop-scoped. FK to ProductCategory. |
| **ShopProductOffering** | — | *Not needed.* Product IS the offering; Product.shop FK. |

### Current relationships

```
Shop (shops)
    location FK → Location (nullable, for SEO)
    owner FK → User

Location (locations)
    parent FK → self (for hierarchy: Westlands → Nairobi)

ProductCategory (catalog)
    shop FK → Shop (nullable; null = global category)

Product (catalog)
    shop FK → Shop
    category FK → ProductCategory (nullable)
```

### SEO page generation

- **Location pages:** `/locations/{slug}` — shops in `Location.shops` (Shop.location FK)
- **Product pages:** `/products/{slug}` — products in shop (Product.shop)
- **Shop pages:** `/shops/{slug}` — shops
- **Product + Location:** `/locations/{loc}/products/{prod}` — derived: `Shop.objects.filter(location=loc).filter(products__slug=prod)` — no permanent table

---

## B. Pricing / Quote Context

| Model | App | Purpose |
|-------|-----|---------|
| **PaperType** | — | *Choice enum* (inventory.PaperType). Not a table. |
| **SheetSize** | — | *Choice enum* (inventory.SheetSize). Not a table. |
| **ProductionPaperSize** | inventory | Parent sheet sizes (SRA3, B2). Used for imposition. |
| **GSMOption** | — | *Attribute* on Paper (gsm integer). Not a table. |
| **ColorMode** | — | *Choice enum* (pricing.ColorMode). Not a table. |
| **PrintSideOption** | — | *Choice enum* (pricing.Sides: SIMPLEX/DUPLEX). Not a table. |
| **FinishingCategory** | pricing | Category for finishing (Lamination, Binding). |
| **FinishingRate** | pricing | Shop-scoped finishing rate. FK to FinishingCategory. |
| **PrintingRate** | pricing | Machine + sheet_size + color_mode. Per-sheet pricing. |
| **PricingRule** | — | *Not a separate model.* PrintingRate + FinishingRate + VolumeDiscount. |

### Current relationships

```
Machine (inventory)
    shop FK → Shop

Paper (inventory)
    shop FK → Shop
    production_size FK → ProductionPaperSize
    sheet_size (CharField, choices)
    gsm (int)
    paper_type (CharField, choices)

PrintingRate (pricing)
    machine FK → Machine
    sheet_size (CharField)
    color_mode (CharField)

FinishingRate (pricing)
    shop FK → Shop
    category FK → FinishingCategory

Material (pricing)
    shop FK → Shop
```

---

## C. Conversion / Workflow

| Model | App | Purpose |
|-------|-----|---------|
| **QuoteRequest** | quotes | Buyer's quote request. Shop-scoped. Status: DRAFT → SENT → ACCEPTED. |
| **QuoteItem** | quotes | Line item in quote. PRODUCT or CUSTOM. Direct FKs to Paper/Material/Machine/FinishingRate. |
| **Quote** | — | *Alias for QuoteRequest.* Same entity. |
| **ProductionOrder** | production | Production order (order fulfillment). Shop-scoped. Formerly Job. |
| **JobRequest** | jobs | Overflow job sharing (marketplace). Not shop-scoped. |
| **OpeningHours** | shops | Per-weekday hours. Shop-scoped. |
| **ShopContact** | — | *Inline on Shop.* business_email, phone_number. No separate model. |
| **SocialLink** | — | *(Future)* Optional. Not needed for MVP. |

### Current relationships

```
QuoteRequest (quotes)
    shop FK → Shop
    created_by FK → User (nullable)

QuoteItem (quotes)
    quote_request FK → QuoteRequest
    product FK → Product (nullable, for PRODUCT type)
    paper FK → Paper (nullable)
    material FK → Material (nullable)
    machine FK → Machine (nullable)
    finishings M2M via QuoteItemFinishing → FinishingRate

ProductionOrder (production)
    shop FK → Shop
    customer FK → Customer (production)
    product FK → ProductionProduct (production)

JobRequest (jobs)
    created_by FK → User
    location (CharField) — free text, not FK to Location
```

### Quote → ProductionOrder conversion (future)

- Add `quote_request FK` (nullable) to `production.ProductionOrder` to link accepted quote to production order.
- Not required for current model map.

---

## D. SEO / Context

**No permanent tables for SEO combinations.**

- **Canonical entities:** Shop, Location, Product, ProductCategory
- **Derived:** Product + Location → filter `Shop.objects.filter(location=loc).filter(products__slug=prod)`
- **Sitemap:** Generate from Location + ProductCategory + Product slugs

---

## App Boundaries

| App | Models |
|-----|--------|
| **shops** | Shop, OpeningHours, FavoriteShop, ShopRating |
| **locations** | Location |
| **catalog** | ProductCategory, Product, Imposition, ProductFinishingOption, ProductImage |
| **inventory** | ProductionPaperSize, FinalPaperSize, Machine, Paper |
| **pricing** | FinishingCategory, PrintingRate, FinishingRate, Material, ServiceRate, ServiceRateTier, VolumeDiscount |
| **quotes** | CustomerInquiry, QuoteRequest, QuoteItem, QuoteItemFinishing, QuoteItemComponent, QuoteRequestService, QuoteShareLink, QuoteItemService |
| **jobs** | JobRequest, JobClaim, JobNotification |
| **production** | Customer, ProductionProduct, ProductionMaterial, Process, Operator, PricingMethod, WastageStage, PriceCard, ProductionOrder, JobProcess |

---

## Indexes (existing + recommended)

| Model | Index | Status |
|-------|-------|--------|
| Shop | (latitude, longitude) | ✅ shops_geo_idx |
| Shop | slug | ✅ unique |
| Location | slug | ✅ locations_slug_idx |
| Location | is_active | ✅ locations_active_idx |
| Product | (shop, slug) | ✅ unique |
| ProductCategory | (shop, slug) | ✅ unique |
| QuoteRequest | (shop, status) | ✅ quotes_shop_status_idx |
| QuoteRequest | (shop, -created_at) | ✅ quotes_shop_created_idx |
| QuoteShareLink | token | ✅ db_index |

---

## Migration Plan

### Phase 1: Indexes (low risk) ✅ Done

1. ~~Add index on QuoteRequest (shop, status)~~ → `quotes_shop_status_idx`
2. ~~Add composite index (shop, -created_at) on QuoteRequest~~ → `quotes_shop_created_idx`

### Phase 2: Optional enhancements

1. **ShopBranch** — only if multi-branch support is required. Defer.
2. **QuoteRequest → ProductionOrder link** — add `quote_request FK` to production.ProductionOrder when conversion flow is built.
3. **JobRequest.location** — consider FK to Location for SEO. Currently CharField.

### Phase 3: No changes

- ProductCategory: keep shop=null for global.
- Product: keep as single offering per shop. No ShopProductOffering.
- Location: keep hierarchical. No ProductLocation table.

---

## Why Each Core Model Exists

| Model | Purpose |
|-------|---------|
| **Shop** | Single source of truth for a print business. All shop-scoped resources (products, papers, machines, quotes) belong to a shop. |
| **Location** | Canonical geographic entity for SEO. Enables location pages (e.g. "Print shops in Westlands") and product+location pages (e.g. "Business cards in Nairobi"). |
| **ProductCategory** | Organizes products. Global (shop=null) for marketplace browse; shop-specific for custom categories. |
| **Product** | What a shop sells. Shop-scoped. Defines dimensions, pricing mode, constraints. Used in quote items and SEO. |
| **Machine** | Shop's printing equipment. Required for PrintingRate and quote pricing. |
| **Paper** | Shop's sheet stock. Required for SHEET-mode pricing. |
| **Material** | Shop's large-format material. Required for LARGE_FORMAT pricing. |
| **PrintingRate** | Per-sheet print cost. Machine + sheet_size + color_mode. |
| **FinishingRate** | Per-piece/side/sqm finishing cost. Shop-scoped. |
| **QuoteRequest** | Buyer's quote. One per shop per request. Tracks status, customer, total. |
| **QuoteItem** | Line item in quote. Stores direct FKs to chosen Paper/Material/Machine/FinishingRate. No attribute lookup. |
| **ProductionOrder** | Production order. Shop-scoped. For fulfillment tracking. Formerly Job. |
| **JobRequest** | Overflow job sharing. Marketplace. Not shop-scoped. |
| **OpeningHours** | Per-weekday hours. Shop-scoped. |
| **FavoriteShop** | Buyer's favorite. One per (user, shop). |
| **ShopRating** | Buyer's rating. One per (user, shop). |

---

## Notes

- **QuoteRequest = Quote:** Same entity. API may use "quote" in URLs; model is QuoteRequest.
- **ProductionOrder vs JobRequest:** Production.ProductionOrder = order fulfillment. Jobs.JobRequest = overflow marketplace. Different domains.
- **QuoteShareLink.quote_request:** FK to QuoteRequest (renamed from quote for clarity).
- **Slugs:** Shop, Location, Product, ProductCategory use slugs. AutoSlugMixin in common.
- **Shop-scoping:** All pricing, inventory, catalog models are shop-scoped via FK. No cross-shop leakage.
