# Printy API

Backend for **Printy** (`printy.ke`) — an online printing marketplace for Kenya.

Printy connects four parties that would otherwise never transact:

| Party | What they do here |
| --- | --- |
| **Client** | Needs something printed. Self-serves with the instant price calculator, or posts a quote request. |
| **Partner** (a.k.a. *print manager* / broker) | Sources production from a shop, applies their own markup, and issues the quote. |
| **Shop** (print production business) | Holds the machines, paper stock and rate card, and physically prints the job. |
| **Printy ops** | Owns fee policy, disputes, settlement and payout release. |

The frontend is a separate Nuxt app in the sibling `printy_ui` repo. This repo is the API, the
authoritative business logic, and the place where money, due dates and stage state are decided.

- **Stack:** Django 5.2.11 + Django REST Framework 3.16, PostgreSQL, JWT auth, Safaricom Daraja (M-Pesa).
- **Domain:** English + Kiswahili, `Africa/Nairobi`, all timestamps stored in UTC.
- **Current focus:** speed tiers, odd-hours pricing, buyer live tracking, and the printer one-action
  stage flow.

---

## The one thing to understand first

**Printy is quote-first, job-second.**

A `ManagedJob` — the record that represents real work moving through production — **cannot exist
until a client has paid**. The ordering is deliberate:

```
demand ──▶ quote ──▶ accepted ──▶ PAID ──▶ ManagedJob ──▶ production ──▶ delivery ──▶ payout
```

Consequences that explain most of the code:

1. **Money is decided once, at quote acceptance, and then frozen.** When a client accepts, a
   `QuoteFinancialSplit` is created and never mutated. The `ManagedJob` copies the amounts *verbatim*
   from that split. Nothing downstream re-derives or recalculates money, so a shop or partner can
   never be paid an amount that differs from what the client was quoted.
2. **Price and due date lock together.** Acceptance freezes the price, the speed/time-window terms
   and the due date. A later manager change creates a new version and triggers the buyer's
   "price/date updated" acknowledgement state.
3. **Nothing is dispatched before payment clears and artwork is approved.** Production is gated on two
   independent conditions (confirmed payment, non-blocking artwork confirmation) so a printer is never
   asked to start work on an unpaid or unverified job.
4. **The server owns the workflow.** The frontend renders the next printer action, stage state, time
   remaining and tracker payload it receives. It does not guess the next stage or compute prices.
5. **Most "bug reports" in this codebase are really money-split reports.** If payouts don't sum to the
   client total, the bug is in the split, not in the payout release. See the invariant below.

### The money invariant (load-bearing — do not break)

```
shop_payout + manager_payout + printy_fee == client_total
```

Two rules enforce it, both in `common/money.py` and the quote financial-split service:

- **All client-facing money is whole KES.** M-Pesa STK Push cannot carry cents, so amounts are rounded
  to whole shillings using `ROUND_HALF_UP`.
- **Round exactly once, at the boundary.** `printy_fee` is computed as the **residual**, never rounded
  independently. If you round the three payout components separately they will stop summing to the
  client total, by a shilling or two, on some orders — and that is unfixable after the client pays.

Speed uplift is applied **before** the split, to the printer's production cost only. The printer
receives the full uplift; manager margin and Printy fee are then computed on the uplifted base.

---

## End-to-end lifecycle

1. **Intake.** Client runs the calculator (`/api/calculator/preview/`) or submits a `QuoteRequest`
   (`/api/intake/submit/`, or a guest draft). Items carry print spec, quantity, sides, colour mode,
   GSM hints and urgency. Public/guest calculations use `/api/calculator/public-preview/` and never
   expose shop identity or private financials.
2. **Sourcing (partner).** Partner queries `/api/partner/production-matches/` — the *production
   matcher* filters candidate shops by real capability: machine size and GSM range, paper in stock,
   rate-card readiness, available finishing, and the computed production cost.
3. **Quoting.** Partner chooses a production option, adds `manager_markup` and selects an available
   speed tier. The pipeline runs in fixed order: imposition → itemized production lines → printer
   total → speed/time-window uplift → margin → Printy fee → buyer total. `ensure_quote_financial_split()`
   freezes the split (tiered markup caps apply; minimum multiple is 1.05). Quote is sent to the client.
