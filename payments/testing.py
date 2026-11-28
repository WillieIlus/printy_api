"""Helpers for tests that post Daraja callbacks (audit #10a).

The test settings run the callback gate the way production does: a callback
must carry MPESA_CALLBACK_SECRET (as the final path segment) and come from a
Safaricom address. Tests that exercise callback *routing* rather than the gate
itself post through these helpers, so they take the same path a live Daraja
callback takes. The gate on its own is covered by
mpesa_payments/test_audit_callback_and_amounts.py.
"""
from payments.callback_security import SAFARICOM_CALLBACK_IPS, signed_callback_url

# A published Safaricom callback source address, used as the test client's peer.
DARAJA_TEST_PEER_IP = SAFARICOM_CALLBACK_IPS[0]


def daraja_callback_path(path: str) -> str:
    """``path`` with the configured callback secret appended, as Daraja calls it."""
    return signed_callback_url(path)


__all__ = ["DARAJA_TEST_PEER_IP", "daraja_callback_path", "signed_callback_url"]
