import logging
import os

import requests as http_requests
from django.contrib.auth import get_user_model
from allauth.account.forms import ResetPasswordForm, ResetPasswordKeyForm, UserTokenForm
from allauth.account.internal.flows.password_reset import finalize_password_reset
from django.conf import settings as django_settings
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .models import User
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

    default_role = User.Role.CLIENT
    role_source = "signup"

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["default_role"] = self.default_role
        context["role_source"] = self.role_source
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
                "claimed_guest_draft_id": getattr(getattr(serializer, "claimed_guest_draft", None), "id", None),
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
    default_role = User.Role.PRODUCTION
    role_source = "signup_production"


class CustomTokenObtainPairView(TokenObtainPairView):
    """JWT token obtain view that accepts username or email."""

    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]


class UserDetailView(generics.RetrieveUpdateAPIView):
    """Current user profile."""

    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class ConfirmEmailView(APIView):
    """Accept the key from the confirmation email and mark the address verified."""

    permission_classes = [AllowAny]

    def post(self, request):
        key = request.data.get("key", "").strip()
        if not key:
            return Response(
                {"detail": "key is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        from allauth.account.models import EmailConfirmationHMAC, EmailConfirmation

        confirmation = EmailConfirmationHMAC.from_key(key)
        if confirmation is None:
            confirmation = EmailConfirmation.from_key(key)

        if confirmation is None:
            logger.info("email_verification_confirm_invalid_key")
            return Response(
                {"detail": "Invalid or expired confirmation key."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email_address = confirmation.confirm(request)
        if not email_address:
            logger.info("email_verification_confirm_failed")
            return Response(
                {"detail": "Invalid or expired confirmation key."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logger.info(
            "email_verification_confirmed email=%s user_id=%s",
            _mask_email(email_address.email),
            email_address.user_id,
        )
        return Response(
            {
                "detail": "Email confirmed successfully.",
                "email": email_address.email,
                "verified": True,
            }
        )


class ResendEmailConfirmationView(APIView):
    """Re-send the confirmation email for an unverified address."""

    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip().lower()
        if not email:
            return Response(
                {"detail": "email is required."}, status=status.HTTP_400_BAD_REQUEST
            )

        from allauth.account.models import EmailAddress

        sent = False
        try:
            email_address = EmailAddress.objects.get(email__iexact=email, verified=False)
            email_address.send_confirmation(request)
            sent = True
        except EmailAddress.DoesNotExist:
            pass

        logger.info(
            "email_verification_resend_requested email=%s outcome=%s",
            _mask_email(email),
            "sent" if sent else "noop",
        )
        return Response(
            {
                "detail": "If that address exists and is unverified, a new confirmation email has been sent.",
                "sent": sent,
            }
        )


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        form = ResetPasswordForm(data={"email": request.data.get("email", "")})
        if not form.is_valid():
            return Response(form.errors, status=status.HTTP_400_BAD_REQUEST)

        form.save(request)
        return Response(
            {
                "detail": "If that email exists, a password reset link has been sent.",
            }
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

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
        finalize_password_reset(request, token_form.reset_user, email=getattr(token_form.reset_user, "email", None))
        return Response({"detail": "Password reset successful."})


class GoogleSocialLoginView(APIView):
    """
    Verify a Google ID token and return JWT tokens for the user.
    Creates the user account on first login.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        id_token = (request.data.get("id_token") or "").strip()
        if not id_token:
            return Response({"detail": "id_token is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            resp = http_requests.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": id_token},
                timeout=10,
            )
        except http_requests.RequestException:
            return Response({"detail": "Could not verify Google token."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        if resp.status_code != 200:
            return Response({"detail": "Invalid Google token."}, status=status.HTTP_400_BAD_REQUEST)

        google_data = resp.json()

        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        if client_id and google_data.get("aud") != client_id:
            return Response({"detail": "Token audience mismatch."}, status=status.HTTP_400_BAD_REQUEST)

        if str(google_data.get("email_verified", "")).lower() not in ("true", "1"):
            return Response({"detail": "Google account email is not verified."}, status=status.HTTP_400_BAD_REQUEST)

        email = (google_data.get("email") or "").strip().lower()
        if not email:
            return Response({"detail": "Google account has no email address."}, status=status.HTTP_400_BAD_REQUEST)

        name = google_data.get("name", "")
        given_name = google_data.get("given_name", "")
        family_name = google_data.get("family_name", "")
        google_sub = google_data.get("sub", "")
        requested_role = normalize_role_value((request.data.get("role") or "client").strip()) or User.Role.CLIENT
        role = requested_role if requested_role in get_public_assignable_roles() else User.Role.CLIENT
        partner_profile_enabled = bool(request.data.get("partner_profile_enabled", False))

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "name": name,
                "first_name": given_name,
                "last_name": family_name,
                "role": role,
                "partner_profile_enabled": partner_profile_enabled,
                "is_active": True,
            },
        )

        if not created and not user.name and name:
            user.name = name
            user.save(update_fields=["name", "updated_at"] if hasattr(user, "updated_at") else ["name"])
        if role == User.Role.PARTNER and not user.partner_profile_enabled:
            user.partner_profile_enabled = True
            user.save(update_fields=["partner_profile_enabled", "updated_at"] if hasattr(user, "updated_at") else ["partner_profile_enabled"])
        assign_role(user, role, source="google_social_login")

        from allauth.account.models import EmailAddress
        EmailAddress.objects.get_or_create(
            user=user,
            email=email,
            defaults={"primary": True, "verified": True},
        )

        if google_sub:
            from allauth.socialaccount.models import SocialAccount
            SocialAccount.objects.get_or_create(
                user=user,
                provider="google",
                uid=google_sub,
                defaults={"extra_data": google_data},
            )

        refresh = RefreshToken.for_user(user)
        logger.info("google_social_login email=%s created=%s", _mask_email(email), created)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": UserSerializer(user).data,
            }
        )
