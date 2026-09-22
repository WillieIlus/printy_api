# Email Delivery (SMTP) — diagnosis, fix, verification

## The bug: "emails work in the backend console but never arrive"

### Symptom
- Emails appear in the server output (PowerShell / journald / `supervisorctl tail`)
  exactly as if they had been sent.
- Recipients never receive anything. No bounced message, no SMTP error logged.
- SMTP credentials (`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_HOST`) are
  configured, but are **ignored**.

### Root cause
Django's `EMAIL_BACKEND` decides *where* mail goes. Two backends matter here:

| Backend | What happens |
| --- | --- |
| `django.core.mail.backends.console.EmailBackend` | The message is written to the process **stdout**. Nothing is sent. |
| `django.core.mail.backends.smtp.EmailBackend` | The message is sent over SMTP using `EMAIL_HOST*` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD`. |

`config/settings.py` reads `EMAIL_BACKEND` from the environment and, if it is
missing, falls back to the **console** backend:

```
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
```

If the deployed `.env` never sets `EMAIL_BACKEND`, the console backend wins.
All SMTP-related settings become dead configuration: every `send_mail()` call
succeeds and "works" (it prints), `fail_silently=True` callers swallow errors,
and no email is ever delivered. That is exactly what happened:

- Local dev (`.env` with `DEBUG=true`, console backend) → mail shows up in the
  PowerShell terminal. ✅ *feels* like it works.
- Production server (same code, `EMAIL_BACKEND` unset) → mail prints to the
  gunicorn/supervisor logs. ❌ nobody receives it, and nothing is alerted.

### Fix (already applied)
1. `settings.py` now gates on `APP_ENV` (defaults to **production** when unset):
   `EMAIL_IS_LOCAL` is true only for `APP_ENV` in
   `{local, dev, development}` or when `DEBUG`/test settings are active.
2. A Django system check (`check_email_delivery_config`, tag `compatibility`)
   is registered. In a non-local environment it **fails startup** when:
   - `EMAIL_BACKEND` is the console backend (or fell back to it) — `printy.E001`
   - SMTP selected but `EMAIL_HOST_USER` empty/placeholder — `printy.E002`
   - SMTP selected but `EMAIL_HOST_PASSWORD` empty/placeholder — `printy.E003`
   - SMTP selected but `EMAIL_HOST` empty — `printy.E004`
   Locally the same check is only informational (`printy.W901`).
3. `python manage.py send_test_email --to you@example.com` sends a real message
   through the currently configured backend and exits non-zero on failure.

## Configuring real delivery

In the **deployed** `.env` (template: `.env.example`):

```dotenv
APP_ENV=production
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=hello@printy.ke
EMAIL_HOST_PASSWORD=an-app-password-not-the-login-password
DEFAULT_FROM_EMAIL=Printy <hello@printy.ke>
ADMIN_NOTIFY_EMAIL=ops@printy.ke
```

Notes:
- Gmail requires an **App Password** (Google account → Security → App passwords);
  the normal account password will raise `SMTPAuthenticationError`.
- Port `587` + `EMAIL_USE_TLS=true` (STARTTLS) is standard. For port `465`
  (implicit TLS) use `django.core.mail.backends.smtp.EmailBackend` with
  `EMAIL_USE_SSL=true` instead of `EMAIL_USE_TLS`.
- Change providers (`SendGrid`, `Mailgun`, `Postmark`, …) by changing
  `EMAIL_HOST`, `EMAIL_PORT` and the credentials.

## Verifying after every deploy

```bash
# 1. Config is valid and the guard is happy.
python manage.py check

# 2. Prove real delivery (prints backend, host, from/to; exits 1 on failure).
python manage.py send_test_email --to ops@printy.ke

# 3. Optional: prove the SMTP connection itself (no message sent).
python manage.py send_test_email --to ops@printy.ke --preflight
```

Then check the recipient inbox **and spam folder**. If the test email arrived
but production emails still don't, look at transactional paths separately
(functional emails such as signup/reset/quote notifications all use the same
backend, so the plumbing is now proven).

## Guard matrix

| `APP_ENV` | `EMAIL_BACKEND` | Result of `manage.py check` |
| --- | --- | --- |
| `local` / `DEBUG=true` | console | ✅ Info `printy.W901` (expected locally) |
| `production` / unset | console (or unset → fallback) | ❌ Error `printy.E001` — startup blocked |
| `production` / unset | smtp, empty/placeholder user | ❌ Error `printy.E002` |
| `production` / unset | smtp, empty/placeholder password | ❌ Error `printy.E003` |
| `production` / unset | smtp, empty `EMAIL_HOST` | ❌ Error `printy.E004` |
| `production` / unset | smtp, real credentials | ✅ Clean |

## Where emails are sent from

- Account flow (signup verification, password reset) via `django-allauth`.
- `accounts/signals.py` — new-signup alert to `ADMIN_NOTIFY_EMAIL`.
- `notifications/services.py` — quote/workflow notifications (uses
  `fail_silently=True`, so delivery failures were especially silent; the
  `printy.E00x` checks now catch misconfiguration before this matters).
- `quotes/services_workflow.py` — workflow emails.

All of them funnel through `django.core.mail`, so the **single lever** is
`EMAIL_BACKEND`. There is no separate per-feature mail setting.

## Regression coverage

`tests/test_email_delivery.py` runs on every test pass and guarantees:

- `_evaluate_email_delivery` flags console-in-production as `printy.E001`,
  placeholder/missing SMTP credentials as errors, and stays clean with real
  credentials.
- The check is actually registered and executed by Django's system-check runner.
- The console backend prints but leaves `django.core.mail.outbox` empty (the
  original bug), while a real backend queues a deliverable message.
- The signup flow emits a deliverable activation email (outbox) with the
  production link.
- `send_test_email` fails fast when the command would only print, and sends a
  real message under a real backend.