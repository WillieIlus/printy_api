"""Subscription admin."""
from django.contrib import admin
from .models import MpesaStkRequest, Payment, Subscription, SubscriptionPlan


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ["name", "price", "billing_period"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ["shop", "plan", "status", "period_start", "period_end", "next_billing_date"]


@admin.register(MpesaStkRequest)
class MpesaStkRequestAdmin(admin.ModelAdmin):
    list_display = ["checkout_request_id", "shop", "plan", "amount", "status", "created_at"]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["subscription", "amount", "method", "status", "receipt_number", "created_at"]
