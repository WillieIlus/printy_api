"""Reconcile M-Pesa payments whose callback never arrived.

Daraja occasionally drops a callback. This command asks Daraja directly about
every payment still pending past its window, so nothing sits in `pending`
forever and the frontend card never shows a permanent spinner.

Run it from cron or celery-beat:
    */10 * * * * python manage.py mpesa_reconcile
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from mpesa_payments.models import MpesaPayment, MpesaPaymentStatus
from mpesa_payments.services import MpesaError, query_stk_status

logger = logging.getLogger("payments")

# Daraja's own STK prompt expires after ~60s; allow generous network slack.
PENDING_WINDOW_MINUTES = 10
# Beyond this, Daraja will no longer answer for the session — give up asking.
GIVE_UP_AFTER_HOURS = 24


class Command(BaseCommand):
    help = "Query Daraja for M-Pesa payments stuck in pending, and cancel stale ones."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report only, change nothing.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()
        window_start = now - timedelta(minutes=PENDING_WINDOW_MINUTES)
        cutoff = now - timedelta(hours=GIVE_UP_AFTER_HOURS)

        stale = MpesaPayment.objects.filter(
            status=MpesaPaymentStatus.PENDING,
            stk_pushed_at__isnull=False,
            stk_pushed_at__lt=window_start,
        )

        queried = confirmed = cancelled = failed = errors = 0

        for payment in stale.iterator():
            if payment.stk_pushed_at < cutoff:
                if dry_run:
                    self.stdout.write(f"[dry-run] would cancel stale {payment.id}")
                    continue
                payment.mark_cancelled(
                    result_code="TIMEOUT",
                    result_desc="No callback received within the reconciliation window.",
                )
                cancelled += 1
                continue

            try:
                before = payment.status
                if dry_run:
                    self.stdout.write(f"[dry-run] would query {payment.id} ({before})")
                    continue
                query_stk_status(payment)
                queried += 1
                if payment.is_paid or payment.status == MpesaPaymentStatus.NEEDS_REVIEW:
                    confirmed += 1
                elif payment.status == MpesaPaymentStatus.CANCELLED:
                    cancelled += 1
                elif payment.status == MpesaPaymentStatus.FAILED:
                    failed += 1
            except MpesaError as exc:
                errors += 1
                logger.warning("Reconcile query failed payment_id=%s error=%s", payment.id, exc)

        summary = (
            f"queried={queried} resolved={confirmed} cancelled={cancelled} "
            f"failed={failed} errors={errors} dry_run={dry_run}"
        )
        self.stdout.write(self.style.SUCCESS(summary))
        logger.info("mpesa_reconcile %s", summary)
