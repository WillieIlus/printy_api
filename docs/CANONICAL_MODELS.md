# Printy Canonical Models

This is the locked target model layer for the Printy backend reset.
Any model not on this list is either deleted, postponed, or marked
DEPRECATED for removal in a later batch.

## Canonical apps and models

accounts:      User, UserProfile
shops:         Shop
catalog:       ProductCategory, Product, ProductImage, ProductFinishingOption
inventory:     Machine, Paper
pricing:       PrintingRate, FinishingRate,
               ShopPricingSettings, VolumeDiscount, PlatformFeePolicy
quotes:        CalculatorDraft, QuoteRequest, ProductionOption, Quote,
               QuoteItem, QuoteShareLink, QuoteFinancialSplit
jobs:          ManagedJob, JobAssignment, JobFile, JobStatusEvent,
               ProductionOrder
payments:      Payment, MpesaSTKRequest
notifications: Notification

(Audit tier3: Machine and Paper live in the `inventory` app, not `pricing`.
`PaymentTransaction` was never built; see "Postponed".)

## Batch 4 fee policy and quote financial foundation

- pricing.PlatformFeePolicy exists and owns active platform fee rates and markup caps.
- quotes.ProductionOption exists for manager/broker/admin sourced production options before a client quote is sent.
- quotes.QuoteFinancialSplit exists as the immutable financial snapshot attached to a Quote.
- Quote.production_option exists as a nullable backward-compatible link to the selected ProductionOption.
- payments.PaymentTransaction remains optional and postponed unless raw gateway logs require it.

## Fee formula (live code, audit tier3)

The Batch 4 formula that used to be here (`printer_fee_rate`,
`broker_margin_fee_rate`, `add_platform_fee_on_top`) is **not** what the
code does. Those fields are kept on `PlatformFeePolicy` but are deprecated
and never read by pricing. The live split is in
`pricing/services/platform_fee_policy.py` (`calculate_quote_financials`,
`_split_amounts`, `minimum_manager_markup`). Every rate, threshold and cap
below is read from the **active `PlatformFeePolicy` row**. The numbers shown
are the model defaults.

Inputs: the **shop-paid production cost** P (what the printer charges for the
job) and the manager's markup M. Both are rounded to whole KES once
(ROUND_HALF_UP), because M-Pesa STK only accepts whole shillings.

1. **Tier** from P (`under_production_threshold` = 1000,
   `high_production_threshold` = 10000):

   | Tier | Production cost P | Shop floor multiple | Manager commission cap | Max client price |
   | --- | --- | --- | --- | --- |
   | `tier_a` | P < 1000 | 1.00 | 0.60 of M | 3.00 x P |
   | `tier_b` | 1000 <= P <= 10000 | 1.05 | 0.45 of M | 2.50 x P |
   | `tier_c` | P > 10000 | 1.08 | 0.35 of M | 2.00 x P |

   Fields: `tier_{a,b,c}_shop_floor_multiple`,
   `tier_{a,b,c}_manager_commission_cap_rate`,
   `tier_{a,b,c}_max_client_price_multiple`.

2. **Client total** = P + M. The client pays exactly this; the Printy fee is
   never added on top.
3. **Shop payout** = P x tier shop floor multiple (1.00 / 1.05 / 1.08). The
   printer's uplift above P comes out of the markup.
4. **Manager payout** = min(M, M x tier commission cap).
5. **Printy fee** = client total - shop payout - manager payout. It is the
   residual, so the three parts always add up to the client total exactly.

Worked examples with the default policy. These numbers are recomputed by
`tests/test_audit_documentation.py`:

| P | M | Tier | Fee waived | Client total | Shop payout | Manager payout | Printy fee |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 800 | 400 | tier_a | no | 1200 | 800 | 240 | 160 |
| 5000 | 2000 | tier_b | no | 7000 | 5250 | 900 | 850 |
| 5000 | 2000 | tier_b | yes (own client) | 7000 | 5250 | 1750 | 0 |
| 20000 | 6000 | tier_c | no | 26000 | 21600 | 2100 | 2300 |

Minimum markup M for P = 800 / 5000 / 20000: 40 / 455 / 2462 with the fee
charged, and 40 / 250 / 1600 with the fee waived.

