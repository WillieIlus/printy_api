"""Queue 12h reminders and 24h escalations for every open managed job.

Idempotent: safe to run on a cron. Uses jobs.stage_notifications.
"""

from django.core.management.base import BaseCommand

from jobs.models import ManagedJob
from jobs.stage_notifications import ensure_stage_reminders


class Command(BaseCommand):
    help = "Queue stage-owner reminders and escalations for open managed jobs."

    def handle(self, *args, **options):
        total = {"reminder": 0, "escalation": 0}
        for job in ManagedJob.objects.exclude(status__in=["completed", "cancelled", "delivered"]):
            counts = ensure_stage_reminders(job)
            for key in total:
                total[key] += counts.get(key, 0)
        self.stdout.write(f"reminders={total['reminder']} escalations={total['escalation']}")
