"""Read-only audit of stored quote splits (audit #6).

Before audit #6 the split could produce a negative Printy fee: a markup below
the tier margin floor meant Printy paid out more than the client was charged
(e.g. production 20,000 + markup 1,000 -> printy_fee -950). New splits now
reject such markups. This command lists any rows already stored with
that problem. It NEVER edits, re-derives or deletes anything; stored
financial snapshots are immutable and need a human decision.

    python manage.py report_financial_split_anomalies
    python manage.py report_financial_split_anomalies --fail-if-any   # exit 1 when found (CI)

Checks, per QuoteFinancialSplit:
  negative_printy_fee      printy_fee < 0
  over_allocated           shop_payout + manager_payout > client_total
  does_not_reconcile       shop_payout + manager_payout + printy_fee != client_total
  negative_payout          shop_payout < 0 or manager_payout < 0
  shop_below_cost          shop_payout < production_cost
  below_current_floor      manager_markup < the active policy's minimum markup
                           (informational: the row may predate the floor)
and per ManagedJob: printy_fee < 0 or broker_payout < 0.

Equivalent SQL for the main check::

    SELECT id, quote_id, production_cost, manager_markup, printy_fee,
           shop_payout, manager_payout, client_total
    FROM quotes_quotefinancialsplit
    WHERE printy_fee < 0 OR shop_payout + manager_payout > client_total;
"""
from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

ZERO = Decimal("0")


def split_anomalies(*, policy=None) -> list[dict]:
    from pricing.services.platform_fee_policy import get_active_platform_fee_policy, minimum_manager_markup
    from quotes.models import QuoteFinancialSplit

    policy = policy or get_active_platform_fee_policy()
    rows: list[dict] = []
    for split in QuoteFinancialSplit.objects.select_related("quote").order_by("id").iterator():
        problems: list[str] = []
        shop = split.shop_payout or ZERO
        manager = split.manager_payout or ZERO
        fee = split.printy_fee or ZERO
        total = split.client_total or ZERO
        if fee < 0:
            problems.append("negative_printy_fee")
        if shop + manager > total:
            problems.append("over_allocated")
        if shop + manager + fee != total:
            problems.append("does_not_reconcile")
        if shop < 0 or manager < 0:
            problems.append("negative_payout")
        if split.production_cost is not None and shop < split.production_cost:
            problems.append("shop_below_cost")
        if split.production_cost and split.production_cost > 0 and split.manager_markup is not None:
            try:
                floor = minimum_manager_markup(
                    production_cost=split.production_cost, policy=policy, fee_waived=bool(split.fee_waived)
                )
            except Exception:  # pragma: no cover - defensive on legacy rows
                floor = None
            if floor is not None and split.manager_markup < floor:
                problems.append("below_current_floor")
        if problems:
            rows.append(
                {
                    "split_id": split.id,
                    "quote_id": split.quote_id,
                    "locked": bool(split.locked),
                    "fee_waived": bool(split.fee_waived),
                    "pricing_tier": split.pricing_tier,
                    "production_cost": split.production_cost,
                    "manager_markup": split.manager_markup,
                    "shop_payout": shop,
                    "manager_payout": manager,
                    "printy_fee": fee,
                    "client_total": total,
                    "problems": problems,
                }
            )
    return rows


def managed_job_anomalies() -> list[dict]:
    from django.db.models import Q

    from jobs.models import ManagedJob

    return [
        {"job_id": job.id, "reference": job.managed_reference, "printy_fee": job.printy_fee, "broker_payout": job.broker_payout}
        for job in ManagedJob.objects.filter(Q(printy_fee__lt=0) | Q(broker_payout__lt=0)).order_by("id")
    ]


class Command(BaseCommand):
    help = "Read-only: list stored quote splits / jobs with negative or over-allocated money (audit #6)."

    def add_arguments(self, parser):
        parser.add_argument("--fail-if-any", action="store_true", help="Exit with an error when anomalies are found.")

    def handle(self, *args, **options):
        splits = split_anomalies()
        jobs = managed_job_anomalies()
        hard = [row for row in splits if set(row["problems"]) - {"below_current_floor"}]
        if not splits and not jobs:
            self.stdout.write(self.style.SUCCESS("No negative or over-allocated splits found."))
            return
        for row in splits:
            self.stdout.write(
                "split #{split_id} quote #{quote_id} [{pricing_tier}{waived_label}{locked_label}] cost={production_cost} "
                "markup={manager_markup} shop={shop_payout} manager={manager_payout} printy={printy_fee} "
                "total={client_total} -> {problems_label}".format(
                    **row,
                    waived_label=", fee waived" if row["fee_waived"] else "",
                    locked_label=", locked" if row["locked"] else "",
                    problems_label=", ".join(row["problems"]),
                )
            )
        for row in jobs:
            self.stdout.write(
                "managed job #{job_id} {reference}: printy_fee={printy_fee} broker_payout={broker_payout}".format(**row)
            )
        self.stdout.write(
            f"{len(hard)} split(s) with negative/over-allocated money, "
            f"{len(splits) - len(hard)} only below the current floor, {len(jobs)} managed job(s). Nothing was modified."
        )
        if options.get("fail_if_any") and (hard or jobs):
            raise CommandError("Financial split anomalies found.")
