# Daraja Production Checklist

This checklist is for real Safaricom production validation. Do not mark any payment as paid unless the callback confirms it.

## Exact callback URL to register

`https://api.printy.ke/api/payments/mpesa/callback/`

## Portal checks

- The Daraja app is in the production environment, not sandbox.
- `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE`, and `MPESA_PASSKEY` all belong to the same production app.
- The shortcode/passkey pair matches the same production paybill or till configured in Daraja.
- The registered callback URL matches the exact deployed backend route above.

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
