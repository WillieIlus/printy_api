# Production Smoke Test Checklist

## Public

- homepage loads
- homepage calculator loads config
- calculator preview returns a KES estimate from the backend
- browser network tab shows no localhost API calls
- `/for-shops` loads
- `/track-job/{token}` loads for a valid token
- invalid tracking token shows a clean error state

## Auth

- register a new account
- activation email arrives
- activation link points to `https://printy.ke`
- activation confirms the account
- login works
- forgot-password email arrives
- reset link points to `https://printy.ke`
- password reset works
- role redirects work after login

## Client

- dashboard loads
- quote/job list loads
- quote thread loads
- upload UI shows visible file metadata
- no invisible text or unreadable contrast in current theme

## Shop

- incoming jobs page loads
- shop job detail page loads
- accept, reject, and status controls only appear where backend supports them
- client-only UI does not leak into the shop view

## Payments

- STK push initiates
- payment card shows a pending state after STK initiation and before callback
- callback hits `POST /api/payments/mpesa/callback/`
- status changes to paid, failed, or needs_review correctly
- duplicate callback does not double-process
- amount mismatch maps to needs_review
- frontend payment card reflects backend truth

## Ops

- Gunicorn logs are clean for key routes
- nginx logs are clean for key routes
- no 500s on homepage, auth, dashboard, tracking, and payment endpoints
- CORS is clean from the production frontend
- media URLs load
- activation and reset emails are not malformed or obviously spam-like
