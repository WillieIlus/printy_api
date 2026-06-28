from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.settings import api_settings

from .models import User, UserProfile
from .services.capabilities import capability_keys, get_account_capabilities, normalize_capability_overrides
from .services.roles import (
    assign_role,
    build_auth_role_payload,
    get_public_assignable_roles,
    normalize_role_value,
    resolve_dashboard_role,
    set_account_role,
)

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
    normalized = normalize_role_value(value)
    if normalized not in get_public_assignable_roles():
        raise serializers.ValidationError("Role must be one of: client, partner, production.")
    return normalized


class UserSerializer(serializers.ModelSerializer):
    """User profile plus persisted dashboard fields."""

    is_email_verified = serializers.SerializerMethodField()
    capabilities = serializers.SerializerMethodField()
    dashboard_role = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField()
    primary_role = serializers.SerializerMethodField()
    home_route = serializers.SerializerMethodField()
    can_access_admin_dashboard = serializers.SerializerMethodField()
    can_access_client_dashboard = serializers.SerializerMethodField()
    can_access_partner_dashboard = serializers.SerializerMethodField()
    can_access_production_dashboard = serializers.SerializerMethodField()
    bio = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    avatar = serializers.CharField(required=False, allow_blank=True, allow_null=True)
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
            "partner_profile_enabled",
            "capability_overrides",
            "capabilities",
            "dashboard_role",
            "home_route",
            "can_access_admin_dashboard",
            "can_access_client_dashboard",
            "can_access_partner_dashboard",
            "can_access_production_dashboard",
            "preferred_language",
            "is_email_verified",
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
        read_only_fields = ["id", "email", "is_active", "is_staff", "is_superuser", "date_joined", "last_login"]

    def get_is_email_verified(self, instance):
        from allauth.account.models import EmailAddress

        return EmailAddress.objects.filter(
            user=instance,
            email__iexact=instance.email,
            primary=True,
            verified=True,
        ).exists()

    def get_capabilities(self, instance):
        return get_account_capabilities(instance)

    def get_dashboard_role(self, instance):
        return resolve_dashboard_role(instance)

    def get_roles(self, instance):
        return build_auth_role_payload(instance)["roles"]

    def get_primary_role(self, instance):
        return build_auth_role_payload(instance)["primary_role"]

    def get_home_route(self, instance):
        return build_auth_role_payload(instance)["home_route"]

    def get_can_access_client_dashboard(self, instance):
        return build_auth_role_payload(instance)["can_access_client_dashboard"]

    def get_can_access_admin_dashboard(self, instance):
        return build_auth_role_payload(instance)["can_access_admin_dashboard"]

    def get_can_access_partner_dashboard(self, instance):
        return build_auth_role_payload(instance)["can_access_partner_dashboard"]

    def get_can_access_production_dashboard(self, instance):
        return build_auth_role_payload(instance)["can_access_production_dashboard"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        role_payload = build_auth_role_payload(instance)
        data["role"] = role_payload["primary_role"]
        profile = get_or_create_profile(instance)
        for field in PROFILE_FIELDS:
            data[field] = getattr(profile, field) or None
        data["social_links"] = []
        return data

    def validate_role(self, value):
        return _normalize_public_role(value)

    def validate_capability_overrides(self, value):
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("Capability overrides must be an object keyed by capability name.")
        normalized = normalize_capability_overrides(value)
        unknown_keys = sorted(set(value.keys()) - set(capability_keys()))
        if unknown_keys:
            raise serializers.ValidationError(f"Unsupported capability override keys: {', '.join(unknown_keys)}.")
        return normalized

    def update(self, instance, validated_data):
        next_role = validated_data.pop("role", None)
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
        if next_role is not None:
            set_account_role(instance, next_role)
            instance.refresh_from_db(fields=["role"])

        if profile_data:
            profile = get_or_create_profile(instance)
            for field, value in profile_data.items():
                setattr(profile, field, value or "")
            profile.save()

        return instance


class UserCreateSerializer(serializers.ModelSerializer):
    """Serializer for user registration."""

    password = serializers.CharField(write_only=True, min_length=8)
    session_key = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=64)
    guest_session_key = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=64)
    guest_draft_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

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
        ]
        read_only_fields = ["id"]
        extra_kwargs = {
            "role": {"required": False},
            "partner_profile_enabled": {"required": False},
            "capability_overrides": {"required": False},
        }

    def validate_role(self, value):
        return _normalize_public_role(value)

    def validate_capability_overrides(self, value):
        if value in (None, ""):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("Capability overrides must be an object keyed by capability name.")
        normalized = normalize_capability_overrides(value)
        unknown_keys = sorted(set(value.keys()) - set(capability_keys()))
        if unknown_keys:
            raise serializers.ValidationError(f"Unsupported capability override keys: {', '.join(unknown_keys)}.")
        return normalized

    def create(self, validated_data):
        session_key = (
            validated_data.pop("session_key", "")
            or validated_data.pop("guest_session_key", "")
            or ""
        )
        guest_draft_id = validated_data.pop("guest_draft_id", None)
        requested_role = validated_data.pop("role", None) or self.context.get("default_role") or User.Role.CLIENT
        normalized_role = _normalize_public_role(requested_role)
        validated_data["role"] = normalized_role
        if normalized_role == User.Role.PARTNER:
            validated_data["partner_profile_enabled"] = True
        user = User.objects.create_user(**validated_data)
        assign_role(user, normalized_role, source=self.context.get("role_source", "signup"))
        from allauth.account.models import EmailAddress
        from django.conf import settings as django_settings
        email_verification = getattr(django_settings, "ACCOUNT_EMAIL_VERIFICATION", "none")
        verified = email_verification == "none"
        email_address = EmailAddress.objects.create(
            user=user,
            email=user.email,
            primary=True,
            verified=verified,
        )
        if not verified:
            request = self.context.get("request")
            email_address.send_confirmation(request, signup=True)
        self.claimed_guest_draft = None
        if normalized_role == User.Role.CLIENT and session_key:
            from quotes.models import CalculatorDraft, CalculatorDraftContext, CalculatorDraftIntent

            drafts = CalculatorDraft.objects.filter(
                guest_session_key=session_key,
                user__isnull=True,
            ).order_by("-updated_at", "-created_at")
            if guest_draft_id:
                drafts = drafts.filter(pk=guest_draft_id)
            draft = drafts.first()
            if draft is not None:
                draft.user = user
                draft.guest_session_key = ""
                draft.calculator_context = CalculatorDraftContext.CLIENT_DASHBOARD
                draft.intent = CalculatorDraftIntent.SAVE_DRAFT
                draft.save(update_fields=["user", "guest_session_key", "calculator_context", "intent", "updated_at"])
                self.claimed_guest_draft = draft
        return user


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Token serializer that accepts email for login (email-based auth)."""

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
        self.fields["email"] = serializers.EmailField(write_only=True, required=True)

    def validate(self, attrs):
        email = attrs.get("email", "").strip()
        password = attrs.get("password")

        if not email:
            raise serializers.ValidationError({"email": "Email is required."})

        from django.contrib.auth import authenticate

        request = self.context.get("request")
        self.user = authenticate(
            request=request, username=email, password=password
        )
        if self.user is None:
            self.user = User.objects.filter(email=email).first()
            if self.user and not self.user.check_password(password):
                self.user = None

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