4. **Acceptance.** Client calls `quotes/<id>/accept/`. Offer is validated for actor, expiry and status;
   sibling quotes are superseded; price, due date and speed snapshot are locked; a `Payment` is created
   idempotently for `client_total`.
5. **Payment.** Daraja STK Push prompts the client's phone. Safaricom posts back to the single
   canonical callback `/api/payments/mpesa/callback/`, which dispatches on body shape to handle both
   the STK result and the Transaction Status Query result. `mark_payment_paid()` verifies received ==
   expected, then creates the `ManagedJob`.
6. **Dispatch.** On `transaction.on_commit`, the job is dispatched to its shop as a `JobAssignment`
   carrying the agreed `shop_payout`. Dispatch cannot roll back a confirmed payment; if it fails it
   logs and is repaired by `ensure_job_assignment_for_paid_job()`.
7. **Artwork & proof gate.** Client uploads artwork (`JobFile`, 50 MB cap, jpg/png/pdf/ai/eps).
   Manager requests confirmation → uploads a proof → approves or rejects. File status runs
   `uploaded → manager_review → proof_uploaded → proof_approved → print_ready`.
8. **Production.** Shop works the assignment through the one-action flow: accept → artwork check →
   printing start/done → each finishing step → quality check → ready for dispatch. Each advance writes
   a `JobEvent`; the most recent advance can be undone for two minutes.
9. **Delivery.** Shop marks delivered (`printy_rider` / `own_rider` / `pickup`); client confirms
   completion → job `completed`.
10. **Settlement.** Payment status advances to `release_ready`, then
    `release_managed_job_payouts()` creates the manager and shop payout rows **from the frozen split**.
    Settlement states: `pending → held → release_ready → released`, with `refunded` / `cancelled`
    branches. Failed releases are recorded via `record_payout_failure()` rather than swallowed.

---

## Speed tiers, odd-hours pricing and estimates

The speed engine lives in `services/pricing/speed_tiers.py`; the admin-editable tables live in
`pricing/models.py`.

- **Tiers:** `SpeedTier` stores key, label, multiplier, per-product target turnaround, sort order and
  active flag. Defaults are Standard (100%), Faster (150%) and Fastest (200%).
- **Odd-hours windows:** `TimeWindowRule` stores normal/overtime/overnight windows, weekdays, start/end
  times, holiday coverage and multiplier. Public holidays come from the admin-editable `PublicHoliday`
  table, never hardcoded.
- **No stacking:** the applied multiplier is the **higher of** the speed-tier multiplier and the
  time-window multiplier, never their product. The response says which rule applied and includes the
  uplift as its own line.
- **Production-only uplift:** the multiplier applies to the printer's production cost only. The printer
  receives the full uplift through the protected-floor split; margin and Printy fee are computed on the
  uplifted base. An already marked-up total is never multiplied.
- **Opt-in:** `ShopSpeedTierOptIn` and `ShopTimeWindowOptIn` decide what a printer can actually meet.
  A tier or window the printer has not accepted is hidden, not greyed out. A tier whose resulting
  ready time lands in an unaccepted window is not offered.
- **Estimates:** `EstimatePolicy` sets the minimum completed-job count. Above it, estimates use the
  median of recorded stage durations and return `source = "measured"`; below it, the printer-declared
  target is used and returns `source = "declared"`. No printer identity is exposed to the buyer.
- **Lock:** acceptance freezes price, due date and the speed snapshot. Manager changes create a new
  version and require buyer acknowledgement before payment.
- **Rush lateness:** `rush_premium` and `rush_premium_status` are separate job fields. When lateness is
  attributable to the manager/printer, the premium is marked for refund/hold and the existing 10%
  lateness penalty is recorded as a separate line. Buyer-caused lateness does not refund the premium.

