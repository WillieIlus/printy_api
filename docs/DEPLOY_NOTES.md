# Deploy notes: security audit remediation (Prompts 0–6)

Read this once before the first production deploy that includes the audit
branch. It lists what the deploy itself must do, in order. The general
procedure is still `docs/PRODUCTION_DEPLOYMENT_RUNBOOK.md` and
`scripts/deploy.sh`. Variable details are in `docs/env_vars.md`, and the
payment go-live steps are in `docs/DARAJA_PRODUCTION_CHECKLIST.md`.

Nothing here has been run against the real server. The audit sandbox had no
access to the production host, nginx, Redis, real SMTP or Daraja. Each step
says what to check.

## 1. New and stricter environment variables

The app now **refuses to start** with `DEBUG=False` unless all of these are
set:

| Variable | Value | Why |
| --- | --- | --- |
| `MPESA_CALLBACK_SECRET` | `python -c "import secrets; print(secrets.token_urlsafe(48))"` | Daraja callbacks are unsigned; this secret, carried in the callback URL, is their authentication. It must be ≥ 32 URL-safe characters. |
| `ALLOWED_HOSTS` | e.g. `api.printy.ke` | There is no default any more; `*` and local-only lists are refused. |
| `REDIS_URL` | e.g. `redis://127.0.0.1:6379/1` | Throttles and the Daraja token cache must be shared across gunicorn workers. Install and start Redis first (`sudo apt install redis-server`, or a managed instance). |

Also recommended:
- `MPESA_CALLBACK_ALLOWED_IPS`: pin it after verifying the real Safaricom
  source IPs (see section 6).
- `MPESA_TRUSTED_PROXIES`: the default `127.0.0.1,::1` is correct for nginx
  on the same host. Add the load balancer's address if there is one.
- Do **not** set `NUM_PROXIES`. It is ignored, and `manage.py check` warns
  `printy.W903` if it is set.

Do not leave `MPESA_FORCE_STUB` set in production. Stub mode requires
`DEBUG=true` and is refused otherwise.

Check before restarting:

```bash
python manage.py check --deploy   # must show no printy.E0xx errors
```

## 2. Python dependencies

`requirements.txt` gained new packages:
- `google-auth` verifies Google ID tokens locally. Without it, Google
  sign-in fails.
- `redis` is the client for Django's RedisCache. Without it, startup fails
  once `REDIS_URL` is set.

`psycopg2-binary` is unchanged.

```bash
pip install -r requirements.txt
pip check
```

`scripts/deploy.sh` already runs both commands.

## 3. Migrations

Four new migrations, all additive or metadata-only:

| Migration | What it does |
| --- | --- |
| `mpesa_payments/0002_checkout_request_id_nullable` | `checkout_request_id` becomes nullable; existing `""` values become NULL. This fixes the IntegrityError on a second failed STK initiation. Its reverse refuses to run if more than one NULL row exists. |
| `payments/0003_payment_needs_review_status` | adds the `needs_review` choice to `Payment.status` |
| `pricing/0009_platform_fee_policy_tier_parameters` | adds the tier parameter fields to `PlatformFeePolicy`, with defaults equal to the previous hard-coded tiers |
| `pricing/0010_platform_fee_policy_deprecated_fields` | help_text / `editable` changes only; no SQL |

```bash
python manage.py migrate --plan   # review
python manage.py migrate --noinput
```

Back up the database first. CI now runs the whole suite on PostgreSQL 16, but
the migrations have not been run against a copy of the production data.

## 4. Data reports to run on production (read-only)

Both commands only **report**; neither modifies data. Run them after the
migrations, and keep the output.

```bash
python manage.py report_duplicate_emails
python manage.py report_financial_split_anomalies
```

- `report_duplicate_emails` lists accounts whose emails differ only by
  letter case.
  - Google sign-in refuses such accounts with 409 `duplicate_email_accounts`
    rather than guess which one is meant.
  - Accounts are **not** merged automatically. Merge them by hand, with the
    owners' agreement.
