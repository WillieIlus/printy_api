# Daraja Production Checklist

This checklist is for real Safaricom production validation. Do not mark any payment as paid unless a verified callback (or a super-admin resolution backed by a receipt) confirms it.

Nothing in this document has been verified against live Daraja. The audit
sandbox had no Safaricom access; the behaviour described is what the code and
its tests do.

## Startup guard (fails fast — read this first)

The backend refuses to boot when `MPESA_ENV=production` is paired with missing or
placeholder Daraja credentials. `manage.py check` / gunicorn / `runserver` will
exit with these errors so a live server can never silently start without M-Pesa:

| ID | Condition |
| --- | --- |
| `printy.E010` | `MPESA_CALLBACK_URL` not set with `MPESA_ENV=production` |
| `printy.E011` | `MPESA_CALLBACK_URL` not HTTPS with `MPESA_ENV=production` |
| `printy.E012` | `MPESA_CALLBACK_URL` points at localhost/127.0.0.1 |
| `printy.E013` | `MPESA_CONSUMER_KEY` missing or `replace-with-*` placeholder |
| `printy.E014` | `MPESA_CONSUMER_SECRET` missing or placeholder |
| `printy.E015` | `MPESA_SHORTCODE` missing, placeholder, or not 5–11 digits. **Inverted** for `MPESA_SHORTCODE_TYPE=buygoodsonline`: that product has no short code, so a *set* value is the error. |
| `printy.E016` | `MPESA_PASSKEY` missing or placeholder. **Inverted** for `MPESA_SHORTCODE_TYPE=buygoodsonline`. |
| `printy.E017` | `MPESA_CALLBACK_SECRET` missing, placeholder or < 32 chars while `DEBUG=False` (settings import also raises `ImproperlyConfigured`). |
| `printy.E018` | `MPESA_CALLBACK_SECRET` contains characters that are not URL-safe. |
| `printy.E019` | An `MPESA_CALLBACK_ALLOWED_IPS` entry is not an IP/CIDR. |

The runtime guard `mpesa_payments.services.validate_production_config()` applies
the same checks again before any HTTP call to Daraja (belt and braces).

The deprecated dev flags `MPESA_SIMULATE_STK_PUSH` / `MPESA_AUTO_CONFIRM_STK_PUSH`
are unused and removed; local sandbox simulation is done via the
`simulate_mpesa_callback` management command (refuses when `MPESA_ENV=production`).

Tests: `tests/test_mpesa_production_config.py` runs the guard matrix on every
test pass.

## Verify before going live

```bash
python manage.py check            # must exit 0 — no printy.E01x errors
```

## Callback URL and the callback secret

Daraja does not sign callbacks. A callback is trusted only when **all three**
of these hold (`payments/callback_security.py`):

1. **Secret in the URL.** `MPESA_CALLBACK_SECRET` (>= 32 URL-safe characters,
   required when `DEBUG=False`) is the last path segment of the URL. The code
   appends it automatically to every URL it sends Daraja (`CallBackURL` on
   each STK push, `ResultURL` and `QueueTimeOutURL` on status queries).
   Keep `MPESA_CALLBACK_URL` at the canonical value **without** the secret:

   - configured: `MPESA_CALLBACK_URL=https://api.printy.ke/api/payments/mpesa/callback/`
   - URL Daraja actually calls: `https://api.printy.ke/api/payments/mpesa/callback/<MPESA_CALLBACK_SECRET>/`

   If you register a URL by hand anywhere in the Daraja portal, register the
   form **with** the secret (or `?token=<secret>`). A missing or wrong secret
   gets 403 and changes nothing. If the secret is unset in production, every
   callback is refused (fail closed).
2. **Source IP on the allowlist.** The peer must be in
   `MPESA_CALLBACK_ALLOWED_IPS`. When that is unset and `DEBUG=False`, the
   built-in list `SAFARICOM_CALLBACK_IPS` in `payments/callback_security.py`
   is enforced. **That built-in list has not been checked against
   Safaricom's current published list.** The audit could not reach
   Safaricom from its sandbox. Before go-live:
   - log the real source IPs of sandbox and first production callbacks
     (the `payments` logger prints `ip=` on every rejection, and nginx logs
     the peer);
   - compare them with the list Safaricom publishes today;
   - then pin `MPESA_CALLBACK_ALLOWED_IPS` explicitly.

   A non-allowlisted source gets 403 even when it carries the right secret.