Relevant endpoints:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/pricing/speed-options/` | Eligible speed/window options for a shop and spec. |
| POST | `/api/pricing/quote-speed/` | Select and freeze a speed option on a quote request. |

---

## Buyer live tracking

`ClientTrackedJobsView` serves `/api/dashboard/client/tracked-jobs/` (with compatibility aliases such
as `/api/client/tracked-jobs/` and `/api/tracked-jobs/`). The projection is built in
`jobs/buyer_tracking.py`.

- A signed-in buyer sees every job they own whose payment is confirmed, automatically — no job code,
  token, phone check or manual lookup.
- A delivered job remains visible for 24 hours, then drops off the active list.
- Late jobs sort first, then by soonest due date.
- The payload contains the buyer-facing stage list, finishing sub-steps, current stage, due date,
  estimated remaining minutes, late flag and last-updated time.
- The buyer payload never contains printer/shop identity, production cost, manager margin, fees or any
  other internal price layer.

The signed-out tracking path (job code plus phone) and manager-issued client links remain separate
flows and continue to work.

---

## Printer one-action stage flow

The printer dashboard receives one primary action per active job from the server. The flow is built in
`jobs/printer_workflow.py`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/job-assignments/<id>/advance/` | Advance the next owned stage. No body required. |
| POST | `/api/job-assignments/<id>/undo/` | Revert the most recent advance within two minutes. |

- Printer-owned steps: artwork check, printing (started/done), each finishing step actually present,
  quality check, ready for dispatch. Missing finishing steps are skipped automatically.
- Only the server clock sets timestamps. Client-supplied times are ignored and the event records that
  they were ignored.
- Out-of-order or stale advances are refused. Duplicate taps/request IDs are no-ops.
- Only the assigned shop owner can advance or undo. Only paid jobs can enter production.
- Stage budgets are display/nudge budgets; the due date still comes from the SLA/speed config.
- Overruns are recorded against the printer as delay owner. The response never includes buyer
  identity, buyer total, margin, fee tier or cost breakdown beyond the printer's own payout.

---

## Manager onboarding fee tiers

The fee-tier policy is defined in `law.txt`: jobs 1–20 waived, 21–40 reduced (default 50%), 41+ normal,
counted per manager from paid + delivered jobs above a configurable minimum value. The waiver applies
to the Printy fee only, never to the printer's rush uplift, and Printy absorbs it without reducing the
printer payout floor.

**Implementation status:** the manager onboarding fee-tier engine is not yet implemented in this
codebase. `fee_tier` in the current canonical payload refers to the existing Tier A/B/C
protected-floor pricing tier. Do not describe the onboarding tiers as live until the counter, audit
field and admin override are built.

---

## Roles and visibility

Canonical user roles (`accounts.User.role`, normalised from legacy aliases in
`accounts/services/roles.py`):

| Role | Meaning |
| --- | --- |
| `super_admin` | Printy ops. |
| `client` | Buyer. |
| `partner` | Print manager / broker. |
| `production` | Print shop staff. |

Requests are additionally resolved to an **actor role** — `public`, `client`, `partner`, `shop`,
`ops` — which drives querysets in `api/visibility.py` and `core/querysets.py`, and per-file
`JobFileVisibility` (`client`, `partner`, `shop`, `ops`, `internal`). A shop acting as its own manager
is a supported case, not a bug. Jobs also carry a `ManagedJobTopologyType` (`client_partner`,
`client_printy_support`, `partner_shop`, `shop_ops`, `ops_internal`) describing the chain the job
travelled.

Auth is JWT-first (Bearer header) for the SPA; session auth exists only so DRF's browsable API can
log in. CSRF is enforced for session-authenticated requests and never for JWT. The active dashboard
role is sent as `X-Printy-Active-Role` and validated against the roles the account really holds.

Role projections are server-side allowlists. Buyer payloads never contain imposition, sheet counts,
cost breakdown, margin, fee tier or printer payout. Printer payloads never contain buyer identity,
buyer totals, margin or fee tier.

---

## Printing domain glossary

Read this before touching pricing or matching code — the vocabulary is not standard web vocabulary.