- `report_financial_split_anomalies` lists stored `QuoteFinancialSplit` rows
  that break the current rules:
  - a negative Printy fee;
  - payouts above the client total;
  - a markup below the floor.

  Stored splits are **not** changed automatically. Review each one; accepted
  quotes keep their stored numbers.

## 5. Mask the callback secret in logs

The secret is part of the URL Daraja calls
(`/api/payments/mpesa/callback/<secret>/`), so a plain access log records it.
Anyone who reads that log and can send traffic from an allowlisted address
could then forge callbacks.

nginx example. It is untested here: nginx was not available in the audit
sandbox. Validate it with `sudo nginx -t`.

```nginx
# http { } context
map $request_uri $printy_log_uri {
    "~^(?<printy_pfx>/api/payments/(?:mpesa/(?:callback|result)|mpesa-callback)/)[^/?]+(?<printy_rest>.*)$" "${printy_pfx}[secret]${printy_rest}";
    "~^(?<printy_pfx2>[^?]*\?(?:.*&)?token=)[^&]*(?<printy_rest2>.*)$" "${printy_pfx2}[secret]${printy_rest2}";
    default $request_uri;
}
log_format printy_masked '$remote_addr - $remote_user [$time_local] '
                         '"$request_method $printy_log_uri $server_protocol" '
                         '$status $body_bytes_sent "$http_referer" "$http_user_agent"';

# server { } block for api.printy.ke
access_log /var/log/nginx/printy_api.access.log printy_masked;
```

Also:
- **gunicorn:** if `--access-logfile` is enabled, it logs the path too.
  Disable it (nginx already logs requests), or put the same masking in a
  custom `access_log_format`.
- **Django:** the `payments` logger never logs the secret; it logs the
  route, the source IP and the reason for a rejection.
- **Old logs:** restrict access to them, and rotate the secret if logs from
  before masking may have leaked (section 7).

## 6. Before taking real payments

Follow `docs/DARAJA_PRODUCTION_CHECKLIST.md`, in particular:

1. **Pin the callback IPs.** The built-in Safaricom allowlist in
   `payments/callback_security.py` was **not** verified against Safaricom's
   current list.
   - Log the real source IPs of the first callbacks: nginx logs, and
     `Rejected M-Pesa callback ... ip=` lines.
   - Compare them with Safaricom's published list.
   - Set `MPESA_CALLBACK_ALLOWED_IPS`.
2. **Do one small real payment end to end** and check:
   - `Payment` is `paid` and has a receipt;
   - the `ManagedJob` reaches `payment_confirmed`;
   - the access log shows the masked path.
3. **Run the go-live reconciliation:** compare Printy's paid payments with
   the Daraja statement daily for the first two weeks.
