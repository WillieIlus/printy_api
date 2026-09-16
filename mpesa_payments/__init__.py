"""Printy M-Pesa (Safaricom Daraja) payments.

Standalone, drop-in app implementing the STK Push / Lipa na M-Pesa Online
flow exactly as described in docs/DARAJA_PRODUCTION_CHECKLIST.md.

Canonical callback:
    https://api.printy.ke/api/payments/mpesa/callback/

Payment lifecycle (canonical frontend/backend states):
    initiated  -> STK push requested by the client
    pending    -> Daraja accepted the push, awaiting callback
    paid       -> success callback with matching amount
    failed     -> failure callback
    cancelled  -> user cancelled (ResultCode 1032) or timed out
    needs_review -> amount mismatch, duplicate or manual-review condition
"""
