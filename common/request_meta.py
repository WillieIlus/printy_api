"""Helpers for extracting analytics-friendly request metadata."""
from __future__ import annotations

from typing import Any

from django.conf import settings
from django.utils.module_loading import import_string


def _truncate(value: str | None, max_length: int) -> str:
    if not value:
        return ""
    return value[:max_length]


def get_client_ip(request) -> str | None:
    """Return the best-effort client IP address from proxied request headers."""
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        parts = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if parts:
            return parts[0]

    real_ip = request.META.get("HTTP_X_REAL_IP")
    if real_ip:
        return real_ip.strip()

    remote_addr = request.META.get("REMOTE_ADDR")
    if remote_addr:
        return remote_addr.strip()

    return None


def get_user_agent(request, max_length: int = 512) -> str:
    """Return the request user agent, truncated for storage."""
    return _truncate(request.META.get("HTTP_USER_AGENT"), max_length)


def get_referer(request, max_length: int = 1024) -> str:
    """Return the request referer, truncated for storage."""
    return _truncate(request.META.get("HTTP_REFERER"), max_length)


def get_session_key(request) -> str:
    """Return the current session key if the session middleware is active."""
    session = getattr(request, "session", None)
    if not session:
        return ""
    return session.session_key or ""


def get_visitor_id(request, max_length: int = 128) -> str:
    """Return an anonymous visitor identifier from headers or cookies when present."""
    visitor_id = (
        request.META.get("HTTP_X_VISITOR_ID")
        or request.COOKIES.get("visitor_id")
        or request.COOKIES.get("visitorId")
        or ""
    )
    return _truncate(visitor_id.strip(), max_length)


def get_request_query_params(request) -> dict[str, Any]:
    """Normalize a QueryDict into a JSON-safe dict for analytics storage."""
    params: dict[str, Any] = {}
    for key in request.GET.keys():
        values = request.GET.getlist(key)
        if not values:
            continue
        params[key] = values[0] if len(values) == 1 else values
    return params


def get_request_path(request, max_length: int = 1024) -> str:
    """Return the request path, including query string when available."""
    path = ""
    if hasattr(request, "get_full_path"):
        path = request.get_full_path()
    else:
        path = getattr(request, "path", "") or ""
    return _truncate(path, max_length)


def get_request_method(request, max_length: int = 16) -> str:
    """Return the normalized HTTP method."""
    return _truncate((getattr(request, "method", "") or "").upper(), max_length)


def resolve_geo_from_ip(ip_address: str | None) -> dict[str, str]:
    """
    Resolve coarse geo fields from an IP address.

    Extension point:
    - Set `ANALYTICS_GEOLOCATION_RESOLVER` to a dotted-path callable.
    - Callable signature: resolver(ip_address) -> dict with optional
      `country`, `region`, and `city` keys.
    """
    default = {"country": "", "region": "", "city": ""}
    if not ip_address:
        return default

    resolver_path = getattr(settings, "ANALYTICS_GEOLOCATION_RESOLVER", "")
    if not resolver_path:
        return default

    resolver = import_string(resolver_path)
    try:
        result = resolver(ip_address) or {}
    except Exception:
        return default

    return {
        "country": _truncate(str(result.get("country", "") or ""), 100),
        "region": _truncate(str(result.get("region", "") or ""), 100),
        "city": _truncate(str(result.get("city", "") or ""), 100),
    }
