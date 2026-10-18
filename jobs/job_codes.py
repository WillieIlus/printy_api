"""Law 10.1: one identity shared by a quote reference and its job code.

The quote reference (``QR-...``) becomes the job code when the quote is
accepted. A job therefore shows "from QR-...", the quote reference and the job
code resolve to the same track page, and the code never changes after
acceptance. Every quote and job card, for every role, carries this code in its
footer as a tappable chip that opens the track page (law 7.7).

These helpers are pure and take model instances without importing models, so
they can be used from serializers, views and tests alike.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote as _urlquote

from django.conf import settings


def quote_request_code(quote_request: Any | None) -> str:
    """Return the ``QR-...`` code for a quote request (law 10.1)."""
    if quote_request is None:
        return ""
    reference = str(getattr(quote_request, "request_reference", "") or "").strip()
    if reference:
        return reference
    pk = getattr(quote_request, "pk", None) or getattr(quote_request, "id", None)
    return f"QR-{pk}" if pk else ""


def quote_code(quote: Any | None) -> str:
    """Return the ``Q-...`` code for a shop quote shown without its request."""
    if quote is None:
        return ""
    reference = str(getattr(quote, "quote_reference", "") or "").strip()
    if reference:
        return reference
    pk = getattr(quote, "pk", None) or getattr(quote, "id", None)
    return f"Q-{pk}" if pk else ""


def _job_quote_request(job: Any | None) -> Any | None:
    if job is None:
        return None
    quote_request = getattr(job, "source_quote_request", None)
    if quote_request is None:
        quote = getattr(job, "source_quote", None)
        quote_request = getattr(quote, "quote_request", None)
    return quote_request


def job_from_quote_ref(job: Any | None) -> str:
    """The quote reference a managed job came from ("from QR-...", law 10.1)."""
    return quote_request_code(_job_quote_request(job))


def job_code(job: Any | None) -> str:
    """The immutable code for a managed job (law 10.1).

    After acceptance the source quote reference *is* the job code. Before a
    source quote exists we fall back to the managed reference so every job still
    has a single code.
    """
    if job is None:
        return ""
    return job_from_quote_ref(job) or str(getattr(job, "managed_reference", "") or "").strip()


def tracking_url(code: str) -> str:
    """The shareable track link for a code (law 7.7 / 10.1).

    The link carries the code only; it contains no secret token. Opening it
    signed-out still requires the phone-on-the-job check (law 7.7).
    """
    code = str(code or "").strip()
    if not code:
        return ""
    base = str(getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    return f"{base}/track/{_urlquote(code)}"


def job_tracking_url(job: Any | None) -> str:
    return tracking_url(job_code(job))


__all__ = [
    "quote_request_code",
    "quote_code",
    "job_from_quote_ref",
    "job_code",
    "tracking_url",
    "job_tracking_url",
]