3. **A transaction we persisted.** An STK callback must carry a
   `CheckoutRequestID` that we stored when Daraja accepted our own push. An
   unknown id gets 404 and changes nothing. A status-query result must match
   a receipt we stored; an unknown receipt is logged and answered 200.

**Proxies (`MPESA_TRUSTED_PROXIES`).** The source IP is `REMOTE_ADDR`, unless
that peer is listed in `MPESA_TRUSTED_PROXIES` (default `127.0.0.1,::1`, i.e.
nginx on the same host). Only then is `X-Forwarded-For` read, right to left,
skipping trusted hops. A raw `X-Forwarded-For` from any other peer is ignored.
If a load balancer sits in front of nginx, add its address. Otherwise every
callback appears to come from the balancer and is refused. The same resolver
keys every API throttle.

**Reachability is not authenticity.** A URL that Safaricom can reach is also
reachable by anyone. "The callback URL works" proves nothing about who
posted to it. Only the three checks above make a callback trustworthy. Never
add a bypass (an unauthenticated debug route, `*` in the allowlist, or the
secret in a public file) to "get callbacks working".

Response codes Printy returns to Daraja:

| Code | When |
| --- | --- |
| 403 | wrong or missing secret, or IP not allowlisted; no state change |
| 404 | unknown `CheckoutRequestID`; no state change |
| 400 | no `CheckoutRequestID`, or a non-integer `ResultCode` |
| 200 | applied, or already final (duplicate or late callback) |

## Canonical payment lifecycle (`payments.Payment` + `MpesaSTKRequest`)

Quote and managed-job payments use `payments.Payment` (one per payable) and
`payments.MpesaSTKRequest` (one per STK prompt). `JobPayment` is deprecated
and no longer exists. The cashier flow (`mpesa_payments.MpesaPayment`) is
separate and has its own states.

```
Quote(sent) -> accept -> Payment(pending; amount = expected_amount =
QuoteFinancialSplit.client_total)
  -> STK push -> MpesaSTKRequest(pending -> sent), Payment(processing)
  -> verified callback:
       success, receipt present, Amount == expected -> MpesaSTKRequest(success),
         Payment(paid) -> ManagedJob created/unlocked (payment_confirmed)
       success but unverifiable                     -> needs_review (both rows)
       ResultCode 1032                              -> MpesaSTKRequest(cancelled)
       other failure                                -> MpesaSTKRequest(failed)
  -> no callback (lost): `mpesa_recover_payments` asks Daraja (STK Query) and
     replays the answer through the same callback state machine:
       final answer       -> as above (a bare success -> needs_review)
       still processing,
       past the window    -> MpesaSTKRequest(expired), Payment(expired)
       Daraja error       -> nothing changes
```

- **Amounts are server-side.** The STK amount always comes from the stored
  split or job; a client-sent amount is ignored for a payable. Only a
  verified success callback marks a payment `paid`. STK initiation alone
  never does.
- **`needs_review`.** A success that cannot be verified is recorded as
  `needs_review` and is **never** `paid`. That covers:
  - no receipt or no Amount;
  - an Amount different from the expected amount;
  - a second debit on an already-paid payment;
  - a success that arrives while the payment is already under review.

  While a payment is under review, the quote cannot be re-pushed or
  re-created.
- **180-second STK expiry.** An unanswered prompt blocks a second prompt for
  the same payment only while it is genuinely in flight:
  `MPESA_STK_IN_FLIGHT_SECONDS`, default 180, minimum 60. The next push marks
  older unanswered prompts `expired` and sends a fresh one. A push inside the
  window returns the existing in-flight prompt instead of sending a second
  one. A late callback for an expired prompt **is** still applied, with the
  same verification, because expiry is Printy's bookkeeping, not Daraja's. A
  late *failure* for an old prompt never touches a newer pending prompt.
