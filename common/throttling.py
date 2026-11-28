"""DRF throttles keyed by the trusted client IP (audit #10b).

Every throttle in the project must derive from :class:`TrustedIdentMixin` so
``get_ident`` goes through :func:`common.client_ip.resolve_client_ip`. DRF's
stock ``get_ident`` trusts the raw ``X-Forwarded-For`` header whenever
``NUM_PROXIES`` is unset, so a client could rotate that header to get a fresh
bucket on every request. ``tests/test_audit_config_proxy_trust.py`` asserts
that no throttle in use bypasses the mixin.

The counters live in Django's default cache. In production that must be the
shared Redis cache (``REDIS_URL``); a per-process LocMemCache would give every
worker its own counters and multiply each limit by the worker count.
"""
from __future__ import annotations

from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle


class TrustedIdentMixin:
    def get_rate(self):
        # DRF freezes DEFAULT_THROTTLE_RATES into SimpleRateThrottle.THROTTLE_RATES
        # at import. Read the live setting instead (REST_FRAMEWORK stays the
        # single source of truth and override_settings works), unless a class
        # deliberately set its own THROTTLE_RATES.
        from django.core.exceptions import ImproperlyConfigured
        from rest_framework.settings import api_settings
        from rest_framework.throttling import SimpleRateThrottle

        if not getattr(self, "scope", None):
            raise ImproperlyConfigured(f"You must set either `.scope` or `.rate` for '{self.__class__.__name__}' throttle")
        rates = self.THROTTLE_RATES
        if rates is SimpleRateThrottle.THROTTLE_RATES:
            rates = api_settings.DEFAULT_THROTTLE_RATES
        try:
            return rates[self.scope]
        except KeyError:
            raise ImproperlyConfigured(f"No default throttle rate set for '{self.scope}' scope")

    def get_ident(self, request):
        from common.client_ip import resolve_client_ip

        return resolve_client_ip(request) or "unknown"


class TrustedAnonRateThrottle(TrustedIdentMixin, AnonRateThrottle):
    pass


class TrustedUserRateThrottle(TrustedIdentMixin, UserRateThrottle):
    pass


class TrustedScopedRateThrottle(TrustedIdentMixin, ScopedRateThrottle):
    pass
