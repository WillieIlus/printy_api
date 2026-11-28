"""Recover lost M-Pesa STK confirmations for canonical payments (audit #1c).

Run it from cron every few minutes, e.g.::

    */5 * * * * cd /srv/printy_api && venv/bin/python manage.py mpesa_recover_payments

It asks Daraja (STK Query) about prompts whose callback never arrived and
applies the answer through the same state machine as a verified callback.
A bare query "success" without receipt/amount becomes needs_review, never paid.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from payments.models import Payment
from payments.recovery import recover_payment, recover_stale_payments, stale_payment_ids


class Command(BaseCommand):
    help = "Query Daraja for STK prompts whose callback never arrived and apply the answer safely."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50, help="Maximum payments to query in this run (default 50).")
        parser.add_argument(
            "--older-than-seconds",
            type=int,
            default=None,
            help="Only query prompts sent at least this long ago (default MPESA_STK_IN_FLIGHT_SECONDS).",
        )
        parser.add_argument(
            "--max-age-hours",
            type=int,
            default=None,
            help="Ignore prompts older than this (default MPESA_RECOVERY_MAX_AGE_HOURS).",
        )
        parser.add_argument("--payment-id", type=int, default=None, help="Recover one payment only.")
        parser.add_argument("--dry-run", action="store_true", help="List the payments that would be queried; call nothing.")

    def handle(self, *args, **options):
        older_than = None if options["older_than_seconds"] is None else timedelta(seconds=options["older_than_seconds"])
        max_age = None if options["max_age_hours"] is None else timedelta(hours=options["max_age_hours"])
        if options["limit"] < 1:
            raise CommandError("--limit must be at least 1.")

        if options["dry_run"]:
            ids = stale_payment_ids(older_than=older_than, max_age=max_age)[: options["limit"]]
            for payment_id in ids:
                self.stdout.write(f"[dry-run] would query payment {payment_id}")
            self.stdout.write(self.style.SUCCESS(f"dry_run=True candidates={len(ids)}"))
            return

        if options["payment_id"] is not None:
            try:
                result = recover_payment(
                    options["payment_id"], older_than=older_than or timedelta(0), max_age=max_age, respect_cooldown=False
                )
            except Payment.DoesNotExist:
                raise CommandError(f"Payment {options['payment_id']} does not exist.")
            self.stdout.write(self.style.SUCCESS(f"payment={result.payment_id} outcome={result.outcome} status={result.payment_status} {result.detail}".rstrip()))
            return

        run = recover_stale_payments(limit=options["limit"], older_than=older_than, max_age=max_age)
        for result in run.results:
            self.stdout.write(f"payment={result.payment_id} outcome={result.outcome} status={result.payment_status}")
        counts = " ".join(f"{key}={value}" for key, value in sorted(run.counts().items())) or "nothing_to_do"
        stopped = f" stopped={run.stopped_reason}" if run.stopped_reason else ""
        self.stdout.write(self.style.SUCCESS(f"{counts}{stopped}"))
