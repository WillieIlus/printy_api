"""seed_workflow - (re)seed the workflow module's manager and printer roster."""
from django.core.management.base import BaseCommand

from workflow.views import seed_workflow


class Command(BaseCommand):
    help = "Seed (or re-seed) the workflow module: managers and printers only (jobs are never seeded)."

    def handle(self, *args, **options):
        seed_workflow()
        self.stdout.write(self.style.SUCCESS("Workflow seeded: managers and printers restored to canonical state (no jobs seeded)."))