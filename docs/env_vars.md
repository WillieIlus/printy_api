# Environment Variables — printy_api

All required and optional environment variables. Use `.env` or your deployment config.

---

## Core

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `DJANGO_SECRET_KEY` | Yes (prod) | `django-insecure-dev-key-change-in-production` | Secret for signing; **must change in production** |
| `DEBUG` | No | `true` | `true`/`1`/`yes` for debug mode |
| `ALLOWED_HOSTS` | No | `localhost,127.0.0.1,printy.ke,www.printy.ke,...` | Comma-separated hosts |

---

## Database

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `DB_ENGINE` | No | `sqlite` | `sqlite` or `mysql` |
| `DB_NAME` | If MySQL | `printshop` | Database name |
| `DB_USER` | If MySQL | `printshop_user` | Database user |
| `DB_PASSWORD` | If MySQL | (empty) | Database password |
| `DB_HOST` | If MySQL | `127.0.0.1` | Database host |
| `DB_PORT` | If MySQL | `3306` | Database port |

---

## CORS & Frontend

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `FRONTEND_URL` | No | `https://printy.ke` | Frontend base URL for email links, redirects |
| `CORS_ALLOWED_ORIGINS` | No | (see settings) | Override in settings if needed; defaults include printy.ke, localhost:3000, localhost:5173 |

**Configured origins (in settings):**
- `http://localhost:3000`
- `http://127.0.0.1:3000`
- `http://localhost:5173` (Vite dev)
- `https://printyke.netlify.app`
- `https://printy.ke`
- `https://www.printy.ke`
- `https://willieilus.pythonanywhere.com`

---

## JWT (SimpleJWT)

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| (uses `DJANGO_SECRET_KEY`) | — | — | JWT signing uses `SECRET_KEY` |
| Access token lifetime | — | 15 min | In settings: `SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]` |
| Refresh token lifetime | — | 30 days | In settings: `SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]` |

No extra env vars for JWT; config is in `config/settings.py`.

---

## Email

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `DEFAULT_FROM_EMAIL` | No | `noreply@printy.ke` | From address for emails |
| `EMAIL_BACKEND` | — | `console` | Override for SMTP in production |

---

## OAuth (django-allauth)

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `GOOGLE_CLIENT_ID` | If Google | (empty) | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | If Google | (empty) | Google OAuth client secret |
| `GITHUB_CLIENT_ID` | If GitHub | (empty) | GitHub OAuth client ID |
| `GITHUB_CLIENT_SECRET` | If GitHub | (empty) | GitHub OAuth client secret |

---

## M-Pesa (Subscription STK Push)

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `MPESA_BASE_URL` | No | `https://sandbox.safaricom.co.ke` | Daraja API base URL (sandbox or production) |
| `MPESA_CONSUMER_KEY` | Yes (when live) | (empty) | Daraja API consumer key |
| `MPESA_CONSUMER_SECRET` | Yes (when live) | (empty) | Daraja API consumer secret |
| `MPESA_SHORTCODE` | Yes (when live) | (empty) | Paybill or till number |
| `MPESA_PASSKEY` | Yes (when live) | (empty) | Lipa Na M-Pesa passkey |
| `MPESA_STK_CALLBACK_URL` | Yes (when live) | `https://printy.ke/api/payments/mpesa/callback/` | STK push callback URL (must be HTTPS, publicly reachable) |
| `MPESA_INITIATOR_NAME` | If B2C | (empty) | B2C initiator name |
| `MPESA_SECURITY_CREDENTIAL` | If B2C | (empty) | B2C security credential |
| `MPESA_TIMEOUT_URL` | No | `https://printy.ke/api/mpesa/timeout/` | M-Pesa timeout callback URL |
| `MPESA_RESULT_URL` | No | `https://printy.ke/api/mpesa/result/` | M-Pesa result callback URL |

**Note:** Callback URLs must be HTTPS and publicly reachable. Use ngrok for local testing.

---

## Subscription (placeholders)

| Variable | Required | Default | Description |
|----------|----------|---------|--------------|
| `FREE_TRIAL_DAYS` | — | 14 | In settings |
| `DEFAULT_SUBSCRIPTION_PLAN` | — | `STARTER` | In settings |

---

## Example `.env`

```env
# Core
DJANGO_SECRET_KEY=your-secret-key-here
DEBUG=false
ALLOWED_HOSTS=printy.ke,www.printy.ke,api.printy.ke

# Database (MySQL)
DB_ENGINE=mysql
DB_NAME=printy
DB_USER=printy_user
DB_PASSWORD=secure-password
DB_HOST=127.0.0.1

# Frontend
FRONTEND_URL=https://printy.ke

# M-Pesa (placeholders)
MPESA_CONSUMER_KEY=
MPESA_CONSUMER_SECRET=
MPESA_SHORTCODE=
MPESA_PASSKEY=
MPESA_STK_CALLBACK_URL=https://printy.ke/api/payments/mpesa/callback/
```
