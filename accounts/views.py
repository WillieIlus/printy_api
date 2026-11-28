import logging

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from allauth.account.forms import (
    ChangePasswordForm,
    ResetPasswordForm,
    ResetPasswordKeyForm,
    UserTokenForm,
)
from allauth.account.internal.flows.password_reset import finalize_password_reset
from django.conf import settings as django_settings
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import User
from .throttling import AUTH_THROTTLE_CLASSES
from .services.roles import assign_role, get_public_assignable_roles, normalize_role_value
from .serializers import CustomTokenObtainPairSerializer, UserCreateSerializer, UserSerializer


logger = logging.getLogger("api.auth")


def _mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not local or not domain:
        return "***"
    if len(local) == 1:
        masked_local = "*"
    elif len(local) == 2:
        masked_local = f"{local[0]}*"
    else:
        masked_local = f"{local[0]}***{local[-1]}"
    return f"{masked_local}@{domain}"


class RegisterView(generics.CreateAPIView):
    """Register a new user (buyer or seller)."""

    permission_classes = [AllowAny]
    serializer_class = UserCreateSerializer
    throttle_classes = AUTH_THROTTLE_CLASSES
    throttle_scope = "auth_register"

    default_role = User.Role.CLIENT
    role_source = "signup"
    signup_intent = ""

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["default_role"] = self.default_role
        context["role_source"] = self.role_source
        context["signup_intent"] = self.signup_intent
        return context

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        headers = self.get_success_headers(serializer.data)
        verification_required = getattr(django_settings, "ACCOUNT_EMAIL_VERIFICATION", "none") != "none"
        return Response(
            {
                "detail": "Check your email to activate your Printy account.",
                "email": user.email,
                "verification_required": verification_required,
                "resend_available": True,
                "activation_email_sent": getattr(serializer, "activation_email_sent", False),
                "claimed_guest_draft_id": getattr(getattr(serializer, "claimed_guest_draft", None), "id", None),
                **({"signup_intent": self.signup_intent} if self.signup_intent else {}),
            },
            status=status.HTTP_201_CREATED,
            headers=headers,
        )


class ClientRegisterView(RegisterView):
    default_role = User.Role.CLIENT
    role_source = "signup_client"


class PartnerRegisterView(RegisterView):
    default_role = User.Role.PARTNER
    role_source = "signup_partner"


class ProductionRegisterView(RegisterView):
    """Printer *applicant* sign-up (audit #3).

    Registration never grants ``production``. The account is created as a
    plain client-role account and no shop is created; the printer then creates
    their shop via POST /api/shops/ (private until an admin approves it), which
    is what makes them production for that shop only.
    """

    default_role = User.Role.CLIENT
    role_source = "signup_printer_applicant"
    signup_intent = "printer_applicant"


class CustomTokenObtainPairView(TokenObtainPairView):
    """JWT token obtain view that accepts username or email."""

    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]
    throttle_classes = AUTH_THROTTLE_CLASSES
    throttle_scope = "auth_login"


