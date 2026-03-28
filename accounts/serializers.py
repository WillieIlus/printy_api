from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.settings import api_settings

from .models import User, UserProfile, UserSocialLink
from .services.roles import get_assignable_roles, set_account_role

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


class UserSocialLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSocialLink
        fields = ["id", "platform", "url", "profile"]
        read_only_fields = ["id", "profile"]


class UserSerializer(serializers.ModelSerializer):
    """User profile plus persisted dashboard fields."""

    bio = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    avatar = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    phone = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    state = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    postal_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    social_links = UserSocialLinkSerializer(many=True, required=False)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "name",
            "first_name",
            "last_name",
            "role",
            "preferred_language",
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
            "social_links",
        ]
        read_only_fields = ["id", "email", "is_active", "is_staff", "is_superuser", "date_joined", "last_login"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        profile = get_or_create_profile(instance)
        for field in PROFILE_FIELDS:
            data[field] = getattr(profile, field) or None
        data["social_links"] = UserSocialLinkSerializer(profile.social_links.all(), many=True).data
        return data

    def validate_role(self, value):
        if value not in get_assignable_roles():
            raise serializers.ValidationError("Role must be one of: client, shop_owner, staff.")
        return value

    def update(self, instance, validated_data):
        social_links_data = validated_data.pop("social_links", None)
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

        if profile_data or social_links_data is not None:
            profile = get_or_create_profile(instance)
            for field, value in profile_data.items():
                setattr(profile, field, value or "")
            profile.save()

            if social_links_data is not None:
                profile.social_links.all().delete()
                UserSocialLink.objects.bulk_create(
                    [
                        UserSocialLink(
                            profile=profile,
                            platform=link.get("platform", "").strip(),
                            url=link.get("url", "").strip(),
                        )
                        for link in social_links_data
                        if link.get("platform") and link.get("url")
                    ]
                )

        return instance


class UserCreateSerializer(serializers.ModelSerializer):
    """Serializer for user registration."""

    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "email", "password", "name", "first_name", "last_name", "role"]
        read_only_fields = ["id"]

    def validate_role(self, value):
        if value not in get_assignable_roles():
            raise serializers.ValidationError("Role must be one of: client, shop_owner, staff.")
        return value

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Token serializer that accepts email for login (email-based auth)."""

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

        refresh = self.get_token(self.user)
        data = {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

        if api_settings.UPDATE_LAST_LOGIN:
            from django.contrib.auth.models import update_last_login

            update_last_login(None, self.user)

        return data
