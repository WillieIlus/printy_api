# API URL Audit

Current production domains:
- Frontend: `https://printy.ke`
- API: `https://api.printy.ke`

## Canonical runtime URLs

- Frontend API base: `NUXT_PUBLIC_API_BASE_URL=https://api.printy.ke/api`
- Backend API mount: `/api/`
- Public job tracking frontend route: `/track-job/{token}`
- Public job tracking API route: `/api/public/job/{token}/`
- Canonical M-Pesa callback route: `/api/payments/mpesa/callback/`

## Auth URLs

| Frontend route | Backend route |
| --- | --- |
| `/auth/login` | `/api/auth/token/` |
| `/auth/register` | `/api/auth/register/` |
| `/auth/confirm-email?key=...` | `/api/auth/confirm-email/` |
| `/auth/forgot-password` | `/api/auth/password-reset/` |
| `/auth/reset-password?key=...` | `/api/auth/password-reset/confirm/` |

## Public tracking URLs

| Purpose | Canonical URL |
| --- | --- |
| frontend share/admin/email link | `https://printy.ke/track-job/{token}` |
| backend token endpoint | `https://api.printy.ke/api/public/job/{token}/` |

Deprecated and no longer canonical:
- `/job/{token}` as a frontend link
- `/public/job/{token}` as a frontend link

## M-Pesa URLs

| Purpose | Canonical URL |
| --- | --- |
| Daraja STK callback (configured value) | `https://api.printy.ke/api/payments/mpesa/callback/` |
| Daraja STK callback (what Daraja is actually given) | `https://api.printy.ke/api/payments/mpesa/callback/<MPESA_CALLBACK_SECRET>/` |
| Transaction Status Query result | `/api/payments/mpesa/result/<secret>/` (`MPESA_RESULT_URL` defaults to the callback URL; the view dispatches on body shape) |
| Legacy callback (still served, same gate) | `/api/payments/mpesa-callback/<secret>/` |
| canonical STK initiation (quote payment) | `/api/payments/stk-push/` |
| cashier STK initiation (`managed_job_id` / `quote_id` / ad-hoc amount) | `/api/payments/mpesa/stk-push/` |
| managed-job STK initiation | `/api/managed-jobs/{id}/payments/mpesa/stk-push/` |
| managed-job payment query (local state only) | `/api/managed-jobs/{id}/payments/mpesa/query/` |
| needs_review list (super-admin) | `/api/admin/payments/needs-review/` |
| needs_review resolution (super-admin) | `/api/admin/payments/{id}/resolve-review/`, `/api/admin/mpesa-payments/{id}/resolve-review/` |
| lost-confirmation recovery (super-admin) | `/api/admin/payments/{id}/recover/` |

Deprecated callback paths (not routed):
- `/api/billing/mpesa/callback/`
- `/api/managed-jobs/payments/mpesa/callback/`

### Callback authentication (audit #1)

Every callback route (`/mpesa/callback/`, `/mpesa/result/`, legacy
`/mpesa-callback/`, each with or without the trailing secret segment) runs
`payments.callback_security.reject_unverified_callback` first:

1. **Secret:** the last path segment, or `?token=`, must equal
   `MPESA_CALLBACK_SECRET` (constant-time compare). The code appends the
   secret to every URL it hands Daraja. If the secret is required
   (`DEBUG=False`) but unset, every callback is refused.
2. **Source IP:** it must be in `MPESA_CALLBACK_ALLOWED_IPS`, or in the
   built-in Safaricom list when that is unset in production. The client IP
   comes from `common.client_ip.resolve_client_ip`, which reads
   `X-Forwarded-For` only from `MPESA_TRUSTED_PROXIES`.
3. **Persisted transaction:** an STK callback must reference a
   `CheckoutRequestID` we stored. A status result must reference a receipt we
   stored.

The bare routes without the secret segment still exist. They accept a
callback only with `?token=<secret>`, or in DEBUG with no secret configured.

### Response changes from the audit (frontend and ops must expect these)

| Endpoint | Change |
| --- | --- |
| all callback routes | **403** for a bad or missing secret or a non-allowlisted IP; **404** for an unknown `CheckoutRequestID`; **400** for a missing `CheckoutRequestID` or a non-integer `ResultCode`. Previously these always returned 200. |
| `/api/payments/mpesa/stk-push/` | the amount for `managed_job_id` / `quote_id` is computed server-side; a client-sent amount is ignored. **403** if the caller is not the job's client or cannot accept the quote. **409** if the job is already paid or has no confirmed price. **400** `artwork_payment_block_reason` while artwork is unapproved. **400** for `quote_request_id` ("pay its quote with quote_id"). |
| `/api/payments/stk-push/`, managed-job STK push | **400** `artwork_payment_block_reason` while artwork is unapproved. **400** while the payment is `needs_review` or already paid. |
| client-portal link payment `/api/client-portal/links/{token}/payment/` | **400** `artwork_payment_block_reason` before any acceptance. A due-date or price change must be acknowledged first. |
| `/api/admin/payments/{id}/resolve-review/` | new; **403** for anyone who is not a super-admin; **400** on validation (amount mismatch without `override_reason`, receipt reused). |
| `/api/admin/payments/{id}/recover/` | new (Prompt 7); super-admin only (**403** otherwise); asks Daraja STK Query now and applies the answer like a callback; **502** on a Daraja error with no state change; **404** unknown payment. |
| admin custody release / dispute resolve | previously **500 on PostgreSQL** on every call (`FOR UPDATE` on a nullable outer join); fixed in audit #10d. |

### `checkout_request_id` removed from responses (audit #1)

A `CheckoutRequestID` plus an open callback URL was enough to forge a
payment, so it is no longer returned where no caller needs it:

- `mpesa_payments` read serializer (`/api/payments/mpesa/stk-push/` 201,
  `/api/payments/mpesa/{id}/`, `/query/`, `/transactions/`): no
  `checkout_request_id` or `merchant_request_id`. The UI polls by payment id.
- signed-out client-portal link payment response: removed (both ids).
- client dashboard recent payments: removed.
- partner "send to client" payment payload: removed (the manager is not the
  payer).

It is **kept** in the authenticated payer's canonical STK and detail
responses (`/api/payments/stk-push/`, `/api/payments/{id}/`, managed-job STK
push), because `/api/managed-jobs/{id}/payments/mpesa/query/` takes it as
input. Forging with it is now blocked by the secret and IP gate.

## Production checks

- Frontend must not call `localhost` or `127.0.0.1`
- email links must point to `https://printy.ke`
- Daraja must be given `https://api.printy.ke/api/payments/mpesa/callback/<secret>/`
  (the code appends the secret automatically to `MPESA_CALLBACK_URL`)