- **Concurrency.** STK initiation, callbacks and resolution all lock the
  `Payment` row (`select_for_update`). The PostgreSQL CI job runs a real
  two-connection test showing that two simultaneous pushes create one prompt.

### Resolving `needs_review` (platform super-admin only)

Only `is_superuser` accounts may resolve. Managers, printers, clients and
plain `is_staff` users get 403. Resolution goes through the API, not a
Django-admin button:

- `GET  /api/admin/payments/needs-review/` lists canonical and cashier
  payments that are awaiting a decision.
- `POST /api/admin/payments/<id>/resolve-review/` resolves a canonical
  `Payment`.
- `POST /api/admin/mpesa-payments/<id>/resolve-review/` resolves a cashier
  `MpesaPayment`.

Body: `resolution` = `confirm_paid` | `mark_failed`, plus `reason` (required).
For `confirm_paid`, also send `receipt_number` and `amount`.
- `confirm_paid`:
  - The amount must equal the stored expected amount. Otherwise an explicit
    `override_reason` is required.
  - The receipt must match the one Daraja reported, if one was recorded,
    and must not already settle another payment.
  - It then runs the normal paid path (ManagedJob, notifications).
- `mark_failed` closes the review; the client can pay again with a fresh
  push.

Every resolution writes an admin audit entry: actor, time, reason, amounts
and receipt. The cashier admin also has a "Flag for manual review" action;
it only flags and does not resolve.

### Recovering a canonical payment whose callback was lost (audit #1c)

`python manage.py mpesa_recover_payments` (service: `payments/recovery.py`)
finds STK prompts that were sent more than `MPESA_STK_IN_FLIGHT_SECONDS` ago
and never got a callback, and asks Daraja about each one with **STK Query**
(`/mpesa/stkpushquery/v1/query`). The answer is replayed through the same
`handle_stk_callback` state machine as a verified callback, with the same
amount and receipt checks:

