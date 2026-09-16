"""
User model for Printy API.
Email as USERNAME_FIELD for allauth compatibility.
"""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

from common.models import TimeStampedModel


def _canonical_role_for_storage(value: str | None) -> str | None:
    mapping = {
        "super_admin": "super_admin",
        "admin": "super_admin",
        "superuser": "super_admin",
        "staff": "super_admin",
        "client": "client",
        "customer": "client",
        "buyer": "client",
        "partner": "partner",
        "broker": "partner",
        "production": "production",
        "shop_owner": "production",
        "printer": "production",
        "production_shop": "production",
    }
    if not value:
        return None
    return mapping.get(str(value).strip().lower())


class UserManager(BaseUserManager):
    """Custom manager for email-based auth."""

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", "super_admin")
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser, TimeStampedModel):
    """
    Custom user with email as primary identifier.
    Alias: AUTH_USER_MODEL = "accounts.User" (same as CustomUser for compatibility).
    """

    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", "Super Admin"
        ADMIN = "admin", "Admin"
        CLIENT = "client", "Client"
        PARTNER = "partner", "Partner"
        PRODUCTION = "production", "Production"
        BROKER = "broker", "Broker"
        SHOP_OWNER = "shop_owner", "Shop Owner"
        PRINTER = "printer", "Printer"
        STAFF = "staff", "Staff"

    username = models.CharField(max_length=150, blank=True, null=True)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CLIENT,
        help_text="Primary account role used by the dashboard UI.",
    )
    partner_profile_enabled = models.BooleanField(
        default=False,
        help_text="Enables future partner/broker capabilities without changing the primary dashboard role yet.",
    )
    capability_overrides = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Optional additive capability overrides. Keys mirror capability names such as "
            "can_manage_clients or can_source_jobs."
        ),
    )
    preferred_language = models.CharField(
        max_length=10,
        blank=True,
        default="en",
        help_text="Preferred language code (en, sw). Used for authenticated requests.",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return self.email

    @property
    def is_client_role(self) -> bool:
        return self.role == self.Role.CLIENT

    @property
    def is_broker_role(self) -> bool:
        return self.role in {self.Role.BROKER, self.Role.PARTNER}

    @property
    def is_shop_owner_role(self) -> bool:
        return self.role in {self.Role.SHOP_OWNER, self.Role.PRODUCTION, self.Role.PRINTER}

    @property
    def is_staff_role(self) -> bool:
        return self.role == self.Role.STAFF

    @property
    def is_hybrid_partner_account(self) -> bool:
        return bool(self.partner_profile_enabled and self.role in {self.Role.SHOP_OWNER, self.Role.PRODUCTION, self.Role.PRINTER, self.Role.STAFF, self.Role.BROKER, self.Role.PARTNER})


class UserProfile(TimeStampedModel):
    """Extended profile fields for dashboard account management."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    bio = models.TextField(blank=True, default="")
    avatar = models.CharField(max_length=500, blank=True, default="")
    phone = models.CharField(max_length=32, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    state = models.CharField(max_length=100, blank=True, default="")
    country = models.CharField(max_length=100, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")
    is_system_account = models.BooleanField(default=False)
    broker_profile_active = models.BooleanField(
        default=True,
        help_text="Manual admin flag for whether this profile can act as an active broker/partner.",
    )
    default_markup_rate = models.DecimalField(max_digits=5, decimal_places=4, default=Decimal("0.0000"))

    class Meta:
        verbose_name = "User profile"
        verbose_name_plural = "User profiles"

    def __str__(self):
        return f"Profile for {self.user.email}"


