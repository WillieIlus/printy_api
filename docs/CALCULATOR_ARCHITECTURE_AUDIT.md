# Calculator And Quote Architecture Audit

Date: 2026-04-03

## Scope inspected

Backend:
- `quotes/models.py`
- `quotes/services.py`
- `quotes/services_workflow.py`
- `quotes/pricing_service.py`
- `quotes/quote_engine.py`
- `pricing/models.py`
- `catalog/models.py`
- `shops/models.py`
- `api/views.py`
- `api/quote_views.py`
- `api/workflow_views.py`
- `api/workflow_serializers.py`
- `services/pricing/engine.py`
- `services/pricing/imposition.py`
- `services/pricing/finishings.py`
- `services/pricing/quote_builder.py`
- `services/public_matching.py`
- `services/engine/services/quote_calculator.py`
- `services/engine/services/booklet_imposer.py`
- `services/engine/services/flat_sheet_imposer.py`
- `services/engine/services/roll_layout_imposer.py`

Frontend:
- `app/components/quotes/PublicCalculator.vue`
- `app/components/quotes/BackendQuoteCalculator.vue`
- `app/services/public.ts`
- `app/services/quoteDraft.ts`
- `app/stores/calculator.ts`
- `app/stores/quoteInbox.ts`
- `app/stores/quoteDraft.ts`
- `app/stores/localQuotes.ts`
- `app/composables/useQuoteBuilder.ts`
- `app/composables/useQuoteRequestBlast.ts`
- `app/pages/index.vue`
- `app/pages/shops/[slug]/index.vue`
- `app/pages/quote-draft.vue`
- `app/shared/api-paths.ts`

## Current architecture

### Current backend sources of truth

1. Physical layout and imposition logic is most mature in `services/engine/services/quote_calculator.py` and its imposers.
2. Shop pricing resolution is centralized in `pricing/models.py` for printing rates, duplex surcharge rules, and finishing billing rules.
3. The newer calculator preview path is `api/workflow_views.py -> services/pricing/quote_builder.py -> services/pricing/engine.py`.
4. Public marketplace and single-shop previews use `services/public_matching.py`, which also delegates to `services/pricing/engine.py`.
5. Quote draft save/send workflow is centralized in `quotes/services_workflow.py`.
6. Request/response threading is modeled in `QuoteDraft`, `QuoteRequest`, `ShopQuote`, and `QuoteRequestMessage`.

### Current duplication and drift risks

1. `quotes/services.py` contains pricing logic that overlaps with `quotes/pricing_service.py`.
2. `quotes/pricing_service.py` and `services/pricing/engine.py` both act like calculation entry points.
3. `services/pricing/imposition.py`, `catalog.Product.get_copies_per_sheet()`, and the richer engine imposers overlap conceptually.
4. The frontend still carries some rule inference in calculator components, especially around finishing selection semantics and turnaround display.
5. Two quote-draft families are active in parallel:
   - newer workflow routes: `calculator/preview`, `calculator/drafts`, `dashboard/shop-home`
   - older draft routes: `quote-drafts/...`, `tweaked-items/...`

## Where each concern is currently computed

### Billing type and charging basis

- Finishing billing basis is defined in `pricing/models.py` on `FinishingRate`.
- Duplex surcharge rule lives in `PrintingRate.resolve()` and related duplex helpers.
- Product pricing mode lives in `catalog/models.py` on `Product.pricing_mode`.
- Some UI surfaces still branch on pricing mode and sides, but backend remains the authority for the final price.

### Imposition and production layout

- Richer layout logic: `services/engine/services/quote_calculator.py`
- Current pricing-engine sheet entry point: `services/pricing/engine.py`
- Simpler sheet breakdown helper: `services/pricing/imposition.py`
- Legacy product helper: `catalog/models.py` product sizing/imposition helpers

### Finishing pricing

- Canonical finishing rate definition and side billing rules: `pricing/models.py`
- Pricing-engine finishing aggregation: `services/pricing/finishings.py`
- Older quote calculators also compute finishing totals separately, which is the main consolidation target.

### Paper, material, and machine selection

- Public matching path auto-picks paper/material/machine in `services/public_matching.py`.
- Workflow preview currently expects explicit paper/machine ids through `CalculatorPreviewSerializer`.
- Product defaults for machine, sizes, GSM ranges, and sheet sizes live in `catalog/models.py`.

### Price totals

- Newer preview totals come from `services/pricing/engine.py`.
- Public matching totals also come from `services/pricing/engine.py`.
- Older quote-request/send flows can still rely on `quotes/pricing_service.py` or `quotes/services.py`.

### Quote drafts and quote requests

- Newer draft/save/send workflow: `quotes/services_workflow.py`
- Older shop-scoped cart-like draft flow: `api/views.py` + `app/services/quoteDraft.ts`
- Client workspace/dashboard flow: `app/stores/quoteInbox.ts` + `app/pages/quote-draft.vue`

## Flat calculator reuse

The flat calculator concept is currently reused in multiple surfaces through two large frontend components:

1. `PublicCalculator.vue`
   - homepage hero
   - public shop pages
   - marketplace matching
   - tweak/custom request surfaces
2. `BackendQuoteCalculator.vue`
   - client workspace
   - shop/admin quote workspace
   - dashboard-related quote building

