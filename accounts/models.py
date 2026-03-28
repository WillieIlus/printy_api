"""
User model for Printy API.
Email as USERNAME_FIELD for allauth compatibility.
"""
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

from common.models import TimeStampedModel


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
        CLIENT = "client", "Client"
        SHOP_OWNER = "shop_owner", "Shop Owner"
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
    def is_shop_owner_role(self) -> bool:
        return self.role == self.Role.SHOP_OWNER

    @property
    def is_staff_role(self) -> bool:
        return self.role == self.Role.STAFF


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

    class Meta:
        verbose_name = "User profile"
        verbose_name_plural = "User profiles"

    def __str__(self):
        return f"Profile for {self.user.email}"


class UserSocialLink(TimeStampedModel):
    """Social links attached to a user's profile."""

    profile = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="social_links",
    )
    platform = models.CharField(max_length=50)
    url = models.URLField(max_length=500)

    class Meta:
        verbose_name = "User social link"
        verbose_name_plural = "User social links"
        ordering = ["id"]

    def __str__(self):
        return f"{self.platform}: {self.url}"
