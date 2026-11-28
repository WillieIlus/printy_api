import logging

from rest_framework import serializers
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.settings import api_settings

from django.core.exceptions import ValidationError as DjangoValidationError

from .models import User, UserProfile
from .services.capabilities import get_account_capabilities
from payments.services import normalize_mpesa_phone
from .services.roles import (
    assign_role,
    build_auth_role_payload,
    get_public_assignable_roles,
    get_request_active_role,
    normalize_role_value,
)

logger = logging.getLogger("api.auth")


def _mask_email_for_log(email: str) -> str:
    """Return a non-identifying form of an email for log lines.

    Never log the raw address: only the first one or two local-part
    characters and the domain are kept.
    """
    value = (email or "").strip()
    if "@" not in value:
        return "***"
    local, _, domain = value.partition("@")
    keep = local[:1] if len(local) <= 2 else local[:2]
    return f"{keep}***@{domain}"


def _mask_phone_for_log(phone: str) -> str:
    """Return a non-identifying tail of a normalised phone for log lines."""
    value = str(phone or "").strip()
    return f"{value[:4]}****{value[-2:]}" if len(value) > 6 else "***"

PROFILE_FIELDS = (
    "bio",
    "avatar",
    "phone",
    "address",
    "city",
    "state",
    "country",
    "postal_code",
)


def get_or_create_profile(user: User) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def _normalize_public_role(value: str) -> str:
    """Audit #3: sign-up may only pick a role from the self-service allowlist."""
    normalized = normalize_role_value(value)
    allowed = get_public_assignable_roles()
    if normalized not in allowed:
        raise serializers.ValidationError(
            f"Role must be one of: {', '.join(sorted(allowed))}."
        )
    return normalized


# Audit #3: privilege-bearing account fields. Self-service endpoints
# (PATCH /api/auth/me/, /api/users/me/, registration) expose them read-only;
# only admin surfaces (Django admin, /api/admin/...) may change them.
PRIVILEGED_USER_FIELDS = ("role", "partner_profile_enabled", "capability_overrides")


