from django.core.management.base import BaseCommand

from quotes.pending_artwork import purge_expired_pending_artwork


class Command(BaseCommand):
    help = "Delete expired guest artwork uploads."

    def handle(self, *args, **options):
        deleted = purge_expired_pending_artwork()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} expired pending artwork upload(s)."))
