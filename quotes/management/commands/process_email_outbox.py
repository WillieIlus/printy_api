from django.core.management.base import BaseCommand
from django.db import connection, transaction

from quotes.messaging import process_email_outbox_entry
from quotes.models import EmailOutbox



class Command(BaseCommand):
    help = "Send pending quote email outbox entries."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100, help="Maximum outbox rows to process in this run.")

    def handle(self, *args, **options):
        limit = max(1, int(options.get("limit") or 100))
        sent_count = 0
        failed_count = 0
        permanent_failed_count = 0

        ready_ids = list(self._ready_queryset().values_list("id", flat=True)[:limit])
        for outbox_id in ready_ids:
            result = self._process_entry(outbox_id)
            if result is None:
                continue
            if result == "sent":
                sent_count += 1
            elif result == "permanently_failed":
                permanent_failed_count += 1
            else:
                failed_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed email outbox: sent={sent_count}, failed={failed_count}, permanently_failed={permanent_failed_count}."
            )
        )

    def _ready_queryset(self):
        return EmailOutbox.objects.filter(
            status__in=[EmailOutbox.Status.PENDING, EmailOutbox.Status.FAILED],
            attempts_count__lt=EmailOutbox.MAX_ATTEMPTS,
        ).order_by("created_at", "id")

    def _process_entry(self, outbox_id):
        with transaction.atomic():
            queryset = self._ready_queryset().filter(pk=outbox_id)
            if connection.features.has_select_for_update:
                queryset = queryset.select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked)
            outbox_entry = queryset.first()
            if outbox_entry is None:
                return None
            return process_email_outbox_entry(outbox_entry)