| Daraja STK Query answer | Result |
| --- | --- |
| `ResultCode 0` with `MpesaReceiptNumber` and `Amount` == expected | `paid` (ManagedJob created), exactly like a callback |
| `ResultCode 0` without receipt/amount (**Daraja's documented answer**) | `needs_review` — **never `paid`**. Reconcile the receipt against the M-Pesa statement, then `POST /api/admin/payments/<id>/resolve-review/` |
| `ResultCode 0` with a different `Amount` | `needs_review` |
| `1032` / other failure codes | the callback failure branch (`cancelled` / `failed`; a late failure for an expired prompt leaves the payment alone) |
| "The transaction is being processed" (`500.001.1001`), prompt past the window | prompt `expired`; payment `expired` if nothing else is in flight |
| network error, auth error, HTTP 429, malformed answer | **nothing changes**; a 429 stops the run |

- Final payments (`paid`, `failed`, `cancelled`, `needs_review`) are never
  queried. Running it twice, or alongside a real callback, is a no-op.
- **Expired is not final.** Expired prompts keep being queried for
  `MPESA_RECOVERY_MAX_AGE_HOURS` (default 24), and a late callback still
  applies, so a late success becomes `paid` (or `needs_review`). The client
  may pay again on the same `Payment` row; a second debit is flagged
  `needs_review` for a refund decision.
- **Rate limits:** `MPESA_RECOVERY_QUERY_INTERVAL_SECONDS` (default 1 s)
  between queries, `MPESA_RECOVERY_QUERY_COOLDOWN_SECONDS` (default 60 s)
  before the same prompt is queried again (shared cache, so use Redis in
  production), `--limit` per run (default 50).
- `MPESA_FORCE_STUB` / test mode: Daraja is never queried.
- **Schedule it** from cron, e.g. every 5 minutes:
  `*/5 * * * * cd /srv/printy_api && venv/bin/python manage.py mpesa_recover_payments`.
  `--dry-run` lists candidates without calling Daraja; `--payment-id N`
  queries one payment immediately.
- **One payment, from the API (super-admin only):**
  `POST /api/admin/payments/<id>/recover/` (optional `reason`). It queries
  immediately (no age threshold or cooldown), returns the outcome, writes an
  admin audit entry, and answers `502` on a Daraja error (nothing changed).
- STK Query against real Daraja has **not** been exercised offline; the
  answer shapes above follow Daraja's documentation and are covered by
  mocked tests. Check one real lost-callback case in sandbox before relying
  on it.

If the customer says they paid and recovery shows `needs_review`, do not ask
them to pay again until the receipt is checked against the statement.

## Go-live reconciliation

Before announcing go-live, and daily for the first two weeks:

1. Export the Daraja / M-Pesa organisation statement for the period. Use
   the portal or the bank-side statement, not Printy's own tables.
2. List Printy payments marked paid in the same period:
   - `Payment.objects.filter(status="paid")` (receipt, amount, paid_at);
   - `MpesaPayment` rows that are confirmed.
3. Match them on receipt number and amount:
   - **paid in Printy but missing from the statement:** stop and
     investigate. This would mean a forged or mis-applied confirmation.
   - **on the statement but not paid in Printy:** a lost callback or a
     `needs_review` item; resolve it as above.
   - **amount differs:** this must already be `needs_review`; if it is not,
     escalate.
4. Check that no `ManagedJob` reached `payment_confirmed` without a matching
   statement line.
5. Record the result (date, counts, discrepancies) in the ops log.

## Phone number expectations

Frontend and backend should submit Kenyan MSISDNs in one of these accepted forms:
- `2547XXXXXXXX`
- `07XXXXXXXX`
- `7XXXXXXXX`

The backend normalizes to `2547XXXXXXXX` before Daraja submission.

## Payment states the frontend sees

The canonical vocabulary is `payments/states.py`. Only a provider-confirmed
`paid` maps to PAID. `processing` and `needs_review` both show as "awaiting
confirmation", never as paid.

| `Payment.status` | Meaning | Frontend card |
| --- | --- | --- |
| `pending` | created at quote acceptance, no prompt yet | not started |
| `processing` | STK prompt sent, waiting for the callback | awaiting confirmation |
| `paid` | verified success callback, or a super-admin `confirm_paid` | paid |
| `failed` | failure callback, or a super-admin `mark_failed` | failed |
| `cancelled` | the customer cancelled the prompt (1032) | cancelled |
| `expired` | the prompt passed the in-flight window and Daraja still had no outcome (set by `mpesa_recover_payments`). Not final: a late success still applies, and the client may pay again on the same row | timed out |
| `needs_review` | an unverifiable success; money may have moved | awaiting confirmation |

A failure callback leaves the `Payment` unchanged in three cases: another
prompt for it is still in flight, it is under review, or the failing prompt
had already expired.

`MpesaSTKRequest.status` values: `pending`, `sent` (both "in flight"),
`success`, `failed`, `cancelled`, `expired`, `needs_review`.

Duplicate callbacks are no-ops. A payment is never settled twice. A second
debit is flagged `needs_review` so someone decides on a refund.

## Logs to inspect after a test payment

Backend application logs:
- Gunicorn journal output
- Django `payments` logger output

Look for:
- `Received billing M-Pesa callback checkout_request_id=... merchant_request_id=...`
- `Rejected M-Pesa callback ...` lines (403s). Each one logs `ip=`; use these
  to confirm the real Safaricom source IPs before pinning
  `MPESA_CALLBACK_ALLOWED_IPS`.
- `M-Pesa STK success NOT applied, flagged needs_review ...` (ERROR level)
- duplicate callback warnings only if Daraja retries
- no stack traces
- no localhost callback warnings in production

Nginx logs:
- access log entry for `POST /api/payments/mpesa/callback/<secret>/`. **Mask
  the secret segment in the access log** (see `docs/DEPLOY_NOTES.md`); anyone
  who can read raw logs can otherwise forge callbacks from an allowlisted
  network position.
- no 4xx/5xx callback failures

## Frontend card expectations

Before callback:
- show pending payment state only

After success callback:
- show paid state only after backend confirms it

After failure callback:
- show failed state

After amount mismatch:
- show needs review

Never:
- fake paid status from STK initiation alone
- treat missing callback as success

## Recovering a cashier `MpesaPayment` whose callback was lost

These commands cover the cashier flow (`mpesa_payments.MpesaPayment`) only.
For canonical `Payment` rows see "Recovering a canonical payment" above.
Daraja occasionally drops an STK callback. Two tools, in order:

```bash
# 1. Did the customer finish the prompt? (needs checkout_request_id)
python manage.py mpesa_reconcile

# 2. The customer says they paid and has a receipt. Ask Safaricom directly.
python manage.py mpesa_verify_receipt --receipt QG1234XY --phone 0712345678
```

`mpesa_reconcile` can only ever move a payment **out of** `pending`, and on a
successful query result it parks the payment in `needs_review` rather than
`paid` — a query proves the prompt was completed, not that our expected amount
arrived.

`mpesa_verify_receipt` is read-only and uses the Transaction Status Query
(`/mpesa/transactionstatus/v1/query`). Daraja answers that endpoint
asynchronously: a `0` ResponseCode means *notification sent*, and the actual
verdict is POSTed to `MPESA_RESULT_URL`. That notification is the only Daraja
response that both proves money moved and reports the amount, so it is allowed
to settle a payment — it reuses `mark_confirmed`, including the amount-mismatch
guard, and it never overwrites a payment that is already terminal.

## Automatic payouts (B2C)

Prompt 7 PART 2. Printy can pay the shop owner and the manager through Daraja
**B2C** once a job is finished. It is **off by default** and stays off until
every item below is ticked. Code: `jobs/auto_payouts.py`, `jobs/payout_state.py`,
`payments/b2c.py`, `jobs/payout_views.py`.

### When a payout is sent (all must hold, checked again under a row lock at send time)

1. The job is `completed` (decision 3: pay only when completed). A job counts
   as completed only when the buyer entered the handover code, or a super-admin
   resolved a dispute (`completion_method` `buyer_handover` / `admin_dispute`).
   Jobs completed as `buyer_no_code` (dispatched before the handover code
   existed) are **never auto-paid**: they go to `needs_review`
   (`legacy_no_handover_code`) and need an admin approval first. So do jobs
   with no `completion_method` at all (completed before the handover release,
   possibly by the shop without delivery): `completion_not_verified`.
2. No open dispute.
3. No lateness hold (`ManagedJob.payout_hold`). A held rush premium does **not**
   block the base payout (decision 2); it is settled separately.
4. The client payment is verified: a `Payment` is `paid` and none for the job is
   `needs_review`.
5. The amounts come from the stored `QuoteFinancialSplit` (`shop_payout`,
   `broker_payout`), match the job/assignment copies, are whole KES, and
   `shop + manager <= client_total`. Anything else goes to `needs_review`.
6. The payee can receive payouts (`can_receive_payouts`) and has a verified
   phone (`UserProfile.phone_verified_at` + `phone_normalized`). Otherwise the
   payout stays `pending` with a `waiting_reason` that admins can see.
7. The payout is under `AUTO_PAYOUT_MAX_AMOUNT` and today's total (Nairobi
   day: processing + released + needs_review) stays under
   `AUTO_PAYOUT_DAILY_CAP`; otherwise `needs_review`.

