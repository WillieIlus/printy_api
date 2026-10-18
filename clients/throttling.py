"""Throttles for the public client-link surface (law 10.6)."""
from __future__ import annotations

from rest_framework.throttling import SimpleRateThrottle


def _client_key(request) -> str:
    ident = request.META.get("HTTP_X_FORWARDED_FOR")
    if ident:
        return f"ip:{ident.split(',')[0].strip()}"
    remote = request.META.get("REMOTE_ADDR")
    return f"ip:{remote}" if remote else "ip:unknown"


class ClientLinkViewThrottle(SimpleRateThrottle):
    """Rate-limit how often a signed-out client can open a link."""

    scope = "client_link_view"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _client_key(request)}


class ClientOtpRequestThrottle(SimpleRateThrottle):
    """Rate-limit OTP resends per client IP."""

    scope = "client_otp_request"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _client_key(request)}


class ClientOtpVerifyThrottle(SimpleRateThrottle):
    """Rate-limit OTP attempts per client IP."""

    scope = "client_otp_verify"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": _client_key(request)}
