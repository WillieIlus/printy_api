"""Throttles for credential and account-creation endpoints (audit #4).

Keys are the real client IP, resolved by ``payments.callback_security.
resolve_client_ip`` (now ``common.client_ip``): ``X-Forwarded-For`` is only honoured when the direct peer
is one of our trusted proxies (``MPESA_TRUSTED_PROXIES``). DRF's stock
``get_ident`` uses the raw header when ``NUM_PROXIES`` is unset, so a client
could pick a fresh throttle bucket per request just by sending a new
``X-Forwarded-For`` value.
"""
from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle


from common.throttling import TrustedIdentMixin


def trusted_client_ident(request) -> str:
    from common.client_ip import client_ident

    return client_ident(request)


class AuthScopedRateThrottle(TrustedIdentMixin, ScopedRateThrottle):
    """``ScopedRateThrottle`` keyed by the trusted client IP.

    Rates are read from settings at request time (not frozen at import), so
    ``REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`` is the single source of truth.
    """

    def get_rate(self):
        if not getattr(self, "scope", None):
            return None
        try:
            return api_settings.DEFAULT_THROTTLE_RATES[self.scope]
        except KeyError:
            # Fail loudly: a missing rate must never silently disable a throttle.
            raise ImproperlyConfigured(f"No throttle rate set for scope {self.scope!r}.")

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": trusted_client_ident(request)}


AUTH_THROTTLE_CLASSES = [AuthScopedRateThrottle]