class ClientInviteClaimView(APIView):
    """Claim a manager-created, phone-first client invite.

    The client (who may have no email) sets their own password here. The
    manager who created the invite never sets or receives the password, so this
    never links or takes over an existing account.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = AUTH_THROTTLE_CLASSES
    throttle_scope = "auth_invite_claim"

    def post(self, request):
        from accounts.serializers import UserSerializer
        from accounts.services.client_invites import claim_client_invite

        token = str(request.data.get("token") or request.data.get("claim_token") or "").strip()
        password = str(request.data.get("password") or "")
        name = str(request.data.get("name") or "").strip()

        if not token:
            return Response(
                {"detail": "A claim token is required.", "code": "TOKEN_REQUIRED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            invite, user = claim_client_invite(token=token, password=password, name=name)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        logger.info(
            "client_invite_claimed invite_id=%s user_id=%s phone=%s",
            invite.pk,
            user.pk,
            _mask_phone(invite.phone_normalized),
        )

        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "detail": "Welcome to Printy. Your account is ready.",
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user, context={"request": request}).data,
                "client_invite_id": invite.id,
            },
            status=status.HTTP_201_CREATED,
        )


def _mask_phone(phone: str) -> str:
    digits = str(phone or "")
    if len(digits) < 4:
        return "***"
    return f"{digits[:5]}***{digits[-2:]}"


class UserDetailView(generics.RetrieveUpdateAPIView):
    """Current user profile."""

    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


def classify_email_confirmation_key(key):
    """Classify an email confirmation key without mutating any state.

    Returns ``(status, email_address)`` where ``status`` is one of
    ``"valid"``, ``"already_verified"``, ``"expired"`` or ``"invalid"``.

    Mirrors allauth's own resolution (HMAC signer or the DB model) so the API
    can tell a genuinely expired link apart from a tampered/unknown one, and so
    a still-valid key for an already-verified address can be treated
    idempotently instead of being reported as "expired".
    """
    from allauth.account import app_settings as allauth_app_settings
    from allauth.account.models import EmailAddress, EmailConfirmation

    if allauth_app_settings.EMAIL_CONFIRMATION_HMAC:
        salt = allauth_app_settings.SALT
        max_age = 60 * 60 * 24 * allauth_app_settings.EMAIL_CONFIRMATION_EXPIRE_DAYS
        try:
            pk = signing.loads(key, max_age=max_age, salt=salt)
            expired = False
        except signing.SignatureExpired:
            try:
                pk = signing.loads(key, salt=salt)
            except signing.BadSignature:
                return "invalid", None
            expired = True
        except signing.BadSignature:
            return "invalid", None

        try:
            email_address = EmailAddress.objects.get(pk=pk)
        except (EmailAddress.DoesNotExist, ValueError, TypeError):
            return "invalid", None

        if expired:
            return "expired", email_address
        if email_address.verified:
            return "already_verified", email_address
        return "valid", email_address

    record = EmailConfirmation.objects.all_valid().filter(key=key.lower()).first()
    if record is None:
        stale = EmailConfirmation.objects.filter(key=key.lower()).first()
        if stale is None:
            return "invalid", None
        return "expired", stale.email_address
    if record.email_address.verified:
        return "already_verified", record.email_address
    return "valid", record.email_address


class ConfirmEmailView(APIView):
    """Verify an email address from a signed confirmation link.

    The signed key is the only proof accepted. The address in the link is a
    display hint for the confirmation page (it prefills the resend field); it
    can never activate an account on its own, because anyone can type an
    address they do not control.
    """

    permission_classes = [AllowAny]

    @staticmethod
    def _verified_response(email_address, *, already_verified: bool) -> Response:
        return Response(
            {
                "detail": (
                    "This email is already verified."
                    if already_verified
                    else "Email confirmed successfully."
                ),
                "email": email_address.email,
                "verified": True,
                "already_verified": already_verified,
            }
        )

    @staticmethod
    def _invalid_response(key: str) -> Response:
        # Distinguish a structurally-valid key whose signature no longer verifies
        # (the symptom when SECRET_KEY is rotated between issue and click) from a
        # genuinely malformed key, so operators can tell the two apart in logs.
        signature_failed = False
        if key and not key.startswith("ey") and key.count(":") >= 1:
            try:
                from allauth.account import app_settings as allauth_app_settings

                signing.loads(key, salt=allauth_app_settings.SALT, max_age=None)
            except signing.SignatureExpired:
                signature_failed = True
            except signing.BadSignature:
                signature_failed = True
            except Exception:
                signature_failed = False

        logger.warning(
            "email_verification_confirm_rejected state=invalid signature_failed=%s key_len=%s",
            signature_failed,
            len(key),
        )
        return Response(
            {
                "detail": "This verification link is invalid.",
                "code": "TOKEN_INVALID",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    def post(self, request):
        key = (request.data.get("key") or request.data.get("token") or "").strip()
        if not key:
            logger.warning("email_verification_confirm_rejected state=missing_key")
            return Response(
                {
                    "detail": (
                        "This verification link is missing its confirmation key. "
                        "Request a new verification email."
                    ),
                    "code": "TOKEN_REQUIRED",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from allauth.account.models import EmailConfirmationHMAC, EmailConfirmation

        confirmation = EmailConfirmationHMAC.from_key(key)
        if confirmation is None:
            confirmation = EmailConfirmation.from_key(key)

        if confirmation is not None:
            email_address = confirmation.confirm(request)
            if email_address is not None:
                logger.info(
                    "email_verification_confirmed email=%s user_id=%s",
                    _mask_email(email_address.email),
                    email_address.user_id,
                )
                return self._verified_response(email_address, already_verified=False)

        state, email_address = classify_email_confirmation_key(key)

        if state == "already_verified" and email_address is not None:
            logger.info(
                "email_verification_already_confirmed email=%s user_id=%s",
                _mask_email(email_address.email),
                email_address.user_id,
            )
            return self._verified_response(email_address, already_verified=True)

        if state == "expired":
            logger.warning(
                "email_verification_confirm_rejected state=expired key_len=%s",
                len(key),
            )
            return Response(
                {
                    "detail": "This verification link has expired. Request a new one.",
                    "code": "TOKEN_EXPIRED",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return self._invalid_response(key)


class ResendEmailConfirmationView(APIView):
    """Re-send the confirmation email for an unverified address."""

    permission_classes = [AllowAny]
    throttle_classes = AUTH_THROTTLE_CLASSES
    throttle_scope = "auth_email_resend"

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        if not email:
            return Response(
                {"detail": "email is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        from allauth.account.models import EmailAddress

        sent = False
        already_verified = False
        email_address = (
            EmailAddress.objects.filter(email__iexact=email, verified=False)
            .order_by("-primary", "id")
            .first()
        )
        if email_address is None:
            already_verified = EmailAddress.objects.filter(
                email__iexact=email, verified=True
            ).exists()

        if email_address is not None:
            try:
                email_address.send_confirmation(request)
                sent = True
            except Exception:
                logger.exception(
                    "email_verification_resend_failed email=%s",
                    _mask_email(email),
                )

        logger.info(
            "email_verification_resend_requested email=%s outcome=%s",
            _mask_email(email),
            "sent" if sent else ("already_verified" if already_verified else "noop"),
        )
        return Response(
            {
                "detail": (
                    "This email is already verified. You can sign in."
                    if already_verified
                    else "If that address exists and is unverified, a new confirmation email has been sent."
                ),
                "sent": sent,
                "already_verified": already_verified,
            }
        )


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = AUTH_THROTTLE_CLASSES
    throttle_scope = "auth_password_reset"

    def post(self, request):
        email = str(request.data.get("email", "")).strip()

        form = ResetPasswordForm(data={"email": email})
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

        # Phase 3A Task D: allauth resolves the target user via
        # filter_users_by_email, which lowercases the input and then does a
        # case-sensitive EmailAddress lookup. Legacy rows keep their original
        # casing, so a lowercase request silently finds nobody and no reset
        # email is sent. Resolve case-insensitively and, only if allauth found
        # nobody, hand it the users we found.
        if not getattr(form, "users", None):
            from allauth.account.models import EmailAddress

            resolved: list = []
            for address in EmailAddress.objects.filter(email__iexact=email).select_related("user"):
                if address.user and address.user.is_active:
                    resolved.append(address.user)
            if not resolved:
                from django.contrib.auth import get_user_model

                resolved = [
                    user
                    for user in get_user_model().objects.filter(email__iexact=email).iterator(2000)
                    if user.is_active
                ]
            if resolved:
                form.users = resolved

        form.save(request)
        return Response(
            {
                "detail": "If that email exists, a password reset link has been sent.",
            }
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = AUTH_THROTTLE_CLASSES
    throttle_scope = "auth_password_reset_confirm"

    def post(self, request):
        opaque_key = str(request.data.get("key", "")).strip()
        password = str(request.data.get("password", "")).strip()

        if not opaque_key or not password:
            return Response(
                {"detail": "key and password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if "-" not in opaque_key:
            return Response(
                {"detail": "Invalid password reset key."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        uidb36, temp_key = opaque_key.split("-", 1)
        token_form = UserTokenForm(data={"uidb36": uidb36, "key": temp_key})
        if not token_form.is_valid():
            return Response(
                {"detail": "Invalid or expired password reset key."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reset_form = ResetPasswordKeyForm(
            data={"password1": password, "password2": password},
            user=token_form.reset_user,
            temp_key=temp_key,
        )
        if not reset_form.is_valid():
            errors = []
            for field_errors in reset_form.errors.values():
                errors.extend(field_errors)
            return Response(
                {"detail": " ".join(errors) or "Password reset failed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reset_form.save()
        # Audit #4b: a reset is account recovery, so refresh tokens issued
        # before it (to whoever held the old password) stop working. The new
        # password hash already invalidates Django sessions.
        from .services.account_security import revoke_refresh_tokens

        revoke_refresh_tokens(token_form.reset_user)
        finalize_password_reset(request, token_form.reset_user, email=getattr(token_form.reset_user, "email", None))
        return Response({"detail": "Password reset successful."})


class PasswordChangeView(APIView):
    """Change the authenticated user's password from their current one."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        current_password = str(request.data.get("current_password", "")).strip()
        new_password = str(request.data.get("new_password", "")).strip()

        if not current_password or not new_password:
            return Response(
                {"detail": "current_password and new_password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        form = ChangePasswordForm(
            user=request.user,
            data={
                "oldpassword": current_password,
                "password1": new_password,
                "password2": new_password,
            },
        )
        if not form.is_valid():
            errors = []
            for field_errors in form.errors.values():
                errors.extend(field_errors)
            return Response(
                {"detail": " ".join(errors) or "Password change failed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        form.save()
        return Response({"detail": "Password changed successfully."})


def _google_token_audiences() -> list[str] | None:
    """The configured OAuth client id(s), or raise if Google login is unconfigured.

    Returns ``None`` (skip the audience check) only under the explicit dev/test
    override; see GOOGLE_OAUTH_DEV_SKIP_AUDIENCE in config/settings.py.
    """
    audiences = list(getattr(django_settings, "GOOGLE_CLIENT_IDS", None) or [])
    if audiences:
        return audiences
    dev_override = (
        bool(getattr(django_settings, "GOOGLE_OAUTH_DEV_SKIP_AUDIENCE", False))
        and bool(getattr(django_settings, "DEBUG", False))
        and str(getattr(django_settings, "APP_ENV", "production")).lower()
        in {"local", "dev", "development", "test"}
    )
    if dev_override:
        logger.warning("google_social_login audience check skipped (GOOGLE_OAUTH_DEV_SKIP_AUDIENCE)")
        return None
    raise ImproperlyConfigured("GOOGLE_CLIENT_ID is not configured.")


def verify_google_id_token(raw_token: str, audiences: list[str] | None) -> dict:
    """Verify signature, expiry, issuer and audience with google-auth.

    Raises ``ValueError`` for a token that must be rejected and
    ``google.auth.exceptions.TransportError`` when Google's certs are unreachable.
    """
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    return google_id_token.verify_oauth2_token(
        raw_token,
        google_requests.Request(),
        audience=audiences,
        clock_skew_in_seconds=10,
    )


def _google_email_verified(claims: dict) -> bool:
    value = claims.get("email_verified")
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() == "true"


class GoogleSocialLoginView(APIView):
    """
    POST /api/auth/social/google/ — verify a Google ID token, return JWTs.

    Audit #4:
    - The token is verified locally with google-auth (signature, expiry,
      issuer in accounts.google.com, audience == GOOGLE_CLIENT_ID). The old
      tokeninfo round-trip is gone. No GOOGLE_CLIENT_ID → 503, fail closed.
    - ``email_verified`` must be true.
    - Accounts are matched case-insensitively (Name@x == name@x). If legacy
      data already holds several accounts for one address the login is refused
      (409) and nothing is merged; see ``manage.py report_duplicate_emails``.
    - Inactive accounts are refused.
    - A role is chosen ONLY when the account is created, and only from the
      self-service allowlist. An existing account's role is never touched.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = AUTH_THROTTLE_CLASSES
    throttle_scope = "auth_google_login"

    def post(self, request):
        from google.auth import exceptions as google_exceptions

        raw_token = str(request.data.get("id_token") or "").strip()
        if not raw_token:
            return Response({"detail": "id_token is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            audiences = _google_token_audiences()
        except ImproperlyConfigured:
            logger.error("google_social_login refused: GOOGLE_CLIENT_ID is not configured")
            return Response(
                {
                    "detail": "Google sign-in is not configured on this server.",
                    "code": "google_oauth_not_configured",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            claims = verify_google_id_token(raw_token, audiences)
        except google_exceptions.TransportError:
            logger.warning("google_social_login cert fetch failed")
            return Response({"detail": "Could not verify Google token."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except (ValueError, google_exceptions.GoogleAuthError) as exc:
            logger.info("google_social_login rejected token: %s", exc.__class__.__name__)
            return Response(
                {"detail": "Invalid Google token.", "code": "invalid_google_token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _google_email_verified(claims):
            return Response(
                {"detail": "Google account email is not verified.", "code": "google_email_unverified"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = str(claims.get("email") or "").strip().lower()
        if not email:
            return Response({"detail": "Google account has no email address."}, status=status.HTTP_400_BAD_REQUEST)
        google_sub = str(claims.get("sub") or "").strip()

        from allauth.account.models import EmailAddress
        from allauth.socialaccount.models import SocialAccount

        social = None
        if google_sub:
            social = SocialAccount.objects.select_related("user").filter(provider="google", uid=google_sub).first()

        if social is not None:
            user = social.user
            created = False
        else:
            matches = list(User.objects.filter(email__iexact=email).order_by("id")[:2])
            if len(matches) > 1:
                logger.warning("google_social_login duplicate accounts for email=%s", _mask_email(email))
                return Response(
                    {
                        "detail": "More than one Printy account uses this email. Contact support to sort it out.",
                        "code": "duplicate_email_accounts",
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            user = matches[0] if matches else None
            created = user is None

        if user is not None and not user.is_active:
            return Response(
                {"detail": "This account is inactive.", "code": "account_inactive"},
                status=status.HTTP_403_FORBIDDEN,
            )

        with transaction.atomic():
            secured_unconfirmed = False
            if not created and social is None:
                # Audit #4b: the address is proved by Google now, but the local
                # account was registered by someone who never proved it. Before
                # confirming the address (which would make that registrant's
                # password work) remove every credential they could hold.
                from .services.account_security import email_is_unconfirmed, secure_unconfirmed_account

                user = User.objects.select_for_update().get(pk=user.pk)
                if email_is_unconfirmed(user, email):
                    secure_unconfirmed_account(user, reason="google_login_on_unconfirmed_email")
                    secured_unconfirmed = True
            if created:
                requested_role = normalize_role_value(str(request.data.get("role") or "client").strip())
                role = requested_role if requested_role in get_public_assignable_roles() else User.Role.CLIENT
                user = User.objects.create_user(
                    email=email,
                    password=None,
                    name=str(claims.get("name") or ""),
                    first_name=str(claims.get("given_name") or ""),
                    last_name=str(claims.get("family_name") or ""),
                    role=role,
                    is_active=True,
                )
                assign_role(user, role, source="google_social_login")
                if role == User.Role.PARTNER:
                    from .models import UserProfile

                    UserProfile.objects.update_or_create(
                        user=user, defaults={"manager_verified": False, "manager_verified_at": None}
                    )
            elif not user.name and claims.get("name"):
                user.name = str(claims.get("name"))
                user.save(update_fields=["name", "updated_at"])

            address = EmailAddress.objects.filter(user=user, email__iexact=email).first()
            if address is None:
                EmailAddress.objects.create(
                    user=user,
                    email=email,
                    primary=not EmailAddress.objects.filter(user=user, primary=True).exists(),
                    verified=True,
                )
            elif not address.verified:
                address.verified = True
                address.save(update_fields=["verified"])

            if google_sub and social is None:
                SocialAccount.objects.get_or_create(
                    provider="google",
                    uid=google_sub,
                    defaults={"user": user, "extra_data": dict(claims)},
                )

        refresh = RefreshToken.for_user(user)
        logger.info(
            "google_social_login email=%s created=%s secured_unconfirmed=%s",
            _mask_email(email),
            created,
            secured_unconfirmed,
        )

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user, context={"request": request}).data,
            }
        )
