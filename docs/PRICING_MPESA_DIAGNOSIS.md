# Diagnosis — Waste billing & adjusted-quote M-Pesa amount (Step 1)

Scope: `printy_api` (Django/DRF, deployed to `/home/wilson/apps/printy_api`) and
`printy_ui` (Nuxt 3). This document is **diagnosis only** — no application code
was changed. Produced for Step 1 of the pricing/M-Pesa integrity fix.

Repo state examined: branch `main`, HEAD `eab699d`. The spoilage-cap work already
in history (`5b4af7e`, `85b5bc3`, both 2026-10-10) is treated as the "existing
intended cap" the brief refers to.

---

## BUG 1 — Excessive waste billing (2 sheets billed as 5)

### Canonical sheet-count service (single source of truth)

`pricing/services/production_cost_calculator.py::calculate_billable_sheets`
(lines 72–108) is the one place sheet counts are derived:

```
raw_sheets                  = ceil(quantity / yield_per_sheet)          # 84
variable_waste_sheets       = ceil(raw_sheets * variable_waste_rate)    # 85
total_sheets_needed         = raw + fixed_waste + variable              # 86  -> physical (5)
production_billable_sheets  = max(total_sheets_needed, minimum_billable_sheets)  # 87
maximum_spoilage_sheets     = ceil(raw_sheets * maximum_spoilage_rate)  # 90  = 1
max_billable_sheets         = raw_sheets + maximum_spoilage_sheets      # 91  -> 3
billable_sheets             = min(production_billable_sheets, max_billable_sheets)  # 92 -> 3
```

For the reported case (raw = 2, `fixed_waste_sheets=2`, `variable_waste_rate=0.10`,
`maximum_spoilage_rate=0.50`): physical `total_sheets_needed = 5`, **billable = 3**.
`WastePolicy.maximum_spoilage_rate` (default `0.5000`) is added by
`pricing/migrations/0002_wastepolicy_maximum_spoilage_rate.py` and declared in
`pricing/models.py` (≈176–184).

### Where the cap is correctly applied (verified)

- `services/pricing/engine.py::_waste_policy_split` (116–138) delegates to
  `calculate_billable_sheets`; `calculate_sheet_pricing` reads it at line 325 and
  charges `paper_cost`/`print_cost` on `billable_sheets` (≈470–471). `production_sheets`
  (331) is emitted for display only and is **not** multiplied into any cost.
- `quotes/pricing_service.py::_compute_sheet_pricing` calls the engine (402) and
  takes `billable_sheets` from the engine result (425–435).
- `pricing/services/production_cost_calculator.py::calculate_production_cost`
  (161–192) and `calculate_client_price_with_waste_setup_and_quantity_tier`
  (210–225) multiply paper/click by `billable_sheets`.
- Tests already pin the capped behavior:
  `pricing/test_wastage_pricing.py` (cap formula, `total_sheets_needed == 5`,
  `billable_sheets == 3`), `quotes/test_quote_sheet_count_display.py`
  (a 2-good-sheet card job bills 3), `api/test_calculator_spec_harmonization.py`.

### Findings — residual defects that still surface "5" or bypass the cap

The canonical *priced* path is capped, so "billed for 5" is **not reproducible
through it**. The following concrete gaps remain (each is a real defect):

1. **Uncapped physical count is still exposed and labelled as "sheets".**
   Payloads emit `production_sheets` / `total_sheets_needed` (= 5) next to
   `billable_sheets` (= 3):
   - `client_calculator/services.py:94–108` (`production_sheets`, `billable_sheets`)
   - `services/public_matching.py:327` and `client_calculator/services.py:104`
   - `services/pricing/calculator_preview.py:729–730`
   - `api/workflow_views.py:728–734` (`sheets_required`/`good_sheets`/`production_sheets`)
   - `api/visibility.py:288`
   Any consumer that renders `production_sheets`/`total_sheets_needed` as the
   billed sheet count reproduces the exact report. Root-cause class:
   **cap not reflected in the (displayed) sheet count**.

