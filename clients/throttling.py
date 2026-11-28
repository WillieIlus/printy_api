"""Throttles for the public client-link surface (law 10.6)."""
from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle

from common.throttling import TrustedIdentMixin


def _client_key(request) -> str:
    # Audit #4: never key on the raw X-Forwarded-For header (a client could pick
    # a fresh bucket per request). Trust it only from our own proxies.
    from accounts.throttling import trusted_client_ident

    return trusted_client_ident(request)


class ClientLinkViewThrottle(TrustedIdentMixin, SimpleRateThrottle):
    """Rate-limit how often a signed-out client can open a link."""

    scope = "client_link_view"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _client_key(request)}


class ClientOtpRequestThrottle(TrustedIdentMixin, SimpleRateThrottle):
    """Rate-limit OTP resends per client IP."""

    scope = "client_otp_request"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _client_key(request)}


class ClientOtpVerifyThrottle(TrustedIdentMixin, SimpleRateThrottle):
    """Rate-limit OTP attempts per client IP."""

    scope = "client_otp_verify"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _client_key(request)}
