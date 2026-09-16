from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Group
from django.contrib.sites.models import Site

try:
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken
except Exception:
    EmailAddress = SocialAccount = SocialApp = SocialToken = None

try:
    from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
except Exception:
    BlacklistedToken = OutstandingToken = None

from .models import User, UserProfile


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
        "phone",
        "city",
        "country",
        "broker_profile_active",
        "is_system_account",
        "default_markup_rate",
        "updated_at",
    ]
    list_filter = ["broker_profile_active", "is_system_account", "country"]
    search_fields = ["user__email", "user__name", "phone", "city", "country"]
    actions = ["activate_broker_profiles", "deactivate_broker_profiles"]

    @admin.action(description="Activate selected broker/partner profiles")
    def activate_broker_profiles(self, request, queryset):
        queryset.update(broker_profile_active=True)

    @admin.action(description="Deactivate selected broker/partner profiles")
    def deactivate_broker_profiles(self, request, queryset):
        queryset.update(broker_profile_active=False)
