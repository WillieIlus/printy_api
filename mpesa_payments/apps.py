from django.apps import AppConfig


class MpesaPaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mpesa_payments"
    verbose_name = "M-Pesa Payments"

    def ready(self):
        import mpesa_payments.receivers  # noqa: F401
