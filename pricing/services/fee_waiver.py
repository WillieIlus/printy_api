"""The manager-side Printy fee waiver decision (LAW 1-6).

There is one rule: the Printy fee is waived only when the job's manager is the
client's origin manager. Everything here is about *deciding* that once; the
money itself is applied by :func:`pricing.services.platform_fee_policy.calculate_quote_financials`.

Life of the decision:

* a manager creates a client (accounts.services.client_invites) -> the client's
  ``UserProfile.origin_manager`` is stamped, once, and is immutable afterwards;
* a quote is priced/sent -> the decision is evaluated from the quote's own
  manager + client pair and FROZEN on ``QuoteFinancialSplit.fee_waived``;
* the quote is accepted -> ``ManagedJob.fee_waived`` receives that stored value;
* every later read (preview, sent quote, job totals, payouts) uses the stored
  flag and never re-derives it.
"""
from __future__ import annotations

from typing import Any

from accounts.services.roles import is_broker, is_client


def _profile_for(user):
    """The ``UserProfile`` behind an account, or None (never raises).

    A stamped origin is immutable (LAW 2), so a cached profile that already has
    one can be trusted. A cached profile **without** an origin may simply be a
    stale copy loaded before the client was stamped (``set_origin_manager``
    updates a different instance), which would silently turn a manager's own
    client into a stranger - so that case always re-reads the database.
    """
    if user is None or getattr(user, "pk", None) is None:
        return None
    profile = getattr(user, "profile", None)
    if profile is not None and getattr(profile, "origin_manager_id", None) is not None:
        return profile
    from accounts.models import UserProfile

    return UserProfile.objects.filter(user_id=user.pk).first()


def _origin_manager_id(client) -> int | None:
    if client is None or getattr(client, "pk", None) is None:
        return None
    profile = _profile_for(client)
    return None if profile is None else profile.origin_manager_id


def is_client_origin_pair(*, client, manager) -> bool:
    """True when ``manager`` created ``client`` (LAW 4).

    A marketplace client (origin_manager NULL) can never match, which is why the
    comparison is on the stored origin rather than on ownership or attribution.
    """
    manager_id = getattr(manager, "pk", None)
    client_id = getattr(client, "pk", None)
    if manager_id is None or client_id is None or manager_id == client_id:
        return False
    if not is_client(client):
        return False
    return _origin_manager_id(client) == manager_id


def fee_waived_for_pair(*, client, manager) -> bool:
    """The decision for an explicit client/manager pair, or False when unknown."""
    return is_client_origin_pair(client=client, manager=manager)


def _manager_candidates(quote_request) -> list[Any]:
    candidates = [
        getattr(quote_request, "assigned_manager", None),
        getattr(quote_request, "created_by_manager", None),
    ]
    created_by = getattr(quote_request, "created_by", None)
    if created_by is not None and (is_broker(created_by) or not is_client(created_by)):
        candidates.append(created_by)
    return candidates


def _client_candidates(quote_request) -> list[Any]:
    candidates = [getattr(quote_request, "on_behalf_of", None)]
    created_by = getattr(quote_request, "created_by", None)
    if created_by is not None and is_client(created_by):
        candidates.append(created_by)
    return candidates


def fee_waived_for_quote_request(quote_request) -> bool:
    """Decide from a ``QuoteRequest``'s manager/client pair, False when unknown.

    The first manager and the first client the request names are the ones that
    matter: ``assigned_manager`` (else ``created_by_manager``, else the partner
    who created it) against ``on_behalf_of`` (else a client-authored request).
    """
    if quote_request is None:
        return False
    managers = _manager_candidates(quote_request)
    if not any(manager is not None for manager in managers):
        return False
    for client in _client_candidates(quote_request):
        if client is None:
            continue
        for manager in managers:
            if manager is not None and is_client_origin_pair(client=client, manager=manager):
                return True
    return False


def fee_waived_for_quote(quote) -> bool:
    """The decision for a quote: its stored split wins, else derive it once."""
    if quote is None:
        return False
    stored = getattr(quote, "financial_split", None)
    if stored is not None:
        return bool(getattr(stored, "fee_waived", False))
    return fee_waived_for_quote_request(getattr(quote, "quote_request", None))


def fee_waived_for_job(job) -> bool:
    """The job's frozen decision (LAW 5). Never recomputed from its parties."""
    return bool(getattr(job, "fee_waived", False))


__all__ = [
    "fee_waived_for_job",
    "fee_waived_for_pair",
    "fee_waived_for_quote",
    "fee_waived_for_quote_request",
    "is_client_origin_pair",
]
