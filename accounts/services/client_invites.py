"""Phone-first, manager-assisted client onboarding.

A print manager onboarding a client over a phone call or WhatsApp may capture
only a phone number. Printy stores a single-use :class:`ClientInvite`; the
manager relays the claim link to the client, and the client claims it with
their own password. The manager never holds the password, and creating or
claiming an invite never touches an existing account's credentials and never
counts as client consent or quote acceptance.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounts.models import ClientInvite, User, UserProfile
from accounts.services.phone import (
    normalize_phone,
    phone_is_linked_elsewhere,
    set_origin_manager,
    set_profile_phone,
)

DEFAULT_INVITE_TTL_HOURS = 72


def _safe_normalize(value) -> str:
    """Normalise a phone for comparison, returning "" when it is unusable."""
    try:
        return normalize_phone(value)
    except Exception:
        return ""


def invite_ttl_hours() -> int:
    return int(getattr(settings, "CLIENT_INVITE_TTL_HOURS", DEFAULT_INVITE_TTL_HOURS))


def invite_expiry():
    return timezone.now() + timedelta(hours=invite_ttl_hours())


def build_invite_claim_url(invite: ClientInvite) -> str:
    base = str(getattr(settings, "FRONTEND_URL", "") or "").rstrip("/")
    path = f"/app/client-invite?token={invite.token}"
    return f"{base}{path}" if base else path


def expire_stale_invites(phone_normalized: str) -> None:
    if not phone_normalized:
        return
    ClientInvite.objects.filter(
        phone_normalized=phone_normalized,
        status=ClientInvite.Status.PENDING,
        expires_at__lte=timezone.now(),
    ).update(status=ClientInvite.Status.EXPIRED)


def find_verified_client_by_phone(phone_normalized: str):
    if not phone_normalized:
        return None
    profile = (
        UserProfile.objects.select_related("user")
        .filter(
            phone_normalized=phone_normalized,
            phone_verified_at__isnull=False,
        )
        .first()
    )
    if profile is None:
        return None
    user = profile.user
    if user.role != User.Role.CLIENT:
        return None
    return user


def find_account_by_phone(phone_normalized: str):
    """Return the profile owner for a normalised phone, verified or not."""
    if not phone_normalized:
        return None
    profile = (
        UserProfile.objects.select_related("user")
        .filter(phone_normalized=phone_normalized)
        .order_by("-phone_verified_at", "id")
        .first()
    )
    return profile.user if profile else None


@transaction.atomic
def create_client_invite(
    *,
    created_by,
    name: str = "",
    phone: str,
    email: str = "",
    company: str = "",
) -> tuple[ClientInvite | None, User | None]:
    """Create (or refresh) a pending invite, or return an existing client.

    Returns ``(invite, linked_user)``. When ``linked_user`` is not None an
    account already owns this phone and no invite is created.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        raise ValueError("A valid phone number is required to invite a client.")

    owner = find_account_by_phone(normalized)
    if owner is not None:
        if owner.role == User.Role.CLIENT:
            # Law 10.4/10.5: an existing client for this number is reused,
            # never duplicated, and never re-invited or claimed.
            return None, owner
        raise ValueError(
            "This phone number belongs to an existing manager or printer account "
            "and cannot be used for a client."
        )

    expire_stale_invites(normalized)

    invite = (
        ClientInvite.objects.select_for_update()
        .filter(phone_normalized=normalized, status=ClientInvite.Status.PENDING)
        .first()
    )
    if invite is None:
        invite = ClientInvite(
            created_by=created_by,
            phone_normalized=normalized,
            token=secrets.token_urlsafe(24),
        )
    elif invite.created_by_id is None:
        invite.created_by = created_by
    # LAW 3 (first touch wins): a pending invite keeps the manager who first
    # captured this number. A second manager may refresh the details, but never
    # becomes the client's origin manager.
    invite.name = str(name or "").strip() or invite.name
    invite.email = str(email or "").strip().lower() or None
    invite.phone = str(phone or "").strip() or invite.phone
    invite.company = str(company or "").strip() or invite.company
    invite.status = ClientInvite.Status.PENDING
    invite.expires_at = invite_expiry()
    invite.token = secrets.token_urlsafe(24)
    invite.save()
    return invite, None


def _mark_invite_expired(invite_id) -> None:
    ClientInvite.objects.filter(
        pk=invite_id, status=ClientInvite.Status.PENDING
    ).update(status=ClientInvite.Status.EXPIRED, updated_at=timezone.now())