**Minimum markup.** A markup below the floor is rejected with
`MarginFloorError` (HTTP 400). It is never clamped. The floor is the larger of:

- `min_markup_multiple` (1.05): P + M >= P x 1.05, i.e. M >= 5% of P,
  rounded up to whole KES;
- the tier margin floor: M x (1 - commission cap) >= P x (shop floor - 1),
  so the Printy fee cannot go negative. With the fee waived:
  M >= P x (shop floor - 1).

Then it steps up by KES 1 until whole-KES rounding leaves a non-negative fee.

**Maximum markup.** `calculate_quote_financials` rejects P + M above the tier
maximum client price (3.00 / 2.50 / 2.00 x P). Partner/manager quote paths
apply a tighter guardrail first (`quotes.guardrails.validate_partner_markup_amount`):
M <= P x (`maximum_manager_markup_multiple` - 1). The default 2.00 gives
client price <= 2.00 x P **for every tier**, so the tier A/B maximums
(3.00 / 2.50) cannot be reached through those paths today. This is an open
product decision; see results.txt, Prompt 6 Section C.

**Own-client rule (fee waiver).** `pricing/services/fee_waiver.py`: the
manager-side Printy fee is waived only when the job's manager is the client's
**origin manager**:
- `UserProfile.origin_manager` is stamped once, when that manager created
  the client, and is immutable afterwards;
- a still-unclaimed `ClientInvite` from the same manager also counts.

A marketplace client (no origin manager) never matches. Mechanics:
- The decision is made once, when the split is first created, and frozen on
  `QuoteFinancialSplit.fee_waived`.
- `ManagedJob.fee_waived` copies it at acceptance. Later reads never
  re-derive it.
- With the waiver, the client total, the shop payout and the printer-side
  uplift are unchanged. Manager payout = client total - shop payout, so the
  Printy fee is 0.

**The Printy fee is never negative.** The minimum-markup floor guarantees it.
`calculate_quote_financials` also refuses any split where the fee would be
< 0 or the payouts would exceed the client total.
`python manage.py report_financial_split_anomalies` lists stored
`QuoteFinancialSplit` rows that break these rules. It only reports; it does
not modify stored rows.

**Snapshot.** The result is stored on `QuoteFinancialSplit` (one per quote).
Once the quote is accepted (or the split is locked) the stored figures are
immutable; recomputing to different numbers raises.

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

Audit tier3: these names have **no model class** in the code any more:
- BaseSize, ProductionPaperSize, FinalPaperSize
- FinishingCategory, Material, ServiceRate, ServiceRateTier
- CalculatorDraftFile
- JobPayment, JobSettlementSplit

They stay listed so nobody reintroduces them.

`JobPayment` and `JobSettlementSplit` remain **DEPRECATED**:
- No code creates them; the canonical payment is `payments.Payment` and the
  canonical split is `QuoteFinancialSplit`.
- Their names survive only in compatibility projections such as
  `jobs.serializers.JobSettlementSplitSerializer`.

## Deprecated fields retained for compatibility

