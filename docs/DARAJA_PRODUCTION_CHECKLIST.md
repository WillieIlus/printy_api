# Daraja Production Checklist

This checklist is for real Safaricom production validation. Do not mark any payment as paid unless the callback confirms it.

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

## Exact callback URL to register

`https://api.printy.ke/api/payments/mpesa/callback/`

## Portal checks

- The Daraja app is in the production environment, not sandbox.
- `MPESA_SHORTCODE_TYPE` names the **one product the app is provisioned for** —
  `paybill`, `till` or `buygoodsonline`. A Daraja app is provisioned for a single
  product and the wrong `TransactionType` is rejected as `Invalid TransactionType`.
- `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE`, and `MPESA_PASSKEY` all belong to the same production app.
- The shortcode/passkey pair matches the same production paybill or till configured in Daraja.
- The registered callback URL matches the exact deployed backend route above.
- For `buygoodsonline`, `MPESA_SHORTCODE` and `MPESA_PASSKEY` are empty — that
  product is authorised by the OAuth bearer token alone. Transaction Status Query
  is unavailable for it (it is authenticated with shortcode + pass key).
- `MPESA_RESULT_URL` (and `MPESA_TIMEOUT_URL`) point at a publicly reachable
  route. They default to `MPESA_CALLBACK_URL`, and the callback view dispatches
  on body shape, so one public URL serves STK callbacks and status-query results
  alike. A dedicated route is also available at `/api/payments/mpesa/result/`.

## Reachability checks

- `https://api.printy.ke/api/payments/mpesa/callback/` is publicly reachable over HTTPS.
- TLS certificate is valid.
- Nginx and Gunicorn are serving the deployed backend at `api.printy.ke`.
- No firewall rule blocks Safaricom from reaching the callback URL.

## Deployed env checks

- `MPESA_ENV=production`
- `MPESA_CALLBACK_URL=https://api.printy.ke/api/payments/mpesa/callback/`
- `FRONTEND_URL=https://printy.ke`
- production API host is not pointing to localhost anywhere

## Phone number expectations

Frontend and backend should submit Kenyan MSISDNs in one of these accepted forms:
- `2547XXXXXXXX`
- `07XXXXXXXX`
- `7XXXXXXXX`

The backend normalizes to `2547XXXXXXXX` before Daraja submission.

## Expected payment lifecycle

Canonical frontend/backend payment states:
- Before STK push: `initiated`
- After STK accepted, before callback: `pending`
- After success callback with matching amount: `paid`
- After failure callback: `failed`
- After cancelled or timed-out flow: `cancelled`
- After amount mismatch or duplicate/manual-review conditions: `needs_review`

## Database expectations after STK push

Managed jobs:
- `JobPayment.payment_status` moves to `stk_push_sent` or `confirmation_pending`
- frontend card maps that to `pending`

After successful callback:
- `JobPayment.payment_status=confirmed`
- `JobPayment.reconciliation_status=confirmed`
- `ManagedJob.payment_status=confirmed`
- frontend card shows `paid`

After failed callback:
- `JobPayment.payment_status=failed`
- frontend card shows `failed`

After amount mismatch:
- `JobPayment.reconciliation_status=amount_mismatch`
- frontend card shows `needs_review`

Duplicate callback behavior:
- no double-processing
- no duplicate settlement/payment success

## Logs to inspect after a test payment

Backend application logs:
- Gunicorn journal output
- Django `payments` logger output

Look for:
- `Received billing M-Pesa callback checkout_request_id=... merchant_request_id=...`
- duplicate callback warnings only if Daraja retries
- no stack traces
- no localhost callback warnings in production

Nginx logs:
- access log entry for `POST /api/payments/mpesa/callback/`
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

## Recovering a payment whose callback was lost

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