### Settings

| Variable | Default | Notes |
|---|---|---|
| `AUTO_PAYOUTS_ENABLED` | off | Kill switch. Turning it off stops all sends at the next run. |
| `MPESA_B2C_SHORTCODE` | – | B2C paybill / shortcode (PartyA). Required. |
| `MPESA_B2C_INITIATOR_NAME` | `MPESA_INITIATOR_NAME` | Daraja API operator. |
| `MPESA_B2C_SECURITY_CREDENTIAL` | `MPESA_SECURITY_CREDENTIAL` | Initiator password encrypted with the Safaricom cert. |
| `MPESA_B2C_COMMAND_ID` | `BusinessPayment` | |
| `MPESA_B2C_RESULT_URL` | `<MPESA_CALLBACK_URL base>/b2c/result/` | The callback secret is appended automatically. HTTPS in production. |
| `MPESA_B2C_TIMEOUT_URL` | `<MPESA_CALLBACK_URL base>/b2c/timeout/` | Same. |
| `MPESA_CALLBACK_SECRET` | – | 32+ chars; shared with the STK callbacks. |
| `AUTO_PAYOUT_MAX_AMOUNT` | 50000 | KES, per payout. |
| `AUTO_PAYOUT_DAILY_CAP` | 200000 | KES, per Nairobi day. |
| `AUTO_PAYOUT_MAX_ATTEMPTS` | 3 | Then `needs_review` (`retries_exhausted`). |
| `AUTO_PAYOUT_RETRY_BASE_SECONDS` | 300 | Backoff = base × 2^(attempt−1). |
| `AUTO_PAYOUT_RESULT_TIMEOUT_MINUTES` | 60 | No result callback by then → `needs_review`. |

