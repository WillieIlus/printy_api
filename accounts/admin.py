from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User, UserProfile, UserSocialLink


@admin.register(User)
class UserAdmin(UserAdmin):
    list_display = ["email", "name", "role", "is_staff", "is_active", "date_joined"]
    list_filter = ["role", "is_staff", "is_active"]
    search_fields = ["email", "name", "first_name", "last_name"]
    ordering = ["email"]
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


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ["user", "phone", "city", "country", "updated_at"]
    search_fields = ["user__email", "user__name", "phone", "city", "country"]


@admin.register(UserSocialLink)
class UserSocialLinkAdmin(admin.ModelAdmin):
    list_display = ["profile", "platform", "url", "updated_at"]
    search_fields = ["profile__user__email", "platform", "url"]
