"""The one place that decides a request's real client IP (audit #10b).

``REMOTE_ADDR`` is the direct TCP peer and the only value a client cannot
forge. ``X-Forwarded-For`` (and ``X-Real-IP``) are plain request headers: any
client can send them. They are believed only when the direct peer is one of
our own reverse proxies, listed in ``MPESA_TRUSTED_PROXIES`` (IPs or CIDRs; the
name predates this helper, the setting now governs every IP decision in the
app: M-Pesa callback allowlist, every DRF throttle, client-portal audit
records, analytics and payment-consent records).

When the peer is trusted, ``X-Forwarded-For`` is walked right to left,
skipping trusted hops; the first untrusted hop is the client. A malformed hop
means the chain was tampered with, so no IP is returned rather than a guess.

DRF's ``NUM_PROXIES`` is deliberately not used: it counts hops blindly and,
when unset, ``get_ident`` returns the raw left-most ``X-Forwarded-For`` value,
which let a client pick a fresh throttle bucket per request.
"""
from __future__ import annotations

import ipaddress
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

TRUSTED_PROXIES_SETTING = "MPESA_TRUSTED_PROXIES"


def as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def networks(entries) -> list:
    parsed = []
    for entry in as_list(entries):
        try:
            parsed.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.error("Ignoring invalid IP/CIDR in proxy/allowlist config: %r", entry)
    return parsed


def parse_ip(value):
    try:
        return ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None


def in_any(ip, nets) -> bool:
    return ip is not None and any(ip.version == net.version and ip in net for net in nets)


def trusted_proxy_networks() -> list:
    return networks(getattr(settings, TRUSTED_PROXIES_SETTING, None))


def resolve_client_ip(request) -> str | None:
    """The real peer address, trusting X-Forwarded-For only from our proxies."""
    meta = getattr(request, "META", {}) or {}
    remote = parse_ip(meta.get("REMOTE_ADDR"))
    if remote is None:
        return None
    trusted = trusted_proxy_networks()
    if not in_any(remote, trusted):
        # Direct connection (or an untrusted hop): headers are attacker-controlled.
        return str(remote)
    forwarded = [part.strip() for part in str(meta.get("HTTP_X_FORWARDED_FOR") or "").split(",") if part.strip()]
    for hop in reversed(forwarded):
        ip = parse_ip(hop)
        if ip is None:
            # A garbage hop appended by an untrusted party: stop and refuse to guess.
            return None
        if not in_any(ip, trusted):
            return str(ip)
    # Every hop was a trusted proxy (or there was no header): the request
    # originated on our side of the proxy.
    return str(remote)


def client_ident(request) -> str:
    """Throttle/cache identifier for an anonymous client."""
    ip = resolve_client_ip(request)
    return f"ip:{ip}" if ip else "ip:unknown"