2. **A second pricing path bypasses the cap.** In
   `quotes/pricing_service.py::_compute_sheet_pricing`, when `machine_id`/`sides`/
   `color_mode` are not all set the engine is skipped and paper is priced on
   `result.sheets_needed` (good sheets) at line **437** — the central
   `calculate_billable_sheets` cap is never consulted on this branch. (This branch
   under-bills rather than over-bills, but it is a genuine "another path bypasses
   the cap" hole.)

3. **Basis mismatch between finishing and paper/print.** `local_finishing_total`
   is computed on `result.sheets_needed` (good = 2) at `quotes/pricing_service.py:392–398`
   and used at line 439, while paper/print use `billable_sheets` (3). Same class as
   the original display bug; the finishing basis is not the capped count.

4. **Stale verification harness re-encodes the pre-cap formula.**
   `rerun_jobab_verification.py::independent_imposition` line **250**
   (`billable = max(good + fixed + variable, min_billable)`) has no spoilage cap,
   so `imposition_verdict` (266–304) will report a false mismatch against the
   (correct) capped engine.

5. **Legacy/duplicate calculators with no cap.** `printy_api_old_0.3-main/...`
   (a repo copy) has no `maximum_spoilage_rate`; `quotes/quote_engine.py:38`
   (`sheets = quantity`) is a naive legacy path. Confirm neither is reachable in
   production before dismissing.

6. **Duplex does not double sheet consumption** (brief item 5). Duplex only
   selects the double-click `PrintingRate` in the engine; physical sheet counts
   come from imposition, not from `sides`. No defect found here.

**Deployment caveat:** the cap depends on the *active* `WastePolicy` row having a
`maximum_spoilage_rate`. A legacy row created before migration `0002` (or a
NULL/zero rate) changes the result; this must be verified on the live DB, which
could not be reached from this environment (see Verification notes).

### Root-cause conclusion (BUG 1)

For the current codebase: the cap exists and the money path is correct, but the
cap is **not reflected on every surface** (finding 1), is **bypassed on the
fallback pricing branch** (finding 2), and the **finishing basis disagrees** with
paper/print (finding 3). If the live deployment still bills 5, the most likely
additional cause is a missing/legacy `WastePolicy` row on the server (deployment
caveat) or an older deploy predating `5b4af7e`. The fix must make the capped
`billable_sheets` the single number used for pricing **and** for every
sheet-count surface, and remove the uncapped fallback.

### Regression tests required (BUG 1)

- 2 sheets required → `billable_sheets == 3`; assert derived paper/print/total.
- Small quantities and small sheet counts; larger quantities.
- Digital printing; duplex; multiple-up imposition.
- Setup waste vs production spoilage counted separately (no double count).
- `min(raw + ceil(raw * maximum_spoilage_rate), production_billable_sheets)` cap.
- Paper and printing costs computed on the capped count.
- Quote description and pricing **snapshot** both show the capped count.
- Every supported pricing path that can produce a quote (engine, fallback branch,
  client calculator, partner prepare, dashboard send-to-client) uses the cap.

---

## BUG 2 — M-Pesa STK pushes the original estimate after a manager adjusts the quote

### Authoritative money chain (verified)

| Step | Location | Behaviour |
|---|---|---|
| Accept | `quotes/acceptance.py:28–85` | `ensure_quote_financial_split(quote=quote)` (41) → `create_payment_for_quote(...)` (84) |
| Build payment | `payments/services.py:102–149` | `amount = _money(split.client_total)` (104–105); `Payment.objects.create(amount=amount, expected_amount=amount, ...)` (120–131) |
| STK view | `api/payment_views.py` (`PaymentInitiateSTKView`) | calls `accept_quote_for_payment` then `initiate_stk_push` |
| STK service | `payments/services.py:470–577` | `amount = _money(payment.amount)` (473); `MpesaSTKRequest.amount` (490–496); Daraja payload `"Amount"` (≈425) |
| Callback | `payments/services.py:244–247` | reconciles against `payment.expected_amount or payment.amount` |

So the single value that decides both the STK charge and the reconciliation is
`Payment.expected_amount`, copied once from `split.client_total`.

### Root cause — the financial split is frozen

`pricing/services/platform_fee_policy.py::ensure_quote_financial_split`
(lines 336–357) returns any **existing** split untouched (it only flips `locked`
on acceptance):

```python
existing = getattr(quote, "financial_split", None)
if existing:
    if quote.status == "accepted" and not existing.locked:
        existing.locked = True; existing.save(update_fields=["locked"])
    return existing                     # never reconciled against quote.total
...
broker_client_price = getattr(quote, "total", None)   # only used when NO split exists
```

`create_quote_financial_split` *can* update an unlocked split (279–309) but is
only called when the split does not exist (or explicitly), and it raises
`"Accepted quote financial snapshots are immutable."` once locked (280–283).

Every manager price-adjustment path writes `Quote.total` / the price snapshot
**without touching the split**:

| Write path | File / line | Writes | Split updated? |
|---|---|---|---|
| Shop revise `PATCH /sent-quotes/{id}/` | `api/quote_serializers.py::QuoteUpdateSerializer.update` (916–956); view `api/quote_views.py:701–747` | `total` (super) + `response_snapshot["price"]` (931–932) | **No** |
| Shop send-quote | `api/quote_views.py` (`send_quote`, ≈566–620) | `quote.total` | **No** |
| Workflow revise `PATCH /workflow/quote-responses/{pk}/` | `api/workflow_views.py:1751–1776` → `quotes/services_workflow.py::update_quote_response` (1014–1049) | `response.total = total` (1033) | **No** |
| Partner prepare | `quotes/partner_services.py::_persist_quote_split` (214–222) → `respond_to_assigned_quote_request` (418–501) | `total` + returns existing split untouched (215–216) | **No** |
| Partner send-to-client | `api/dashboard_views.py` (`PartnerQuoteSendToClientView`, 1072–1315) | `create_quote_financial_split(...)` (1250) and `total` only when status `PENDING` (1224–1231); `else` branch does not write `total` | partial / diverges |

Because the client read surfaces are inconsistent — the client response **list**
uses raw `total` (`api/workflow_serializers.py::ClientResponseListItemSerializer.price`,
`source="total"`, ≈1455), while the **detail** prefers the split
(`QuoteResponseReadSerializer.get_total`, ≈1269–1280) — the client is shown the
adjusted number but the STK is built from the stale split. After acceptance the
split is locked, so the divergence can never be corrected and
`mark_payment_paid` will not flag it (its guard compares received vs the equally
stale `expected_amount`).

### Secondary divergence

`api/dashboard_views.py:1123–1131` recomputes the client price from canonical
quantity-tier pricing while the split is written at 1250; in the `else` branch
`total` is not written, so the displayed list price and the charged split can
diverge in the opposite direction.

### Required behaviour (BUG 2)

The authoritative payment amount must come from the persisted, currently accepted
quote. Concretely: reconcile the split to the current accepted `quote.total`
before it is locked; route every price-writing path through the pricing service;
make the client-visible total, the split's `client_total`, `Payment.amount`,
`Payment.expected_amount`, and the STK payload all resolve to one value.

### Regression tests required (BUG 2)

- Estimate 4,000 adjusted to 3,500 → STK push for **3,500**.
- Estimate 4,000 adjusted to 4,500 → STK push for **4,500**.
- `Payment.expected_amount` stores the adjusted amount.
- Original estimate never used once a different accepted `quote.total` is authoritative.
- Client cannot manipulate the amount by submitting a different total.
- Duplicate initiations handled safely (no duplicate payments).
- Duplicate callbacks cannot double-apply.
- Incorrect callback amount does not mark the payment paid.
- Failed/expired attempts retry against the latest accepted quote without duplicates.
- Accepted quote / payment / job remain financially consistent.
- Preserve `tests/test_phase_a_formula_contracts.py::test_accepted_financial_snapshot_is_immutable`.

---

## Verification notes / limitations

- Code paths above were read directly (line numbers current as of HEAD `eab699d`).
- Django tests could **not** be executed in this environment: the test DB
  connection failed (`FATAL: password authentication failed for user "printy_user"`
  at `localhost:5432`). Targeted suites `pricing.test_wastage_pricing`,
  `quotes.test_quote_sheet_count_display`, `payments.tests`,
  `tests.test_phase_a_formula_contracts` must be run where the DB is reachable.
- The live `WastePolicy.maximum_spoilage_rate` and the deployed commit
  (server at `~/apps/printy_api`) were not verifiable from here (no SSH key).

## Recommended next step (Step 2)

Implement the BUG 1 correction: make the capped `billable_sheets` the single
figure for pricing **and** all sheet-count surfaces, remove the uncapped fallback
branch, align the finishing basis, refresh the stale verification harness, and add
the regression tests above. Run the targeted suites, inspect the diff, commit, and
stop.
