from rest_framework.throttling import SimpleRateThrottle


class AnalyticsEventThrottle(SimpleRateThrottle):
    """Conservative per-client throttle for analytics ingestion."""

    scope = "analytics_event"
    rate = "60/min"

    def get_cache_key(self, request, view):
        if request.user and request.user.is_authenticated:
            ident = f"user:{request.user.pk}"
        else:
            ident = f"ip:{self.get_ident(request)}"
        return self.cache_format % {
            "scope": self.scope,
            "ident": ident,
        }


class GuestQuoteRequestThrottle(SimpleRateThrottle):
    """Rate-limit anonymous quote request submissions to prevent spam."""

    scope = "guest_quote_request"
    rate = "10/hour"

    def get_cache_key(self, request, view):
        ident = f"ip:{self.get_ident(request)}"
        return self.cache_format % {
            "scope": self.scope,
            "ident": ident,
        }
