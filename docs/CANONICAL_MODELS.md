# Printy Canonical Models

This is the locked target model layer for the Printy backend reset.
Any model not on this list is either deleted, postponed, or marked
DEPRECATED for removal in a later batch.

## Canonical apps and models

accounts:      User, UserProfile
shops:         Shop
catalog:       ProductCategory, Product, ProductImage, ProductFinishingOption
pricing:       Machine, Paper, PrintingRate, FinishingRate,
               ShopPricingSettings, VolumeDiscount, PlatformFeePolicy
quotes:        CalculatorDraft, QuoteRequest, ProductionOption, Quote,
               QuoteItem, QuoteShareLink, QuoteFinancialSplit
jobs:          ManagedJob, JobAssignment, JobFile, JobStatusEvent,
               ProductionOrder
payments:      Payment, MpesaSTKRequest, PaymentTransaction (optional)
notifications: Notification

## Batch 4 fee policy and quote financial foundation

- pricing.PlatformFeePolicy exists and owns active platform fee rates and markup caps.
- quotes.ProductionOption exists for manager/broker/admin sourced production options before a client quote is sent.
- quotes.QuoteFinancialSplit exists as the immutable financial snapshot attached to a Quote.
- Quote.production_option exists as a nullable backward-compatible link to the selected ProductionOption.
- payments.PaymentTransaction remains optional and postponed unless raw gateway logs require it.

Fee formula:

- gross_margin = broker_client_price - production_cost
- printer_side_fee = production_cost * printer_fee_rate
- broker_margin_fee = gross_margin * broker_margin_fee_rate
- printy_fee = printer_side_fee + broker_margin_fee
- if add_platform_fee_on_top is false, client_total = broker_client_price
- if add_platform_fee_on_top is false, broker_payout = gross_margin - printy_fee
- if add_platform_fee_on_top is true, client_total = broker_client_price + printy_fee
- if add_platform_fee_on_top is true, broker_payout = gross_margin

## Remaining batch scope

- Batch 5: pricing cleanup and canonical payment readiness. PlatformFeePolicy is the authoritative fee policy, QuoteFinancialSplit is the authoritative financial split, and Payment is not a split-math model.
- Batch 6: deprecated model/field removal where safe, including JobSettlementSplit surfaces and profile/shop margin compatibility fields.
- Batch 7: routing and calculator context cleanup.
- Batch 8: actor-aware serializer visibility cleanup.

## Transitional models (DEPRECATED)

inventory: BaseSize, ProductionPaperSize, FinalPaperSize
pricing:   FinishingCategory, Material, ServiceRate, ServiceRateTier
quotes:    CalculatorDraftFile, QuoteItemFinishing, QuoteItemComponent,
           QuoteRequestService, QuoteRequestAttachment,
           QuoteAttachment, QuoteItemAttachment, QuoteItemService
jobs:      JobPayment, JobSettlementSplit

## Deprecated fields retained for compatibility

- accounts.UserProfile.default_markup_rate: retained only for dashboard/profile compatibility. Active fee defaults come from PlatformFeePolicy.
- pricing.ShopPricingSettings broker/service margin fields and locked flags: retained only for legacy preview UI. Authoritative quote economics use QuoteFinancialSplit.
- catalog.Product shop/default_machine/lowest_price/highest_price: retained for catalog setup/display compatibility. Product is not a pricing formula source.
- shops.Shop pricing_ready/public_match_ready/supports_* and pricing_ranges/mvp_rate_card: retained for setup, public matching, and rate-card compatibility until routing cleanup.
- quotes.QuoteRequest.shop: retained as an internal legacy routing field. Public/client flow cleanup is deferred.
- quotes.Quote production_base_price, broker_margin_*, platform_service_*: retained as legacy cache fields. New authoritative financial data must use Quote.financial_split.
- jobs.JobPayment and jobs.JobSettlementSplit: retained for compatibility only. Canonical payments use payments.Payment and future settlement views should project QuoteFinancialSplit.

