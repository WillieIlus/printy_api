"""Authenticity gate for inbound Daraja callbacks (audit #1).

Daraja does not sign its callbacks, so a callback is only trusted when ALL of
these hold:

1. **Secret URL.** The callback URL registered with Daraja carries
   ``MPESA_CALLBACK_SECRET`` as its final path segment (or ``?token=``). It is
   compared in constant time with :func:`hmac.compare_digest`. When the secret
   is required (production, ``DEBUG=False``) and not configured, every callback
   is refused — the gate fails closed, it never falls back to "open".
2. **Source IP.** The peer must be in ``MPESA_CALLBACK_ALLOWED_IPS`` (or, in
   production when that is unset, the published Safaricom ranges below). The
   client IP is ``REMOTE_ADDR`` unless the direct peer is a configured trusted
   proxy (``MPESA_TRUSTED_PROXIES``); only then is ``X-Forwarded-For`` walked
   right-to-left, skipping trusted hops. A raw, client-supplied
   ``X-Forwarded-For`` is therefore never believed.
3. **Persisted transaction.** Enforced by the callers: the body must reference
   a CheckoutRequestID we stored when Daraja accepted our own STK push.

A refused callback gets HTTP 403 and causes no state change.
"""

from __future__ import annotations

import hmac
import logging
from urllib.parse import quote

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

logger = logging.getLogger("payments")

# Safaricom's published Daraja callback source addresses (Daraja "Going Live"
# whitelisting list). Enforced in production when MPESA_CALLBACK_ALLOWED_IPS is
# unset. Operators should keep MPESA_CALLBACK_ALLOWED_IPS in sync with the
# current list Safaricom publishes; setting it overrides these defaults.
SAFARICOM_CALLBACK_IPS: tuple[str, ...] = (
    "196.201.214.200",
    "196.201.214.206",
    "196.201.213.114",
    "196.201.214.207",
    "196.201.214.208",
    "196.201.213.44",
    "196.201.212.127",
    "196.201.212.138",
    "196.201.212.129",
    "196.201.212.136",
    "196.201.212.74",
    "196.201.212.69",
)

TOKEN_QUERY_PARAM = "token"


from common.client_ip import as_list as _as_list  # noqa: E402
from common.client_ip import in_any as _in_any  # noqa: E402
from common.client_ip import networks as _networks  # noqa: E402
from common.client_ip import parse_ip as _parse_ip  # noqa: E402
from common.client_ip import resolve_client_ip  # noqa: E402,F401  (re-exported)


def callback_secret() -> str:
    return str(getattr(settings, "MPESA_CALLBACK_SECRET", "") or "").strip()


def callback_secret_required() -> bool:
    return bool(getattr(settings, "MPESA_CALLBACK_SECRET_REQUIRED", False))


def allowed_callback_networks() -> list:
    """The enforced allowlist, or [] when no IP restriction applies."""
    configured = _as_list(getattr(settings, "MPESA_CALLBACK_ALLOWED_IPS", None))
    if configured:
        return _networks(configured)
    if callback_secret_required():
        return _networks(SAFARICOM_CALLBACK_IPS)
    return []


def _presented_token(request, path_token) -> str:
    if path_token:
        return str(path_token)
    return str(request.query_params.get(TOKEN_QUERY_PARAM, "") if hasattr(request, "query_params") else "")


def signed_callback_url(url: str) -> str:
    """Append the callback secret as the final path segment of ``url``.

    Lets MPESA_CALLBACK_URL stay the documented canonical value while every
    URL we hand Daraja (CallBackURL, ResultURL, QueueTimeOutURL) carries the
    secret. Idempotent: a URL that already carries it is returned unchanged.
    """
    secret = callback_secret()
    if not url or not secret:
        return url
    if secret in url:
        return url
    base, sep, query = url.partition("?")
    if not base.endswith("/"):
        base += "/"
    signed = f"{base}{quote(secret, safe='')}/"
    return f"{signed}{sep}{query}" if sep else signed


def reject_unverified_callback(request, path_token: str | None = None, *, route: str = "") -> Response | None:
    """Return a 403 response when the callback fails authentication, else None."""
    client_ip = resolve_client_ip(request)

    networks = allowed_callback_networks()
    if networks and not _in_any(_parse_ip(client_ip), networks):
        logger.warning(
            "Rejected M-Pesa callback from non-allowlisted ip=%s route=%s remote_addr=%s",
            client_ip, route, request.META.get("REMOTE_ADDR"),
        )
        return Response({"ResultCode": 1, "ResultDesc": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

    secret = callback_secret()
    if secret:
        presented = _presented_token(request, path_token)
        if not presented or not hmac.compare_digest(presented.encode(), secret.encode()):
            logger.warning(
                "Rejected M-Pesa callback with %s secret ip=%s route=%s",
                "missing" if not presented else "invalid", client_ip, route,
            )
            return Response({"ResultCode": 1, "ResultDesc": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
    elif callback_secret_required():
        # Misconfigured production: refuse rather than accept unauthenticated.
        logger.error(
            "Rejected M-Pesa callback: MPESA_CALLBACK_SECRET is required but not configured ip=%s route=%s",
            client_ip, route,
        )
        return Response({"ResultCode": 1, "ResultDesc": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
    return None
