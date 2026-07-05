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
| Daraja callback | `https://api.printy.ke/api/payments/mpesa/callback/` |
| managed-job STK initiation | `/api/managed-jobs/{id}/payments/mpesa/stk-push/` |
| managed-job payment query | `/api/managed-jobs/{id}/payments/mpesa/query/` |

Deprecated callback paths:
- `/api/billing/mpesa/callback/`
- `/api/managed-jobs/payments/mpesa/callback/`

## Production checks

- Frontend must not call `localhost` or `127.0.0.1`
- email links must point to `https://printy.ke`
- Daraja must target `https://api.printy.ke/api/payments/mpesa/callback/`