## Batch 5 cleanup notes

- Product and Shop are not pricing formula dumping grounds.
- Payment must not contain broker/shop margin, pricing formula, or split fields.
- Human/browser testing and real migrations remain deferred until cleanup batches are complete.

## Batch 7 canonical payment and managed job flow

- Quote acceptance now creates a canonical `payments.Payment` from `Quote.financial_split.client_total`.
- `Payment` stores payment state only: payer, amount, expected amount, provider identifiers, receipt, and confirmation timestamps.
- `Payment` does not calculate or own production cost, Printy fee, broker payout, shop payout, or gross margin. Those remain on `QuoteFinancialSplit`.
- M-Pesa STK lifecycle is tracked through `payments.MpesaSTKRequest`.
- Canonical flow:
  `Quote(sent/revised)` -> accept -> `Payment(pending)` -> STK push -> `MpesaSTKRequest(sent)` -> callback -> `Payment(paid)` -> `ManagedJob(payment_confirmed)` -> dispatch -> `JobAssignment`.
- Successful callback reconciliation is idempotent and creates or unlocks a single `ManagedJob` for the paid quote.
- `ManagedJob` may copy display totals from `QuoteFinancialSplit` for workflow dashboards, but `QuoteFinancialSplit` remains the source of truth.
- Manager/broker/admin dispatch is separate from payment confirmation. Payment confirmation must not create `JobAssignment`.
- New canonical flows must not create `JobPayment` or `JobSettlementSplit` records.

## Postponed (not MVP, kept but not transitional)

quotes: QuoteRequestMessage
payments: PaymentTransaction

## Calculator routing rules

- Homepage/client calculators must not route directly to shops.
- Manager/broker calculators may source shops.
- Shop calculators are internal estimate/respond only.
- Pricing must remain backend-owned.

## Serializer visibility rules

- Public/client serializers must not expose shop payout, broker payout,
  Printy fee, production cost, shop identity in brokered flow, formulas,
  or competing shop rates.
- Shop serializers must not expose client total, broker margin, broker
  payout, or competing options.

## Hard rules

- No new model may be added unless it is on this list.
- No model on the DEPRECATED list may gain new fields, FKs, or features.
- No public/client endpoint may create or reference a shop directly.
- No hardcoded fee or markup percentages (PlatformFeePolicy owns those).
## Batch 6 Actor Visibility

Actor projections are selected from the authenticated user role at the view or serializer boundary. Do not trust request payload fields such as `actor` for financial visibility decisions.

Supported actor roles:

- `client`: sees quote totals, public request snapshots, payment status, and job tracking only.
- `broker` / `manager`: may see production options, shop identity, `QuoteFinancialSplit`, client total, shop payout, broker payout, gross margin, and Printy fee.
- `shop`: sees assigned specs, files, deadlines, production notes, and shop payout only.
- `admin`: has full operational visibility.

Client/public payloads must not expose `production_cost`, `production_base_price`, `shop_payout`, `broker_payout`, `broker_margin_*`, `gross_margin`, `printy_fee`, `platform_service_*`, `selected_shop*`, raw pricing snapshots, internal formulas, competing shop rates, or production shop identity in brokered flows.

Shop payloads must not expose `client_total`, `broker_payout`, `broker_margin_*`, `gross_margin`, `printy_fee`, competing production options, client identity, or private client payment details.

`QuoteFinancialSplit` remains the authoritative quote economics source, but it must always be actor-projected. Client serializers should expose only `Quote.client_total`; shop serializers expose only `shop_payout`; broker/manager/admin serializers can expose the full split.

`JobSettlementSplit` remains deprecated compatibility data. It is not a public serializer source and must be represented only through Batch 6 allowlisted compatibility projections until Batch 7 removes or replaces the legacy settlement surface.
