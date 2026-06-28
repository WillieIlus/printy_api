from django.core.management.base import BaseCommand
from django.utils import timezone

from quotes.choices import QuoteOfferStatus
from quotes.guardrails import expire_quote
from quotes.models import Quote


class Command(BaseCommand):
    help = "Marks expired client-facing quotes as expired."

    def handle(self, *args, **options):
        now = timezone.now()
        expired_count = 0
        queryset = Quote.objects.select_related("quote_request", "created_by", "sent_to_client_by").filter(
            status__in=[QuoteOfferStatus.SENT, QuoteOfferStatus.REVISED, QuoteOfferStatus.MODIFIED],
            expires_at__isnull=False,
            expires_at__lt=now,
        )
        for quote in queryset.iterator():
            if expire_quote(quote=quote, now=now):
                expired_count += 1

        self.stdout.write(self.style.SUCCESS(f"Expired {expired_count} quote(s)."))
