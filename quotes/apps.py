from django.apps import AppConfig


class QuotesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'quotes'
    verbose_name = 'Quotes'

    def ready(self):
        # Register the verified-payment -> transaction receiver (law 10.2(b)).
        from quotes import signals  # noqa: F401