This reuse is good for UI consistency, but both components now contain overlapping request-shaping and field-normalization logic.

## Quote draft workflow today

### Newer workflow path

1. Frontend builds calculator payload.
2. `calculator/preview/` returns backend preview totals.
3. Draft is saved through `calculator/drafts/`.
4. Draft is sent to one or more shops through `calculator/drafts/{id}/send/`.
5. Backend creates `QuoteRequest`, `QuoteItem`, `QuoteItemFinishing`, and request messages in `quotes/services_workflow.py`.

### Older draft path

1. Frontend uses `quote-drafts/active/`.
2. Items are added/updated through `quote-drafts/{id}/items/...`.
3. Preview uses `quote-drafts/{id}/preview-price/`.
4. Submission uses `quote-drafts/{id}/request-quote/`.

Both flows are still live. New work should avoid expanding both.

## Customer and shop-owner separation

The codebase already has a partial separation:

- Customer-side request building and tracking:
  - `QuoteDraft`
  - `QuoteRequest`
  - `app/pages/quote-draft.vue`
  - `app/stores/quoteInbox.ts`
- Shop-side inbox and quoting:
  - `api/quote_views.py`
  - incoming request endpoints
  - sent quote endpoints
  - dashboard shop home endpoint

The separation is good enough to extend, but the calculator inputs and response snapshots are still not normalized into one shared contract.

## Safest extension points

### 1. Size presets and unit switching

Safest path:
- add normalization at calculator input boundaries
- preserve millimetres as backend source of truth
- resolve presets before calling `services/pricing/quote_builder.py`

Do not:
- add unit conversion logic independently in multiple Vue components

### 2. Duplex surcharge pricing

Safest path:
- keep all surcharge rule decisions in `pricing.PrintingRate`
- let preview and quote flows call the same resolver

Do not:
- add surcharge math in serializers or components

### 3. Turnaround in working hours

Safest path:
- shop schedule on `Shop` / `OpeningHours`
- turnaround estimation in one backend service
- attach read-only turnaround outputs in workflow/public serializers

Do not:
- calculate ready times in the frontend

### 4. Booklet calculator

Safest path:
- extend the existing engine path in `services/engine/services/quote_calculator.py`
- keep booklet-specific sheet math and signatures there
- feed resulting production requirements into the shared pricing engine

Do not:
- create a separate booklet-only pricing stack

### 5. Large format calculator

Safest path:
- continue using `services/pricing/engine.calculate_large_format_pricing`
- align custom/public/workflow payloads onto one normalized large-format contract

### 6. Quote-builder workflow

Safest path:
- standardize on `quotes/services_workflow.py` for save/send
- gradually retire the parallel `quote-drafts/...` mutation path

### 7. Customer workspace / inbox

Safest path:
- extend `QuoteDraft`, `QuoteRequest`, `ShopQuote`, `QuoteRequestMessage`
- keep request tracking in `quoteInboxStore`
- avoid introducing a second customer messaging model

## What should stay shared

1. Rate resolution, finishing billing rules, duplex surcharge rules, and VAT handling in backend pricing services.
2. The engine-based imposition and layout logic.
3. Quote request, response, and message models.
4. Shared frontend display components for breakdowns and preview panels.

## What should become specialized

1. Input normalization for sheet jobs, booklet jobs, and large-format jobs.
2. UI composition for public browsing vs shop workspace.
3. Quote-builder orchestration around multi-shop send, not the pricing rules themselves.

## Refactor-first recommendations

1. Pick one backend pricing entry point for customer preview and quote-send repricing.
   - Recommended target: `services/pricing/engine.py` via thin orchestration helpers.
2. Stop growing the older `quote-drafts/...` mutation flow.
3. Extract shared request-payload normalization from `PublicCalculator.vue` and `BackendQuoteCalculator.vue`.
4. Define one shared backend-facing calculator payload contract for:
   - sheet
   - large format
   - booklet
5. Move all human turnaround labels and future size-preset labels into backend-backed constants/services.

## Recommended implementation order

1. Consolidate pricing entry points without changing pricing rules.
2. Normalize calculator payload shapes across public and dashboard surfaces.
3. Introduce size presets and unit switching on top of normalized dimensions.
4. Finalize duplex surcharge flow on the single pricing path.
5. Add working-hours turnaround on the single quote preview and response path.
6. Extend the engine-backed path for booklet jobs.
7. Expand large-format workflow support where the payload contract is already unified.
8. Build the customer workspace/inbox on top of the existing request/response/message models.

## Anti-patterns to avoid expanding

1. Backend pricing logic split across multiple quote services.
2. Frontend components inferring business rules from display labels.
3. Separate request payload conventions between public, dashboard, and legacy draft flows.
4. Adding new feature support to both workflow drafts and legacy drafts at the same time.
5. Serializer-level or component-level pricing math.

## Immediate guardrail for future work

Before feature delivery work starts, new calculator features should target this path by default:

`frontend calculator surface -> normalized payload -> workflow/public backend endpoint -> services/pricing/engine.py (+ engine layout services) -> request/response snapshot`

If an upcoming change cannot use that path, it should be treated as a refactor candidate first, not as a reason to add another calculator stack.
