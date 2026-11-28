"""Cron entry point for automatic payouts (Prompt 7 PART 2).

    python manage.py run_auto_payouts            # one pass
    python manage.py run_auto_payouts --limit 50 # at most 50 jobs prepared

Does nothing (and says so) unless AUTO_PAYOUTS_ENABLED is on and the B2C
configuration is complete. Safe to run concurrently: payouts are claimed under
row locks and moved to ``processing`` before any B2C request is sent.
"""

from django.core.management.base import BaseCommand

from jobs.auto_payouts import run_auto_payouts


class Command(BaseCommand):
    help = "Send due automatic payouts through Daraja B2C (disabled unless AUTO_PAYOUTS_ENABLED)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="Maximum number of jobs to prepare this pass.")

    def handle(self, *args, **options):
        summary = run_auto_payouts(limit=options.get("limit"))
        if not summary.get("enabled"):
            self.stdout.write("Automatic payouts are disabled (AUTO_PAYOUTS_ENABLED is off); nothing sent.")
            return
        if summary.get("config_errors"):
            self.stderr.write("Automatic payouts are misconfigured; nothing sent:")
            for error in summary["config_errors"]:
                self.stderr.write(f"  - {error}")
            return
        self.stdout.write(
            f"jobs_checked={summary.get('jobs_checked', 0)} sent={summary['sent']} "
            f"timeouts={summary.get('timeouts', 0)} cancelled={summary.get('cancelled', 0)} "
            f"outcomes={summary['outcomes']}"
        )
