from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Set the django.contrib.sites domain/name from SITE_DOMAIN / SITE_NAME env vars."

    def handle(self, *args, **options):
        from django.contrib.sites.models import Site

        domain = getattr(settings, "SITE_DOMAIN", "printy.ke")
        name = getattr(settings, "SITE_NAME", "Printyke")
        site, created = Site.objects.get_or_create(pk=settings.SITE_ID)
        site.domain = domain
        site.name = name
        site.save()
        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{verb} site #{settings.SITE_ID}: {name} ({domain})"))