4. **Schedule lost-confirmation recovery (audit #1c, Prompt 7):** add a
   cron entry that runs `python manage.py mpesa_recover_payments` every
   5 minutes (see the checklist section "Recovering a canonical payment
   whose callback was lost"). It needs no new variables; the optional
   `MPESA_RECOVERY_*` rate limits are in `docs/env_vars.md`.

## 7. Rotating `MPESA_CALLBACK_SECRET`

The secret is part of the callback URL, so **rotating it changes the URL
Daraja calls**. Daraja keeps calling the URL it was given when the push was
made. So any prompt sent before the rotation gets its callback refused (403)
afterwards, even when the customer paid.

Procedure:

1. Pick a quiet period. Note the time.
2. Set the new secret and restart gunicorn.
3. After the in-flight window (`MPESA_STK_IN_FLIGHT_SECONDS`, default 180 s),
   list the prompts sent before the rotation that never got a callback:
   ```python
   from payments.models import MpesaSTKRequest
   MpesaSTKRequest.objects.filter(requested_at__lt=ROTATED_AT, callback_received_at__isnull=True)
   ```
   Also check cashier `MpesaPayment` rows still `pending`.
4. Reconcile each one against the Daraja statement:
   - Cashier payments: run `python manage.py mpesa_reconcile`, then
     `mpesa_verify_receipt`.
   - Canonical payments: there is no automatic query. Confirm from the
     statement and resolve through the super-admin resolution endpoint, or
     with engineering help if the payment is not in `needs_review`.
5. If you registered a URL by hand in the Daraja portal, update it to the new
   secret.

## 8. Frontend follow-ups (printy_ui, not edited by the audit)

The API changed in ways the UI must handle:

- **Payments**
  - Handle `400` with `artwork_payment_block_reason` on every payment
    path, including the `quote_id` STK push and the client-link payment.
    Show the reason; do not retry.
  - Handle `403` (not the payer) and `409` (already paid / no confirmed
    price) on `/api/payments/mpesa/stk-push/`.
  - Stop reading `checkout_request_id` / `merchant_request_id` from the
    cashier, client-link, client-dashboard and send-to-client responses. Poll
    by payment id.
  - Show `needs_review` and `processing` as "awaiting confirmation", never
    as paid.
  - A due-date or price change must be acknowledged before payment.
- **Super-admin review UI (new):**
  - `GET /api/admin/payments/needs-review/`;
  - `POST .../resolve-review/` with `resolution`, `reason`, and for
    `confirm_paid` also `receipt_number`, `amount` and optional
    `override_reason`.
  - `POST /api/admin/payments/{id}/recover/` ("check with M-Pesa now"),
    optional `reason`; show `outcome` and `payment_status`; `502` means
    Daraja could not be reached and nothing changed.
- **Expired payments (Prompt 7):** `Payment.status` can now be `expired`
  ("timed out"). It is not final: offer "pay again" (same payment), and
  keep refreshing it, because a late M-Pesa success can still turn it into
  `paid`.
- **Auth**
  - Handle `409 duplicate_email_accounts` ("contact support"), `403
    account_inactive`, `400 google_email_unverified` / `invalid_google_token`,
    and `503 google_oauth_not_configured`.
  - Credential endpoints can return `429` with `Retry-After`.
- **Printer signup**
  - `/api/auth/register/production/` creates a client-role applicant with no
    shop (`signup_intent: "printer_applicant"`); `role=production` returns
    400.
  - After login, route the user to "create your shop" (`POST /api/shops/`).
- **Visibility**
  - `/api/public/shops/*` and `/api/shops/nearby/` are for verified managers
    only. Anonymous visitors, clients and unverified managers get 401/403.
    Do not call these from public pages.
  - Client quote lists return `null` cost and margin fields and a masked shop
    label.
- **Client portal**
  - The link artwork upload must be multipart with a `file` field.
  - The OTP request can return `503` when no SMS provider is configured,
    which is the case in production today.
- **Uploads:** PDF/EPS previews are downloads now, so do not embed them.
  Upload errors say when the contents do not match the extension, and SVG is
  refused.
- **Pricing:** send-to-client and pricing previews can return `400` "Manager
  markup is below the minimum of KES X". `suggested_selling_range.min` is the
  minimum sendable price.
- **Profile:** phone edits return `400` for invalid or non-Kenyan numbers.

## 9. Known gaps (not fixed, recorded in results.txt)

- Lost-confirmation recovery (Prompt 7) uses Daraja STK Query, which
  returns no receipt or amount, so a recovered success lands in
  `needs_review` and a super-admin confirms it against the statement.
  Fully automatic settlement would need the Transaction Status API with
  initiator credentials.
- No real SMS/WhatsApp provider: client-link OTPs return 503 in production.
- Partner markup cap: `maximum_manager_markup_multiple` (2.00x) is flat
  across tiers. Whether it should be per-tier is a product decision.