Production (`DEBUG` off) **refuses to boot** with `AUTO_PAYOUTS_ENABLED` on and
any of the above missing, a placeholder value, a non-HTTPS callback URL, or a
short callback secret. With it off, nothing is required and nothing is sent.

### Running it

```bash
# cron, e.g. every 5 minutes
python manage.py run_auto_payouts
```

Each run: moves `processing` payouts with no result after
`AUTO_PAYOUT_RESULT_TIMEOUT_MINUTES` to `needs_review`, cancels unsent payouts
of cancelled jobs (never one whose money may have moved), prepares
payout rows for completed jobs, then sends what is due. Concurrent runs are
safe: a payout is moved to `processing` and committed **before** the B2C
request is sent, under row locks (job, then payout).

### Money safety rules

- Each attempt has its own key `PRINTY-PO<payout id>-A<attempt>`, sent as
  `OriginatorConversationID`. A result callback is applied only when it matches
  the current key **and** the stored `ConversationID`.
- A success callback releases the payout only with a receipt and the **exact**
  amount. Duplicates are no-ops; unknown, stale-attempt, mismatched or
  wrong-amount callbacks change nothing and are logged.
- Only a B2C callback can mark a B2C payout `released`. `processing` and
  `needs_review` payouts can't be released manually (`PayoutTransitionError`),
  and `released` and `cancelled` are final.
- Definite rejections (OAuth failure, connect timeout, 4xx, `ResponseCode != 0`)
  are retried with backoff. Anything ambiguous (read timeout, 5xx, a body we
  can't parse, a queue timeout) goes straight to `needs_review` and is
  **never retried automatically**: the money may have moved.
- Every status change is appended to `ManagedJobPayout.audit_log` (who, source,
  reason, key, amount).

### Admin API (super_admin only)

- `GET /api/admin/payouts/?status=needs_review&waiting_reason=…&role=…&job=…`
- `POST /api/admin/payouts/<id>/approve/` with
  `{"confirm_amount": "800.00", "reason": "…", "confirm_not_paid": true}`. The
  amount must equal the payout. A reason is required. `confirm_not_paid` is
  required when money may already have moved (`ambiguous_send`, `queue_timeout`,
  `result_timeout`): check the M-Pesa statement first. Data problems
  (no split, split exceeds the client total, amount mismatch, not whole KES)
  can't be approved: fix the data instead. Approval sends the payout back to
  `pending`, and it then follows the normal path (all gates still apply).
- `POST /api/admin/payouts/<id>/retry/` with `{"reason": "…"}` for a `failed`
  payout.
- `GET /api/admin/jobs/<id>/money-summary/`: client paid, shop, manager,
  Printy fee, unallocated, holds, payout rows. Read-only.

Approvals and retries are written to the Django admin log (`LogEntry`).

Payees: `GET /api/payouts/mine/` (shop owner: own shop payouts, manager: own
manager payouts, clients: 403).

### Before turning it on

- [ ] B2C product enabled on the shortcode; initiator created; security
      credential generated from the **production** certificate.
- [ ] `MPESA_B2C_*` set; `python manage.py check --deploy` boots.
- [ ] B2C result and timeout URLs reachable from Safaricom (same IP allow-list
      and nginx path rules as the STK callback).
- [ ] One sandbox payout run end-to-end (`run_auto_payouts` → result callback →
      `released`, receipt stored).
- [ ] Caps agreed with finance.
- [ ] Cron installed; an alert on `needs_review` payouts.
- [ ] Then set `AUTO_PAYOUTS_ENABLED=true`. To stop: set it back to false.
      In-flight payouts still reconcile from their callbacks.
