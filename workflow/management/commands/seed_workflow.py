"""seed_workflow - (re)seed the workflow module to its canonical demo state."""
from django.core.management.base import BaseCommand

from workflow.views import seed_workflow


class Command(BaseCommand):
    help = "Seed (or re-seed) the workflow module: managers, printers and the canonical INITIAL_JOBS."

    def handle(self, *args, **options):
        seed_workflow()
        self.stdout.write(self.style.SUCCESS("Workflow seeded: managers, printers and jobs restored to canonical state."))