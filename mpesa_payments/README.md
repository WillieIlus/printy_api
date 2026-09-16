# Printy M-Pesa (Safaricom Daraja) payments

A standalone drop-in app implementing the **STK Push / Lipa na M-Pesa Online** flow,
built to match `docs/DARAJA_PRODUCTION_CHECKLIST.md` and `docs/env_vars.md`.

Canonical callback: `https://api.printy.ke/api/payments/mpesa/callback/`

## Files

| File | Purpose |
|---|---|
| `models.py` | `MpesaPayment` state machine, token cache, callback log |
| `services.py` | Daraja client, MSISDN normalization, STK push, query, idempotent callback |
| `signals.py` | `mpesa_payment_confirmed` / `mpesa_payment_failed` |
| `serializers.py` | Input + canonical output serializers |
| `views.py` | STK push, callback, detail, query, transactions |
| `urls.py` | Routes under `/api/payments/mpesa/` |
| `admin.py` | Read-mostly admin with query/review actions |
| `settings_addition.py` | The settings block to copy in |
| `management/commands/mpesa_reconcile.py` | Sweeps payments stuck in `pending` |
| `tests.py` | Covers the checklist's critical rules |

## Install

1. Copy `mpesa_payments/` into the repo root, beside `quotes/` and `api/`.
2. Add to `INSTALLED_APPS`:
   ```python
   "mpesa_payments",
   ```
3. Copy the block from `settings_addition.py` into `settings.py`.
4. Add the throttle scope:
   ```python
   "DEFAULT_THROTTLE_RATES": {"mpesa_stk_push": "10/min", ...}
   ```
5. Add to `api/urls.py`:
   ```python
   path("payments/mpesa/", include("mpesa_payments.urls")),
   ```
6. Migrate: `python manage.py migrate`
7. Test: `python manage.py test mpesa_payments`
8. Register the callback URL in the Daraja portal exactly as above.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/payments/mpesa/stk-push/` | Yes | Send the STK prompt |
| POST | `/api/payments/mpesa/callback/` | No (Safaricom) | Result webhook |
| GET | `/api/payments/mpesa/{id}/` | Yes | Poll one payment |
| POST | `/api/payments/mpesa/{id}/query/` | Yes | Ask Daraja directly |
| GET | `/api/payments/mpesa/transactions/` | Yes | My payment history |

## Request

```json
POST /api/payments/mpesa/stk-push/
{
  "phone_number": "0712345678",
  "amount": "2500.00",
  "managed_job_id": 1041,
  "account_reference": "PTY-1041",
  "description": "Aurora Heights brochures"
}
```

Phone accepts `2547XXXXXXXX`, `07XXXXXXXX`, `7XXXXXXXX` or `+254...` — normalized
server-side to `2547XXXXXXXX`.

## Response

```json
{
  "id": 12,
  "status": "pending",
  "is_paid": false,
  "is_terminal": false,
  "amount": "2500.00",
  "currency": "KES",
  "phone_number": "254712345678",
  "checkout_request_id": "ws_CO_...",
  "customer_message": "Success. Request accepted for processing",
  "reconciliation_status": "pending"
}
```

`status: "pending"` means **Daraja accepted the request**. It does not mean the
customer paid. Only the callback can produce `paid`.

## State machine

```
initiated ── push accepted ──> pending
   │                              │
   └── push rejected ──> failed   ├─ success + amount ok ──> paid
                                  ├─ success + mismatch  ──> needs_review
                                  ├─ ResultCode 1032/1037 ─> cancelled
                                  └─ other failure        ─> failed
```

`reconciliation_status` is a separate axis (`pending` / `confirmed` /
`amount_mismatch` / `duplicate` / `manual_review`) because money can arrive at
the wrong amount and still needs auditing.

## Guarantees

- **Never paid from initiation.** Only a success callback with a receipt number sets `paid`.
- **No double-processing.** `checkout_request_id` is unique, the row is locked with
  `select_for_update`, and terminal states are immutable. Replays are logged with `duplicate=True`.
- **Amount mismatches never become `paid`.** They go to `needs_review`.
- **Callback always returns 200** so Safaricom stops retrying a body we already handled.
- **Query can't invent success.** `query_stk_status` only moves a payment *out* of pending;
  a bare query success becomes `needs_review` until the callback's receipt arrives.
- **Config guards.** Production rejects a missing, localhost or non-HTTPS callback URL.

## Releasing custody on confirmation

This app deliberately knows nothing about settlement. Listen for the signal:

```python
from django.dispatch import receiver
from mpesa_payments.signals import mpesa_payment_confirmed

@receiver(mpesa_payment_confirmed)
def release_printy_custody(sender, payment, **kwargs):
    job = payment.payable          # ManagedJob / Quote / QuoteRequest
    if job is None:
        return
    # ... existing custody release / job confirmation logic
```

`mpesa_payment_failed` fires once after a failure or cancellation.

## Reconciliation

Daraja occasionally drops a callback. Run this from cron or celery-beat:

```bash
*/10 * * * * python manage.py mpesa_reconcile
```

It queries Daraja for payments pending longer than 10 minutes and cancels any
older than 24 hours so no card shows a permanent spinner.

## Environment

```env
MPESA_ENV=production
MPESA_CONSUMER_KEY=...
MPESA_CONSUMER_SECRET=...
MPESA_SHORTCODE=...
MPESA_PASSKEY=...
MPESA_CALLBACK_URL=https://api.printy.ke/api/payments/mpesa/callback/
MPESA_TIMEOUT_SECONDS=30
MPESA_ACCOUNT_REFERENCE_DEFAULT=PRINTY
MPESA_TRANSACTION_DESC_DEFAULT=Printy payment
```

`MPESA_BASE_URL` is optional — derived from `MPESA_ENV` when unset.

## Testing

```bash
python manage.py test mpesa_payments
```

The suite pins every rule in the production checklist: no paid-from-initiation,
duplicate safety, amount-mismatch routing, terminal-state immutability, phone
normalization, and the production config guards.