class UserSerializer(serializers.ModelSerializer):
    """User profile plus persisted dashboard fields."""

    is_email_verified = serializers.SerializerMethodField()
    is_phone_verified = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()
    shop_name = serializers.SerializerMethodField()
    dashboard_role = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    primary_role = serializers.SerializerMethodField()
    active_role = serializers.SerializerMethodField()
    active_dashboard_role = serializers.SerializerMethodField()
    home_route = serializers.SerializerMethodField()
    can_access_admin_dashboard = serializers.SerializerMethodField()
    can_access_client_dashboard = serializers.SerializerMethodField()
    can_access_partner_dashboard = serializers.SerializerMethodField()
    can_access_production_dashboard = serializers.SerializerMethodField()
    bio = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    avatar = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    state = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    postal_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "name",
            "first_name",
            "last_name",
            "role",
            "roles",
            "primary_role",
            "active_role",
            "active_dashboard_role",
            "partner_profile_enabled",
            "capability_overrides",
            "capabilities",
            "dashboard_role",
            "home_route",
            "shop_name",
            "can_access_admin_dashboard",
            "can_access_client_dashboard",
            "can_access_partner_dashboard",
            "can_access_production_dashboard",
            "preferred_language",
            "is_email_verified",
            "is_phone_verified",
            "is_active",
            "is_staff",
            "is_superuser",
            "date_joined",
            "last_login",
            "bio",
            "avatar",
            "phone",
            "address",
            "city",
            "state",
            "country",
            "postal_code",
        ]
        read_only_fields = [
            "id",
            "is_active",
            "is_staff",
            "is_superuser",
            "date_joined",
            "last_login",
            *PRIVILEGED_USER_FIELDS,
        ]

    def get_is_email_verified(self, instance):
        from allauth.account.models import EmailAddress

        if not instance.email:
            return False
        return EmailAddress.objects.filter(
            user=instance,
            email__iexact=instance.email,
            primary=True,
            verified=True,
        ).exists()

    def get_is_phone_verified(self, instance):
        profile = get_or_create_profile(instance)
        return bool(profile.phone and profile.phone_verified_at)

    def validate_email(self, value):
        """Email is optional and can only be added while the account has none.

        An account created phone-first (manager-assisted onboarding) starts
        without an email. It may set one later; an account that already has an
        email can never have it changed through this endpoint.
        """
        value = (value or "").strip().lower()
        if not value:
            return None
        if self.instance is not None and self.instance.email:
            if value != self.instance.email.lower():
                raise serializers.ValidationError(
                    "Email cannot be changed once set. Contact support for help."
                )
            return self.instance.email
        # Audit #4b: an address already used by another account (in any case)
        # is refused. Before, a case variant created a case-duplicate (which
        # then blocks Google sign-in for the real owner with a 409) and an
        # exact duplicate hit the unique constraint as a 500.
        others = User.objects.filter(email__iexact=value)
        if self.instance is not None:
            others = others.exclude(pk=self.instance.pk)
        if others.exists():
            raise serializers.ValidationError("A user with that email already exists.")
        return value

    def get_capabilities(self, instance):
        return get_account_capabilities(instance)

    def get_shop_name(self, instance):
        shop = instance.owned_shops.order_by("id").first()
        return shop.name if shop else ""

    def _role_payload(self, instance):
        request = self.context.get("request")
        return build_auth_role_payload(instance, get_request_active_role(request))

    def get_dashboard_role(self, instance):
        return self._role_payload(instance)["dashboard_role"]

    def get_roles(self, instance):
        return self._role_payload(instance)["roles"]

    def get_primary_role(self, instance):
        return self._role_payload(instance)["primary_role"]

    def get_active_role(self, instance):
        return self._role_payload(instance)["active_role"]

    def get_active_dashboard_role(self, instance):
        return self._role_payload(instance)["active_dashboard_role"]

    def get_home_route(self, instance):
        return self._role_payload(instance)["home_route"]

    def get_can_access_client_dashboard(self, instance):
        return self._role_payload(instance)["can_access_client_dashboard"]

    def get_can_access_admin_dashboard(self, instance):
        return self._role_payload(instance)["can_access_admin_dashboard"]

    def get_can_access_partner_dashboard(self, instance):
        return self._role_payload(instance)["can_access_partner_dashboard"]

    def get_can_access_production_dashboard(self, instance):
        return self._role_payload(instance)["can_access_production_dashboard"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        role_payload = self._role_payload(instance)
        data["role"] = role_payload["primary_role"]
        profile = get_or_create_profile(instance)
        for field in PROFILE_FIELDS:
            data[field] = getattr(profile, field) or None
        data["social_links"] = []
        return data

    def validate_phone(self, value):
        """Normalise here so a bad number is a 400, not a 500 from the save."""
        raw = str(value or "").strip()
        if raw:
            from accounts.services.phone import normalize_phone

            normalize_phone(raw)
        return raw

    def update(self, instance, validated_data):
        # Audit #3: role / partner_profile_enabled / capability_overrides are
        # read-only here; drop them defensively in case a subclass re-enables them.
        for field in PRIVILEGED_USER_FIELDS:
            validated_data.pop(field, None)
        new_email = validated_data.pop("email", serializers.empty)
        if new_email is not serializers.empty and new_email and not instance.email:
            instance.email = new_email
            instance.save(update_fields=["email", "updated_at"])
            self._provision_email_address(instance, new_email)
        phone_value = validated_data.pop("phone", serializers.empty)
        profile_data = {
            field: validated_data.pop(field)
            for field in PROFILE_FIELDS
            if field in validated_data
        }

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if "name" not in validated_data and (
            "first_name" in validated_data or "last_name" in validated_data
        ):
            instance.name = " ".join(
                part for part in [instance.first_name.strip(), instance.last_name.strip()] if part
            )
        instance.save()

        if profile_data:
            profile = get_or_create_profile(instance)
            for field, value in profile_data.items():
                setattr(profile, field, value or "")
            profile.save()

        if phone_value is not serializers.empty:
            # Audit #3: every phone write goes through set_profile_phone so the
            # normalised form is kept and a changed number loses its verification.
            from accounts.services.phone import set_profile_phone

            set_profile_phone(instance, phone_value or "", verified=False)

        return instance

    def _provision_email_address(self, instance, email: str) -> None:
        """Attach an email to a phone-first account and request verification."""
        from allauth.account.models import EmailAddress
        from django.conf import settings as django_settings

        email_verification = getattr(django_settings, "ACCOUNT_EMAIL_VERIFICATION", "none")
        verified = email_verification == "none"
        email_address, _ = EmailAddress.objects.update_or_create(
            user=instance,
            email=email,
            defaults={"primary": True, "verified": verified},
        )
        if not verified:
            request = self.context.get("request")
            try:
                email_address.send_confirmation(request)
            except Exception:
                logger.exception(
                    "add_email_activation_failed user_id=%s",
                    instance.pk,
                )


class UserCreateSerializer(serializers.ModelSerializer):
    """Serializer for user registration."""

    password = serializers.CharField(write_only=True, min_length=8)
    email = serializers.EmailField(
        required=True,
        validators=[
            UniqueValidator(
                queryset=User.objects.all(),
                lookup="iexact",
                message="A user with that email already exists.",
            )
        ],
    )
    session_key = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=64)
    guest_session_key = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=64)
    guest_draft_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    shop_name = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=255)
    phone = serializers.CharField(write_only=True, required=False, allow_blank=True, allow_null=True, max_length=20)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "password",
            "name",
            "first_name",
            "last_name",
            "role",
            "partner_profile_enabled",
            "capability_overrides",
            "session_key",
            "guest_session_key",
            "guest_draft_id",
            "shop_name",
            "phone",
        ]
        # Audit #3: partner_profile_enabled / capability_overrides are never
        # self-service. ``role`` stays writable but only from the allowlist.
        read_only_fields = ["id", "partner_profile_enabled", "capability_overrides"]
        extra_kwargs = {
            "role": {"required": False},
        }

    def validate_email(self, value):
        return (value or "").strip().lower()

    def validate_role(self, value):
        return _normalize_public_role(value)

    def validate_phone(self, value):
        if value in (None, ""):
            return ""
        try:
            return normalize_mpesa_phone(str(value))
        except DjangoValidationError:
            raise serializers.ValidationError(
                "Enter a valid Kenyan M-Pesa phone number, for example 2547XXXXXXXX."
            )

    def create(self, validated_data):
        phone = validated_data.pop("phone", "")
        session_key = (
            validated_data.pop("session_key", "")
            or validated_data.pop("guest_session_key", "")
            or ""
        )
        guest_draft_id = validated_data.pop("guest_draft_id", None)
        # Audit #3: printer applicants no longer get a shop (and with it the
        # production role) at sign-up; they create it later via POST /api/shops/,
        # which starts private until an admin approves it. Accepted and ignored
        # so older clients that still send it do not get a 400.
        validated_data.pop("shop_name", "")
        for field in PRIVILEGED_USER_FIELDS[1:]:
            validated_data.pop(field, None)
        requested_role = validated_data.pop("role", None) or self.context.get("default_role") or User.Role.CLIENT
        normalized_role = _normalize_public_role(requested_role)
        validated_data["role"] = normalized_role
        if normalized_role == User.Role.PARTNER:
            validated_data["partner_profile_enabled"] = True
        user = User.objects.create_user(**validated_data)
        assign_role(user, normalized_role, source=self.context.get("role_source", "signup"))
        if normalized_role == User.Role.PARTNER:
            # Manager applicant: explicitly unverified until Printy staff decide.
            UserProfile.objects.update_or_create(
                user=user,
                defaults={"manager_verified": False, "manager_verified_at": None},
            )
        from allauth.account.models import EmailAddress
        from django.conf import settings as django_settings
        email_verification = getattr(django_settings, "ACCOUNT_EMAIL_VERIFICATION", "none")
        verified = email_verification == "none"
        email_address = EmailAddress.objects.create(
            user=user,
            email=(user.email or "").lower(),
            primary=True,
            verified=verified,
        )
        self.activation_email_sent = False
        if not verified:
            request = self.context.get("request")
            try:
                email_address.send_confirmation(request, signup=True)
                self.activation_email_sent = True
            except Exception:
                logger.exception(
                    "registration_activation_email_failed user_id=%s",
                    user.pk,
                )
        if phone:
            from accounts.services.phone import set_profile_phone

            set_profile_phone(user, phone, verified=False)
            # LAW 12: signing up with a phone a manager already captured adopts that
            # client record (same origin, same quotes/jobs/payments) instead of
            # starting a second one.
            from accounts.services.client_invites import link_client_identity

            try:
                link_client_identity(user, phone)
            except Exception:  # linking is additive; never fail the sign-up for it
                logger.exception("client_identity_link_failed user_id=%s", user.pk)
        self.claimed_guest_draft = None
        if session_key:
            from quotes.models import CalculatorDraft, CalculatorDraftContext, CalculatorDraftIntent

            drafts = CalculatorDraft.objects.filter(
                guest_session_key=session_key,
                user__isnull=True,
            ).order_by("-updated_at", "-created_at")
            if guest_draft_id:
                drafts = drafts.filter(pk=guest_draft_id)
            is_buyer_signup = (
                normalized_role == User.Role.CLIENT
                and self.context.get("signup_intent") != "printer_applicant"
            )
            if is_buyer_signup:
                draft = drafts.first()
                if draft is not None:
                    draft.user = user
                    draft.guest_session_key = ""
                    draft.calculator_context = CalculatorDraftContext.CLIENT_DASHBOARD
                    draft.intent = CalculatorDraftIntent.SAVE_DRAFT
                    draft.save(update_fields=["user", "guest_session_key", "calculator_context", "intent", "updated_at"])
                    self.claimed_guest_draft = draft
            else:
                # Law 9.4: guest drafts and the guest manager selections they
                # carry are claimed only into buyer accounts. A manager or
                # printer sign-up ignores and clears them.
                drafts.delete()
        return user


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Token serializer that accepts email or a verified phone number."""

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        role_payload = build_auth_role_payload(user)
        token["primary_role"] = role_payload["primary_role"]
        token["roles"] = role_payload["roles"]
        token["home_route"] = role_payload["home_route"]
        return token

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop("username", None)
        self.fields["email"] = serializers.EmailField(write_only=True, required=False, allow_blank=True)
        self.fields["phone"] = serializers.CharField(write_only=True, required=False, allow_blank=True)

    def _authenticate_by_phone(self, phone: str, password: str):
        from accounts.services.phone import normalize_phone

        normalized = normalize_phone(phone)
        if not normalized:
            raise serializers.ValidationError({"phone": "Enter a valid phone number."})

        verified_profile = (
            UserProfile.objects.select_related("user")
            .filter(phone_normalized=normalized, phone_verified_at__isnull=False)
            .first()
        )
        if verified_profile is not None:
            if verified_profile.user.check_password(password):
                return verified_profile.user
            return None

        # Self-signup stores the phone without verifying it. The number can
        # still identify the account when that account already has a trusted
        # email identity, so phone and email sign-in resolve to the same user.
        # Ambiguous matches are never resolved by guesswork.
        candidates = list(
            UserProfile.objects.select_related("user")
            .filter(phone_normalized=normalized, phone_verified_at__isnull=True)
            .exclude(user__email__isnull=True)
            .exclude(user__email="")
        )
        if len(candidates) != 1:
            if len(candidates) > 1:
                logger.warning(
                    "login_phone_collision phone=%s matches=%s",
                    _mask_phone_for_log(normalized),
                    len(candidates),
                )
            return None
        profile = candidates[0]
        if not self._unverified_phone_login_allowed(profile.user):
            return None
        if not profile.user.check_password(password):
            return None
        return profile.user

    @staticmethod
    def _unverified_phone_login_allowed(user) -> bool:
        """Let an unverified phone resolve an account with a trusted email.

        When email verification is mandatory the account's primary email must
        already be verified. When it is disabled the email is trusted by
        configuration, matching how email sign-in is treated.
        """
        from allauth.account.models import EmailAddress
        from django.conf import settings as django_settings

        if getattr(django_settings, "ACCOUNT_EMAIL_VERIFICATION", "none") != "mandatory":
            return True
        email_obj = EmailAddress.objects.filter(user=user, primary=True).first()
        return bool(email_obj and email_obj.verified)

    def validate(self, attrs):
        email = (attrs.get("email") or "").strip().lower()
        phone = (attrs.get("phone") or "").strip()
        password = attrs.get("password")

        if not email and not phone:
            raise serializers.ValidationError(
                {"detail": "Provide an email or a verified phone number."}
            )

        from django.contrib.auth import authenticate

        request = self.context.get("request")
        if email:
            self.user = authenticate(
                request=request, username=email, password=password
            )
            if self.user is None:
                # Accounts written before email case normalisation may still
                # hold the original local-part case, so fall back to a
                # case-insensitive lookup. Never guess between ambiguous rows.
                candidates = list(User.objects.filter(email__iexact=email)[:2])
                if len(candidates) == 1:
                    candidate = candidates[0]
                    if candidate.check_password(password):
                        self.user = candidate
                elif len(candidates) > 1:
                    logger.warning(
                        "login_email_collision email=%s matches=%s",
                        _mask_email_for_log(email),
                        len(candidates),
                    )
                    self.user = None
        else:
            self.user = self._authenticate_by_phone(phone, password)

        if not api_settings.USER_AUTHENTICATION_RULE(self.user):
            raise serializers.ValidationError(
                {"detail": "No active account found with the given credentials."}
            )

        from allauth.account.models import EmailAddress
        from django.conf import settings as django_settings
        if getattr(django_settings, "ACCOUNT_EMAIL_VERIFICATION", "none") == "mandatory":
            email_obj = EmailAddress.objects.filter(user=self.user, primary=True).first()
            if email_obj and not email_obj.verified:
                raise serializers.ValidationError(
                    {
                        "detail": "Your account exists but needs email verification.",
                        "code": "EMAIL_UNVERIFIED",
                        "email": self.user.email,
                        "resend_available": True,
                    }
                )

        refresh = self.get_token(self.user)
        data = {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user": UserSerializer(self.user).data,
        }

        if api_settings.UPDATE_LAST_LOGIN:
            from django.contrib.auth.models import update_last_login

            update_last_login(None, self.user)

        return data