def claim_client_invite(*, token: str, password: str, name: str = ""):
    """Create the client account for a pending invite.

    Raises ``ValueError`` with a client-safe message on any refusal. Never
    mutates an existing account. The account is created inside a single
    transaction, but expired-invite bookkeeping is committed before the refusal
    is raised so a stale invite is not left looking claimable.
    """
    token = str(token or "").strip()
    if not token:
        raise ValueError("A claim token is required.")
    if not password or len(str(password)) < 8:
        raise ValueError("Choose a password with at least 8 characters.")

    invite = ClientInvite.objects.filter(token=token).first()
    if invite is None:
        raise ValueError("This client invite link is invalid.")
    if invite.status == ClientInvite.Status.CLAIMED:
        raise ValueError("This client invite has already been used. Please sign in instead.")
    if invite.status != ClientInvite.Status.PENDING:
        raise ValueError("This client invite is no longer active.")
    if invite.is_expired:
        _mark_invite_expired(invite.pk)
        raise ValueError("This client invite has expired. Ask your print manager to resend it.")

    with transaction.atomic():
        invite = ClientInvite.objects.select_for_update().get(pk=invite.pk)
        if invite.status != ClientInvite.Status.PENDING or invite.is_expired:
            raise ValueError("This client invite is no longer active.")

        normalized = invite.phone_normalized
        if not normalized:
            raise ValueError("This client invite has no phone number attached.")

        existing = find_account_by_phone(normalized)
        if existing is not None:
            if existing.role == User.Role.CLIENT:
                raise ValueError(
                    "An account already exists for this phone number. Please sign in with "
                    "your password instead."
                )
            raise ValueError(
                "This phone number belongs to an existing manager or printer account and "
                "cannot be used for a client."
            )

        if phone_is_linked_elsewhere(normalized):
            raise ValueError(
                "An account already exists for this phone number. Please sign in with "
                "your password instead."
            )

        display_name = str(name or "").strip() or invite.name or ""
        user = User.objects.create_user(
            email=invite.email or None,
            password=password,
            name=display_name,
            role=User.Role.CLIENT,
            phone=invite.phone or normalized,
            phone_verified=True,
        )

        # LAW 1-3: the manager whose invite created this account is the client's
        # origin manager, fixed from here on. A client who already existed could
        # never reach this branch (checked above), so nothing is ever re-originated.
        set_origin_manager(user, invite.created_by)

        if invite.email:
            from allauth.account.models import EmailAddress

            EmailAddress.objects.update_or_create(
                user=user,
                email=invite.email,
                defaults={"primary": True, "verified": False},
            )

        invite.status = ClientInvite.Status.CLAIMED
        invite.client_user = user
        invite.claimed_at = timezone.now()
        invite.save(update_fields=["status", "client_user", "claimed_at", "updated_at"])

        # Links the quotes for this phone and the jobs/payments already made under it;
        # the invite is already claimed here, so only the linking branches run.
        link_client_identity(user, normalized)
    return invite, user


@transaction.atomic
def link_pending_quotes_for_client(user, phone_normalized: str) -> int:
    """Associate manager-prepared quotes for this phone with the new client.

    Only links; never changes quote status. Linking is association, not
    acceptance, so no quote becomes accepted by this call.
    """
    if not phone_normalized:
        return 0
    from quotes.models import QuoteRequest

    linked = 0
    candidates = (
        QuoteRequest.objects.select_for_update()
        .filter(on_behalf_of__isnull=True)
        .exclude(status__in=["accepted", "paid", "completed", "cancelled"])
        .order_by("id")
    )
    for quote_request in candidates:
        snapshot = (
            quote_request.request_snapshot
            if isinstance(quote_request.request_snapshot, dict)
            else {}
        )
        pending_client = snapshot.get("pending_client")
        pending_phone = ""
        if isinstance(pending_client, dict):
            pending_phone = _safe_normalize(pending_client.get("phone"))
        quote_phone = _safe_normalize(quote_request.customer_phone)
        if phone_normalized not in {pending_phone, quote_phone}:
            continue
        quote_request.on_behalf_of = user
        quote_request.save(update_fields=["on_behalf_of", "updated_at"])
        linked += 1
    return linked


def _link_jobs_and_payments_for_client(user, phone_normalized: str) -> int:
    """Point the jobs and payments already made for this phone at the account.

    LAW 12: quotes, jobs and payments made under the phone before the client had
    an account belong to the same client record once they sign up. Only empty
    links are filled - an existing payer or job client is never overwritten.
    """
    if not phone_normalized:
        return 0
    from jobs.models import ManagedJob
    from payments.models import Payment
    from quotes.models import QuoteRequest

    requests = QuoteRequest.objects.filter(on_behalf_of=user)
    linked = 0
    jobs = ManagedJob.objects.filter(
        source_quote_request__in=requests, client__isnull=True
    )
    for job in jobs:
        job.client = user
        job.save(update_fields=["client", "updated_at"])
        linked += 1
    payments = Payment.objects.filter(
        quote__quote_request__in=requests, payer__isnull=True
    ).select_related("quote")
    for payment in payments:
        payment.payer = user
        payment.save(update_fields=["payer", "updated_at"])
        linked += 1
    return linked


@transaction.atomic
def link_client_identity(user, phone) -> int:
    """LAW 12: bind a new account to the client record already held for its phone.

    Called when a client signs up with the phone a manager captured. It never
    creates a second client record: it reuses the pending invite, stamps the
    same ``origin_manager`` the invite already carried, marks that invite claimed
    by this account and attaches the quotes, jobs and payments already made under
    the phone. Returns the number of quote requests linked.
    """
    from accounts.models import ClientInvite, User

    normalized = _safe_normalize(phone)
    if not normalized or user is None or getattr(user, "pk", None) is None:
        return 0
    if getattr(user, "role", "") != User.Role.CLIENT:
        return 0

    # The phone may already belong to another client account. That account is the
    # client record; this second, unverified sign-up must not be merged into it.
    owner = find_account_by_phone(normalized)
    if owner is not None and owner.pk != user.pk:
        return 0

    invite = (
        ClientInvite.objects.select_for_update()
        .filter(phone_normalized=normalized)
        .order_by("created_at", "id")
        .first()
    )
    if invite is not None:
        # First touch wins: the invite's creator is the origin, whether the client
        # claimed the invitation or simply signed up with the same phone.
        set_origin_manager(user, invite.created_by)
        if invite.status != ClientInvite.Status.CLAIMED and invite.client_user_id is None:
            invite.status = ClientInvite.Status.CLAIMED
            invite.client_user = user
            invite.claimed_at = timezone.now()
            invite.save(update_fields=["status", "client_user", "claimed_at", "updated_at"])

    linked = link_pending_quotes_for_client(user, normalized)
    _link_jobs_and_payments_for_client(user, normalized)
    return linked


def attach_client_to_profile(user, phone: str, *, verified: bool = True):
    """Used by tests/tools to mark a phone verified on an existing client."""
    return set_profile_phone(user, phone, verified=verified)