- accounts.UserProfile.default_markup_rate: retained only for dashboard/profile compatibility. Active fee defaults come from PlatformFeePolicy.
- pricing.ShopPricingSettings broker/service margin fields and locked flags: retained only for legacy preview UI. Authoritative quote economics use QuoteFinancialSplit.
- catalog.Product shop/default_machine/lowest_price/highest_price: retained for catalog setup/display compatibility. Product is not a pricing formula source.
- shops.Shop pricing_ready/public_match_ready/supports_* and pricing_ranges/mvp_rate_card: retained for setup, public matching, and rate-card compatibility until routing cleanup.
- quotes.QuoteRequest.shop: retained as an internal legacy routing field. Public/client flow cleanup is deferred.
- quotes.Quote production_base_price, broker_margin_*, platform_service_*: retained as legacy cache fields. New authoritative financial data must use Quote.financial_split.
- jobs.JobPayment and jobs.JobSettlementSplit: DEPRECATED. The model classes are already gone. Canonical payments use payments.Payment, and settlement views project QuoteFinancialSplit.

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
- Lost callbacks (audit #1c): `payments.recovery` / `mpesa_recover_payments` ask Daraja STK Query and replay the answer through the same callback state machine. A bare query success becomes `needs_review`, never `paid`. A prompt still unresolved after `MPESA_STK_IN_FLIGHT_SECONDS` becomes `MpesaSTKRequest(expired)` and `Payment(expired)`; expired is not final (a late success still applies, and a retry reuses the same `Payment` row).
- `ManagedJob` may copy display totals from `QuoteFinancialSplit` for workflow dashboards, but `QuoteFinancialSplit` remains the source of truth.
- Manager/broker/admin dispatch is separate from payment confirmation. Payment confirmation must not create `JobAssignment`.
- New canonical flows must not create `JobPayment` or `JobSettlementSplit` records.
- Delivery handover (Prompt 7): `ManagedJob(ready)` -> mark dispatched **with a handover code** -> `ManagedJob(delivered)` -> buyer enters the matching code ("I received it") -> `ManagedJob(completed)`. The code and its trail live on `ManagedJob` (`handover_code`, never serialized; `handover_failed_attempts`; `handover_confirmed_at/by`; `completion_method` = `buyer_handover` | `buyer_no_code` (legacy job dispatched before codes) | `admin_dispute`). No other path completes a job: `JobAssignment` completion follows the job and cannot complete it.

## Postponed (not MVP, kept but not transitional)

quotes: QuoteRequestMessage
payments: PaymentTransaction (not built; there is no model class)

## Additional models present in the code (audit tier3)

These models exist and are in use but were never added to the lists above.
They are recorded here so that this file describes the real model layer.
This listing does not approve them as canonical; that is a product decision.

accounts:       ManagerWorkPhoto, ManagerLock, ClientInvite
clients:        ClientLink, ClientLinkUse, ClientOtp, ClientContactPreference,
                MessageDelivery
contact:        ContactMessage
jobs:           ManagedJobPayout, JobEvent, ViewerItemReadState
mpesa_payments: MpesaPayment, MpesaAccessToken, MpesaCallbackLog
                (the cashier / managed-job M-Pesa flow, which runs alongside
                payments.Payment)
payments:       PaymentPhoneConsent
pricing:        ShopRateCardSetup, WastePolicy, SetupCostPolicy,
                QuantityPricingTier, SpeedTier, TimeWindowRule, PublicHoliday,
                ShopSpeedTierOptIn, ShopTimeWindowOptIn, EstimatePolicy
quotes:         EmailOutbox, PendingArtworkUpload, QuoteOverride, QuoteVersion,
                PriceTransaction, QuoteDateRevision
workflow:       WorkflowManager, WorkflowPrinter, WorkflowJob

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

Each rule below is true of the current code and is checked by a test.
Changing the code so that a rule stops holding makes that test fail.

- **Every model is listed in this file.** It must appear under canonical,
  transitional, postponed or "Additional models". A new model has to be added
  here in the same change (`tests/test_audit_documentation.py`).
- **No code creates `JobPayment` or `JobSettlementSplit`;** neither model
  class exists (`tests/test_audit_documentation.py`).
- **Signed-out visitors and clients never see a shop:** no shop name, slug,
  id, location, contact, cost or rate card. They find managers. Printer-facing
  marketplace endpoints are for verified, active managers and super_admin; a
  printer owner sees only their own shop. Client quote-draft shop routing
  returns 400 (`tests/test_audit_role_visibility_matrix.py`).
- **Printers never see client identity or the client total. Clients never
  see production cost** (`tests/test_audit_role_visibility_matrix.py`).
- **The live split reads every rate, threshold and cap from the active
  `PlatformFeePolicy` row.** Admin edits take effect. The only literals are
  the model field defaults. The deprecated / legacy policy fields do not
  affect the split (`pricing/test_audit_fee_floor.py`,
  `pricing/test_audit_fee_policy_cleanup.py`).
  - UI hints that are not limits: the partner default markup suggestion
    0.75 (`quotes.guardrails.get_partner_markup_default_rate`) and the
    `PARTNER_MARKUP_WARNING` setting (default 1.00).
- **The Printy fee is never negative, and shop payout + manager payout +
  Printy fee = client total exactly** (`pricing/test_audit_fee_floor.py`).

Process rule (enforced by review, not by a test): no model on the DEPRECATED
list may gain new fields, FKs or features.
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
