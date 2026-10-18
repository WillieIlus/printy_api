"""OTP service (law 10.4).

Codes are generated, stored **hashed** (salted PBKDF2 via Django's password
hashers), expire, are limited in attempts, and resends are throttled. Viewing a
link needs no OTP; actions (artwork upload, price/date acknowledgement, payment
initiation) require a verified challenge for the client's phone.
"""
from __future__ import annotations

import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from clients.models import ClientOtp, new_verified_token, otp_ttl


class OtpError(Exception):
    """Base class for OTP failures (message is client-safe)."""


class OtpThrottled(OtpError):
    pass


class OtpLocked(OtpError):
    pass


class OtpExpired(OtpError):
    pass


class OtpInvalid(OtpError):
    pass


def _otp_length() -> int:
    return int(getattr(settings, "OTP_LENGTH", 6))


def _max_attempts() -> int:
    return int(getattr(settings, "OTP_MAX_ATTEMPTS", 5))


def _resend_cooldown_seconds() -> int:
    return int(getattr(settings, "OTP_RESEND_COOLDOWN_SECONDS", 60))


def generate_code(length: int | None = None) -> str:
    length = length or _otp_length()
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def _latest_challenge(*, phone_normalized: str, purpose: str, quote_request=None):
    queryset = ClientOtp.objects.filter(phone_normalized=phone_normalized, purpose=purpose)
    if quote_request is not None:
        queryset = queryset.filter(quote_request=quote_request)
    return queryset.order_by("-created_at", "-id").first()


@transaction.atomic
def request_otp(*, phone_normalized: str, purpose: str = ClientOtp.Purpose.CLIENT_ACTIONS, quote_request=None) -> tuple[ClientOtp, str]:
    """Create and return ``(challenge, plaintext_code)``.

    Raises :class:`OtpThrottled` when a code was sent within the resend
    cooldown. The caller is responsible for actually delivering the code.
    """
    phone = str(phone_normalized or "").strip()
    if not phone:
        raise OtpError("A phone number is required.")

    latest = _latest_challenge(phone_normalized=phone, purpose=purpose, quote_request=quote_request)
    cooldown = _resend_cooldown_seconds()
    if latest is not None and latest.last_sent_at is not None and cooldown > 0:
        elapsed = (timezone.now() - latest.last_sent_at).total_seconds()
        if elapsed < cooldown:
            raise OtpThrottled(
                f"Please wait {int(cooldown - elapsed) or 1}s before requesting another code."
            )

    code = generate_code()
    challenge = ClientOtp.objects.create(
        quote_request=quote_request,
        phone_normalized=phone,
        purpose=purpose,
        code_hash=make_password(code),
        expires_at=timezone.now() + otp_ttl(),
        max_attempts=_max_attempts(),
        resend_count=(latest.resend_count + 1) if latest is not None else 0,
        last_sent_at=timezone.now(),
    )
    return challenge, code


def validate_action_token(token: str, *, quote_request=None, phone_normalized: str | None = None) -> bool:
    """True when ``token`` is the verified token of a live challenge.

    Used by link-scoped action endpoints to enforce that the client verified
    their phone (law 10.4) before an action.
    """
    token = str(token or "").strip()
    if not token:
        return False
    queryset = ClientOtp.objects.filter(verified_token=token)
    if quote_request is not None:
        queryset = queryset.filter(quote_request=quote_request)
    if phone_normalized:
        queryset = queryset.filter(phone_normalized=phone_normalized)
    return queryset.filter(verified_at__isnull=False).exclude(verified_token="").exists()


def verify_otp(*, phone_normalized: str, code: str, purpose: str = ClientOtp.Purpose.CLIENT_ACTIONS, quote_request=None) -> ClientOtp:
    """Verify a code and return the now-verified challenge.

    A failed attempt increments the counter and is persisted **immediately** so
    it survives the raised error (the counter must not be rolled back).
    """
    phone = str(phone_normalized or "").strip()
    code = str(code or "").strip()
    if not phone or not code:
        raise OtpInvalid("Enter the code we sent you.")

    challenge = _latest_challenge(phone_normalized=phone, purpose=purpose, quote_request=quote_request)
    if challenge is None:
        raise OtpInvalid("Enter the code we sent you.")
    if challenge.is_locked:
        raise OtpLocked("Too many attempts. Request a new code.")
    if challenge.is_expired:
        raise OtpExpired("That code has expired. Request a new one.")
    if not check_password(code, challenge.code_hash):
        challenge.attempt_count += 1
        challenge.save(update_fields=["attempt_count", "updated_at"])
        if challenge.attempt_count >= challenge.max_attempts:
            raise OtpLocked("Too many attempts. Request a new code.")
        raise OtpInvalid("That code is not correct.")

    with transaction.atomic():
        challenge = ClientOtp.objects.select_for_update().get(pk=challenge.pk)
        challenge.verified_at = timezone.now()
        challenge.verified_token = new_verified_token()
        challenge.save(update_fields=["verified_at", "verified_token", "updated_at"])
    return challenge


def action_token_is_valid(token: str, *, quote_request=None, phone_normalized: str | None = None) -> bool:
    """True when ``token`` is a live verified token for the given scope."""
    token = str(token or "").strip()
    if not token:
        return False
    queryset = ClientOtp.objects.filter(verified_token=token, verified_at__isnull=False)
    if quote_request is not None:
        queryset = queryset.filter(quote_request=quote_request)
    if phone_normalized:
        queryset = queryset.filter(phone_normalized=str(phone_normalized).strip())
    challenge = queryset.order_by("-created_at", "-id").first()
    if challenge is None:
        return False
    if challenge.is_expired:
        return False
    return True


__all__ = [
    "OtpError",
    "OtpThrottled",
    "OtpLocked",
    "OtpExpired",
    "OtpInvalid",
    "generate_code",
    "request_otp",
    "verify_otp",
    "validate_action_token",
]
