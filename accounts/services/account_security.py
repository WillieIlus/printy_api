"""Account-security helpers (audit #4b).

Pre-registration account takeover: anyone can register a local account for an
address they do not own. The account stays unconfirmed (login is refused while
ACCOUNT_EMAIL_VERIFICATION is mandatory), but its password, phone and any
refresh tokens belong to whoever registered it. When the real owner later
proves the address (Google ``email_verified``), the account must be cleaned of
every credential the unproven registrant could still hold before it is handed
over; otherwise confirming the address would hand the registrant a working
login to the owner's account.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def email_is_unconfirmed(user, email: str) -> bool:
    """True when ``user`` has address records but none verifying ``email``.

    Accounts with no EmailAddress rows at all (created before allauth, by
    ``createsuperuser`` or by admins) are treated the way password login treats
    them: trusted. See the audit #4b findings in results.txt.
    """
    from allauth.account.models import EmailAddress

    addresses = EmailAddress.objects.filter(user=user)
    if not addresses.exists():
        return False
    return not addresses.filter(email__iexact=email, verified=True).exists()


def revoke_refresh_tokens(user) -> int:
    """Blacklist every outstanding simplejwt refresh token of ``user``.

    Returns how many tokens were newly blacklisted. Access tokens are
    stateless and stay valid until they expire (ACCESS_TOKEN_LIFETIME).
    """
    try:
        from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
    except Exception:  # pragma: no cover - app always installed in settings
        return 0
    revoked = 0
    for token in OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False):
        _obj, created = BlacklistedToken.objects.get_or_create(token=token)
        revoked += int(created)
    return revoked


def secure_unconfirmed_account(user, *, reason: str) -> None:
    """Remove every credential an unproven registrant could hold.

    * the password is made unusable (this also changes the session auth hash,
      so Django sessions created with the old password stop authenticating);
    * all refresh tokens are blacklisted.

    The caller confirms the email address afterwards. The owner can set a new
    password through the normal reset flow.
    """
    user.set_unusable_password()
    update_fields = ["password"]
    if hasattr(user, "updated_at"):
        update_fields.append("updated_at")
    user.save(update_fields=update_fields)
    revoked = revoke_refresh_tokens(user)
    logger.warning(
        "account_secured_before_confirmation user_id=%s reason=%s revoked_refresh_tokens=%s",
        user.pk,
        reason,
        revoked,
    )