| Term | Meaning |
| --- | --- |
| **GSM** | Paper weight, grams per square metre. `Paper.gsm`; a machine's `min_gsm`/`max_gsm` bounds what it can run. |
| **Sheet size** | Pre-cut sizes: `A4`, `A3`, `SRA3` (320×450 mm, the shrink-proof A3 used for booklets), `A2`–`A0`, `custom`. |
| **Bleed** | Extra print area beyond the trim so cutting leaves no white slivers. Default 3 mm. |
| **Imposition** | Laying finished pieces out on a sheet to maximise yield. `catalog/imposition.py` does a grid fit, tries both orientations, and returns `pieces_per_sheet()`; sheets needed is `ceil(quantity / pieces_per_sheet)`. |
| **Wastage / spoilage** | Extra sheets billed to cover setup and spoilage: `fixed_waste_sheets` (2) + `variable_waste_rate` (10%), floored by `minimum_billable_sheets` (3). |
| **Setup cost** | Fixed per-job overhead: setup minutes, labour rate, machine setup fee, admin handling fee, file-check fee. |
| **Sides / colour mode** | Canonical enums `SIMPLEX`/`DUPLEX` and `BW`/`COLOR`. Friendly input ("single-sided", "both sides") is normalised on input. |
| **Rate card** | A shop's machine + paper + finishing prices. Readiness is tracked by `ShopRateCardSetup`. |
| **Finishing** | Post-print operations (lamination, binding, folding, cutting) priced by `FinishingRate`. |
| **Production matching** | Choosing which shop can actually produce a job. `services/production_matching.py`. |
| **Production option** | One shop's offer of a `production_cost` + terms for fulfilling a request. |
| **Manager markup** | The partner's gross margin above production cost, capped by tier, minimum multiple 1.05. |
| **Financial split** | The frozen money snapshot: production cost, markup, shop payout, manager payout, `printy_fee`, client total, tier, policy version. |
| **Speed tier** | Admin-editable turnaround option with a production multiplier. The applied multiplier is the higher of tier and odd-hours window. |
| **Time window** | Admin-editable odd-hours rule (evening, overnight, Sunday, public holiday) with its own multiplier and printer opt-in. |
| **Rush premium** | The production uplift on a non-standard tier, stored separately so it can be refunded or held on late rush jobs. |
| **Measured / declared estimate** | `measured` uses the median of completed-job stage durations; `declared` uses the printer's target until enough jobs exist. |
| **Stage budget** | Weighted time allowance per printer stage, locked at acceptance and used for nudges, not as the final due date. |
| **Proof** | A pre-production file the manager approves before the press runs. |
| **Artwork confirmation** | Explicit client sign-off that blocks dispatch (`not_required`, `requested`, `approved`, `rejected`). |
| **Custody** | Who currently holds a job or file: `awaiting`, `held`, `released`. |
| **Urgency** | `standard`, `same_day`, `express`, `after_hours`, `emergency`. |
| **STK Push** | Safaricom Daraja prompt-to-pay: pushes a payment request to the customer's phone. |
| **Transaction Status Query** | Daraja async confirmation API, wired to the `ResultURL`/`TimeoutURL`. |

---

## Layout

```
config/            settings, root urls, wsgi/asgi, test settings, startup safety checks
accounts/          custom user, roles, JWT, allauth, email verification
shops/             the seller/production business (owner, location, VAT, status)
inventory/         machines and paper stock (per-shop)
pricing/           rate cards, speed tiers, time windows, holidays, fee/wastage/setup policies
catalog/           products, MPTT categories, finishing options, imposition math
quotes/            the marketplace: requests, quotes, items, splits, drafts, attachments, threads
jobs/              CANONICAL operational layer: ManagedJob, assignments, files, events, payouts,
                   buyer tracking, timing, printer one-action workflow
production/        shop-side mirrored view of a job (compatibility layer, see below)
payments/          canonical business payments, STK requests, phone consent
mpesa_payments/    Daraja layer: transactions, cached OAuth token, callback audit log
clients/           client records, phone identity and client links
client_calculator/ client-facing calculator drafts and quote handoff
notifications/     in-app + email notifications
contact/           public contact form
gallery/           public gallery assets
locations/         location/reference data
setup/             first-run and setup helpers
workflow/          separate JSON state-machine port (see caveat below)
api/               HTTP surface: viewsets, dashboards, permissions, visibility, throttling, SEO
services/          pricing engine, speed tiers, production matching, VAT — a package, not a Django app
common/            shared money/rounding helpers
core/              shared querysets and base utilities
tests/             cross-cutting regression suite
docs/              the real documentation surface (see below)
```

### Two workflow engines — don't conflate them

- **`jobs`** is canonical. It owns money, files, dispatch, payouts, delivery, timing and the printer
  one-action flow.
- **`workflow`** is a faithful Django port of the TypeScript `printy_workflow` state-machine demo
  (press states, custody, feed/history, "nudge"). It has its own models, router, camelCase serializers
  and `seed_workflow` command, and is exposed at `/api/workflow/`. It is useful for modelling and
  demos, but it is **not** the source of truth for fulfilment.

### Legacy `ProductionOrder`

