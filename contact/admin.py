from django.contrib import admin

from .models import ContactMessage


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "email", "topic", "created_at"]
    list_filter = ["topic"]
    search_fields = ["name", "email", "message"]