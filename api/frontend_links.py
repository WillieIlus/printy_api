"""Translate stored deep-link action URLs to the current frontend route scheme.

Frontend dashboard routes live under ``/app/*`` (single-page dashboards). Links
persisted in message metadata before that migration (e.g.
``/dashboard/client/requests/{id}/quote/{id}``) no longer resolve, so stored links
are normalised to their routed equivalent whenever they are read back out.
"""
import re
from urllib.parse import urlsplit

_LEGACY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^/dashboard/client/requests/\d+/quote/\d+$"), "/app/buyer?tab=quote"),
    (re.compile(r"^/dashboard/client/requests/\d+$"), "/app/buyer?tab=quote"),
    (re.compile(r"^/dashboard/client/quotes/\d+$"), "/app/buyer?tab=quote"),
    (re.compile(r"^/dashboard/client/jobs/\d+$"), "/app/buyer"),
    (re.compile(r"^/dashboard/client"), "/app/buyer"),
    (re.compile(r"^/dashboard/partner"), "/app/manager"),
    (re.compile(r"^/dashboard/(production|printshop|shop)"), "/app/printer"),
    (re.compile(r"^/dashboard/admin"), "/app/admin"),
)


def _strip_origin(url: str) -> str:
    """Drop an optional FRONTEND_URL origin so path patterns can match."""
    if url.startswith(("http://", "https://")):
        parts = urlsplit(url)
        path = parts.path
        return f"{path}?{parts.query}" if parts.query else path
    return url


def normalize_frontend_path(url: str) -> str:
    """Return the routed equivalent of a stored link.

    Already-routed ``/app/*`` links and unrelated paths pass through unchanged.
    Legacy ``/dashboard/*`` links are mapped to the dashboard for the role they
    target (the client quote deep link lands on the buyer quotes tab).
    """
    if not url:
        return ""
    path = _strip_origin(url).rstrip("/")
    if path.startswith("/app/") or not path.startswith("/dashboard"):
        return path
    for pattern, routed in _LEGACY_PATTERNS:
        if pattern.match(path):
            return routed
    return "/"