`production.ProductionOrder` mirrors the canonical job for shop-facing displays. It is kept in sync by
`jobs/assignment_services.py` and `jobs/delivery_services.py`. It is a compatibility/display layer, not
a second source of truth — never derive money or authoritative status from it.

### Compat routes are intentional

`api/urls.py` contains a number of `-compat` aliases and duplicate paths
(`client-jobs-compat`, `setup-status-compat`, `public/job/<token>/` vs `managed-jobs/public/<token>/`,
`jobs/` vs `dashboard/client/jobs/`, multiple tracked-jobs aliases). These exist for the Nuxt
frontend's benefit. Don't "clean them up" without checking the frontend first.

---

## Running it locally

```bash
python -m venv env
# Windows: env\Scripts\activate
# macOS/Linux: source env/bin/activate
pip install -r requirements.txt
cp .env.local.example .env          # local template; .env.example is the production template
python manage.py migrate
python manage.py runserver
```

PostgreSQL is expected in development and production. Optional seed/demo commands:

```bash
python manage.py configure_site
python manage.py seed_shop_pricing        # inventory: machines + paper for a shop
python manage.py seed_paper_catalog       # pricing: standard paper catalogue
python manage.py seed_data                # core: MVP reference data
python manage.py seed_demo_accounts       # accounts: demo users per role
python manage.py seed_workflow            # workflow app state-machine demo data
```

Seeds never confirm a rate card: confirmation is the owner's explicit "Confirm and go live" action
(D1). `seed_paper_catalog` writes a rate-card setup row with `is_confirmed=False`, which hides that
shop from manager find-printer results until the owner confirms it. A shop with **no** rate-card setup
row (for example after `seed_shop_pricing --shop <slug>`) stays visible and usable, because it never
opened the rate-card screen.

To get a visible, confirmed shop locally, seed pricing and then confirm it (the
`/app/printer/rates` "Confirm and go live" button does the same):

```bash
python manage.py seed_shop_pricing --shop <slug>
python manage.py shell -c "from services.pricing.mvp_rate_card import complete_shop_rate_card_setup; from shops.models import Shop; complete_shop_rate_card_setup(Shop.objects.get(slug='<slug>'))"
```

Before launch, run `python manage.py report_rate_card_readiness` and confirm every shop
you need on day one; the report also lists confirmed shops that are no longer usable.

### Tests

```bash
python -m pytest
```

The root `conftest.py` pins `DJANGO_SETTINGS_MODULE=config.test_settings`, which imports production
settings but switches to SQLite, fast password hashing, and forces `MPESA_ENV=sandbox` — so the suite
never touches live money. Much of the suite is phase-numbered (`test_phase_*.py`) and encodes
regressions for previously shipped bugs; treat those as executable documentation.

Focused suites for the current work:

```bash
python -m pytest jobs/test_printer_stage_advance.py jobs/test_buyer_tracked_jobs.py -q
python -m pytest pricing/test_speed_tiers.py -q
python -m pytest jobs/test_law_part4_timing.py -q
```

On Windows, prefix the command with the venv interpreter if the environment is not activated:
`env\Scripts\python.exe -m pytest ...`.

---

## Configuration and production safety

`docs/env_vars.md` is the source of truth for environment variables. The important categories:
`SECRET_KEY` / `APP_ENV` / `ALLOWED_HOSTS`, `DB_*`, `FRONTEND_URL` + `CORS_*` / `CSRF_*`, `EMAIL_*`,
the HTTPS cookie/HSTS flags, and `MPESA_*`.

The settings module is deliberately **loud about misconfiguration**. Rather than failing silently at
the first bad email or payment, it refuses to start:

- `printy.E001`–`E004` — console email backend or missing/placeholder SMTP credentials in a non-local
  environment (emails would be printed to the server console and never delivered).
- `printy.E010`–`E016` — Daraja aimed at `MPESA_ENV=production` with missing or placeholder
  credentials, a non-HTTPS or localhost callback, or a short code sent to a BuyGoodsOnline app that
  Daraja rejects.
- Placeholder `SECRET_KEY`, localhost `FRONTEND_URL`, or non-HTTPS CORS/CSRF origins all raise
  `ImproperlyConfigured` when `DEBUG=False`.
- `MPESA_FORCE_STUB` simulates Daraja in-process so end-to-end runs can complete without a human
  approving the prompt. It is **hard-gated to never work with `DEBUG=False`**, so a deployment cannot
  fake a real payment.

