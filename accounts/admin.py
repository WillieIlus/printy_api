from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.contrib.sites.models import Site
from django.utils import timezone

try:
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
except Exception:
    EmailAddress = SocialAccount = SocialApp = SocialToken = None

try:
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
except Exception:
    BlacklistedToken = OutstandingToken = None

from .models import ManagerLock, User, UserProfile


for model in [
    Site,
    Group,
    SocialAccount,
    SocialApp,
    SocialToken,
    EmailAddress,
    BlacklistedToken,
    OutstandingToken,
]:
    if model is None:
        continue
    try:
        admin.site.unregister(model)
    except admin.sites.NotRegistered:
        pass


if EmailAddress is not None:
    class EmailAddressInline(admin.TabularInline):
        model = EmailAddress
        extra = 0
        fields = ("email", "primary", "verified")


    @admin.register(EmailAddress)
    class EmailAddressAdmin(admin.ModelAdmin):
        list_display = ("email", "user", "primary", "verified")
        list_filter = ("verified", "primary")
        search_fields = ("email", "user__email", "user__name")
        autocomplete_fields = ("user",)
        actions = ("mark_verified", "mark_unverified")

        @admin.action(description="Mark selected email addresses as verified")
        def mark_verified(self, request, queryset):
            queryset.update(verified=True)

        @admin.action(description="Mark selected email addresses as unverified")
        def mark_unverified(self, request, queryset):
            queryset.update(verified=False)


@admin.register(User)
class UserAdmin(UserAdmin):
    list_display = ["email", "name", "role", "email_verified", "is_staff", "is_active", "date_joined"]
    list_filter = ["role", "is_staff", "is_active"]
    search_fields = ["email", "name", "first_name", "last_name"]
    ordering = ["email"]
    inlines = [EmailAddressInline] if EmailAddress is not None else []
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal", {"fields": ("name", "first_name", "last_name", "role", "preferred_language")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"fields": ("email", "password1", "password2")}),
        ("Personal", {"fields": ("name", "first_name", "last_name", "role", "preferred_language")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser")}),
    )

    @admin.display(boolean=True, description="Email verified")
    def email_verified(self, obj):
        if EmailAddress is None:
            return False
        return EmailAddress.objects.filter(user=obj, email__iexact=obj.email, primary=True, verified=True).exists()


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "public_display_name",
        "phone",
        "city",
        "country",
        "broker_profile_active",
        "manager_verified",
        "manager_verified_at",
        "accepts_new_jobs",
        "max_concurrent_jobs",
        "manager_public_opt_in",
        "manager_speciality",
        "is_system_account",
        "default_markup_rate",
        "updated_at",
    ]
    list_filter = [
        "broker_profile_active",
        "manager_verified",
        "accepts_new_jobs",
        "manager_public_opt_in",
        "is_system_account",
        "country",
    ]
    search_fields = ["user__email", "user__name", "public_display_name", "phone", "city", "country"]
    # LAW 2: a client's origin manager is set once when the client is created and
    # is never editable afterwards, so the admin shows it read-only. The public
    # id, verification timestamp and rotation stamp are derived/staff-only.
    readonly_fields = ["origin_manager", "public_id", "manager_verified_at", "last_assigned_at"]
    actions = ["activate_broker_profiles", "deactivate_broker_profiles"]

    def save_model(self, request, obj, form, change):
        """Keep the verification timestamp in step with the staff toggle."""
        if obj.manager_verified:
            if obj.manager_verified_at is None:
                obj.manager_verified_at = timezone.now()
        else:
            obj.manager_verified_at = None
        super().save_model(request, obj, form, change)

    @admin.action(description="Activate selected broker/partner profiles")
    def activate_broker_profiles(self, request, queryset):
        queryset.update(broker_profile_active=True)

    @admin.action(description="Deactivate selected broker/partner profiles")
    def deactivate_broker_profiles(self, request, queryset):
        queryset.update(broker_profile_active=False)


@admin.register(ManagerLock)
class ManagerLockAdmin(admin.ModelAdmin):
    list_display = ["buyer", "manager", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["buyer__email", "buyer__name", "manager__email", "manager__name"]
    autocomplete_fields = ["buyer", "manager"]
    readonly_fields = ["created_at", "updated_at"]
