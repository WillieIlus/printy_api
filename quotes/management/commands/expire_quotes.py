from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.guardrails import expire_quote
from quotes.models import Quote, QuoteVersion, QuoteVersionOutcome


class Command(BaseCommand):
    help = (
        "Marks expired client-facing quotes and open quote versions as expired "
        "(law 10.2(a))."
    )

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

        version_count = self._expire_stale_versions(now)

        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {expired_count} quote(s) and {version_count} quote version(s)."
            )
        )

    def _expire_stale_versions(self, now) -> int:
        """Mark OPEN versions older than QUOTE_VALIDITY_DAYS as EXPIRED.

        History is kept: only the terminal outcome moves, the version row and
        its snapshot stay forever.
        """
        validity_days = getattr(settings, "QUOTE_VALIDITY_DAYS", None)
        if validity_days is None:
            return 0
        cutoff = now - timedelta(days=validity_days)
        stale = (
            QuoteVersion.objects.filter(
                outcome=QuoteVersionOutcome.OPEN,
                created_at__lte=cutoff,
            )
            .select_related("quote", "quote_request")
            .order_by("pk")
        )
        count = 0
        for version in stale.iterator():
            quote = getattr(version, "quote", None)
            if quote is not None and quote.status in {
                QuoteOfferStatus.SENT,
                QuoteOfferStatus.REVISED,
                QuoteOfferStatus.MODIFIED,
            }:
                quote.status = QuoteOfferStatus.EXPIRED
                quote.save(update_fields=["status", "updated_at"])
            request = getattr(version, "quote_request", None)
            if request is not None and request.status not in {
                QuoteStatus.ACCEPTED,
                QuoteStatus.CANCELLED,
                QuoteStatus.CLOSED,
                QuoteStatus.REJECTED,
                QuoteStatus.EXPIRED,
            }:
                request.status = QuoteStatus.EXPIRED
                request.save(update_fields=["status", "updated_at"])

            version.outcome = QuoteVersionOutcome.EXPIRED
            version.save(update_fields=["outcome", "updated_at"])
            count += 1
        return count