`APP_ENV` defaults to `production` on purpose: forgetting to set it fails loudly rather than silently
running as development. Local work must set `APP_ENV=local`.

Deployment and verification:

- `docs/PRODUCTION_DEPLOYMENT_RUNBOOK.md`
- `docs/PRODUCTION_SMOKE_TEST_CHECKLIST.md`
- `docs/DARAJA_PRODUCTION_CHECKLIST.md`
- `docs/EMAIL_DELIVERY.md`

Sanity checks before trusting a deploy:

```bash
python manage.py check
python manage.py send_test_email --to you@example.com
```

---

## API surface highlights

| Area | Main routes |
| --- | --- |
| Auth | `/api/auth/login/`, `/api/auth/register/`, `/api/auth/token/refresh/`, `/api/auth/me/`, `/api/auth/client-invites/claim/` |
| Calculator / intake | `/api/calculator/preview/`, `/api/calculator/public-preview/`, `/api/calculator/guest-drafts/`, `/api/intake/submit/` |
| Manager sourcing | `/api/partner/production-matches/`, `/api/dashboard/partner/quotes/<id>/shop-options/`, `/api/dashboard/partner/quotes/<id>/prepare/` |
| Quote pricing | `/api/partner/quotes/preview/`, `/api/pricing/quote-financials/preview/`, `/api/pricing/quotes/<id>/financials/` |
| Speed | `/api/pricing/speed-options/`, `/api/pricing/quote-speed/` |
| Acceptance / payment | `/api/quotes/<id>/accept/`, `/api/payments/stk-push/`, `/api/payments/mpesa/callback/` |
| Client dashboard | `/api/dashboard/client/quotes/`, `/api/dashboard/client/jobs/`, `/api/dashboard/client/tracked-jobs/` |
| Printer dashboard | `/api/shop/assignments/`, `/api/job-assignments/<id>/advance/`, `/api/job-assignments/<id>/undo/` |
| Managed job detail | `/api/managed-jobs/<id>/`, files, events, settlement, delivery, artwork confirmation |
| Client links | `/api/client-portal/links/<token>/`, `/otp/request/`, `/otp/verify/`, `/artwork/`, `/acknowledge/`, `/payment/` |
| Notifications | `/api/me/notifications/`, `/api/me/notifications/unread-count/`, `/api/me/notifications/mark-all-read/` |

---

## Documentation

`docs/` is the detailed reference; this README is the orientation.

**Architecture and intent**
`CANONICAL_MODELS.md` (the intended design — read this first), `MODEL_MAP.md`,
`QUOTE_MARKETPLACE_IMPLEMENTATION_NOTES.md`, `CALCULATOR_ARCHITECTURE_AUDIT.md`,
`API_URL_AUDIT.md`, `QUOTE_ATTACHMENT_AUDIT.md`

**API and contracts**
`api_contract.md`, `QUOTE_ENDPOINTS.md`, `QUOTE_SERIALIZERS.md`, `QUOTE_SUMMARY_SERVICE.md`,
`PRICING_API_GUIDE.md`, `MANDATORY_FIELDS.md`, `PRINT_SIDES_COLOR_MODE_CONTRACT.md`,
`NOTIFICATION_EVENTS.md`, `QUOTE_ACCESS_RULES.md`, `QUOTE_NAMING.md`

**Operations**
`PRODUCTION_DEPLOYMENT_RUNBOOK.md`, `PRODUCTION_SMOKE_TEST_CHECKLIST.md`,
`DARAJA_PRODUCTION_CHECKLIST.md`, `EMAIL_DELIVERY.md`, `env_vars.md`

**Workflow**
`workflow/manager-quote-payment-approval-workflow.md`

---

## Housekeeping note

The repo root contains local debugging residue — `jobA_*.json` / `jobB_*.json` fixtures,
`runserver*.log`, `verify_*.py`, `rerun_jobab_*.py`, `_forensics_tmp.py`, and `forensic_creds.json`
(which holds plaintext credentials). These are scratch artefacts, not project files. They are already
covered by `.gitignore` so they cannot be committed by accident, but they clutter the working tree and
`forensic_creds.json` is worth deleting outright after any investigation that needed it.

`scripts/deploy.sh` is the one-command production deploy; see
`docs/PRODUCTION_DEPLOYMENT_RUNBOOK.md`.
