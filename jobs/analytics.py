"""Internal production analytics derived only from real recorded data (law 3.4 / 10.3).

These helpers never invent a figure. Every aggregate reports how many records
underpin it (``sample_size``) and whether that is enough to trust
(``sufficient``); when the sample is too small the ``value`` is ``null`` rather
than a misleading number built from one or two rows. Unknown actuals stay
unknown — they are excluded from the count, never treated as zero.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from django.conf import settings

from jobs.completion_actuals import COMPLETION_ACTUALS_EVENT
from jobs.models import JobEvent, ManagedJob
from quotes.models import QuoteVersionOutcome

DEFAULT_MIN_SAMPLE = 5

#: A quote version is a "decision" once it is accepted, lost, or expired.
#: ``open`` (still undecided) and ``superseded`` (replaced by a newer price)
#: are not outcomes of the buyer's decision and never count in the rate.
DECIDED_QUOTE_OUTCOMES = (
    QuoteVersionOutcome.ACCEPTED,
    QuoteVersionOutcome.LOST,
    QuoteVersionOutcome.EXPIRED,
)


def min_sample() -> int:
    """Records required before an aggregate is treated as reliable."""
    return int(getattr(settings, "PRODUCTION_ANALYTICS_MIN_SAMPLE", DEFAULT_MIN_SAMPLE))


def _metric(value: Any, sample_size: int) -> dict[str, Any]:
    threshold = min_sample()
    sufficient = sample_size >= threshold
    return {
        "value": value if sufficient else None,
        "sample_size": sample_size,
        "sufficient": sufficient,
        "min_sample": threshold,
    }


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def completion_actuals_events(managed_job_ids: Iterable[int] | None = None):
    qs = JobEvent.objects.filter(event=COMPLETION_ACTUALS_EVENT)
    if managed_job_ids is not None:
        qs = qs.filter(managed_job_id__in=list(managed_job_ids))
    return qs


def latest_actuals_per_job(
    managed_job_ids: Iterable[int] | None = None,
) -> dict[int, JobEvent]:
    """The most recent completion-actuals event for each job.

    Repeated recordings for one job collapse to the latest figure so a job is
    never double-counted in an aggregate.
    """
    latest: dict[int, JobEvent] = {}
    for event in completion_actuals_events(managed_job_ids).order_by(
        "managed_job_id", "-occurred_at", "-id"
    ):
        latest.setdefault(event.managed_job_id, event)
    return latest


def estimate_vs_actual(
    managed_job_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Total accepted estimate vs real production cost, for jobs with both."""
    total_estimate = Decimal("0")
    total_actual = Decimal("0")
    count = 0
    for event in latest_actuals_per_job(managed_job_ids).values():
        metadata = event.metadata or {}
        estimate = _dec((metadata.get("estimate") or {}).get("production_cost"))
        actual = _dec(metadata.get("actual_production_cost"))
        if estimate is None or actual is None:
            continue
        total_estimate += estimate
        total_actual += actual
        count += 1

    if count == 0:
        value: dict[str, Any] | None = None
    else:
        variance = total_actual - total_estimate
        pct = (
            (variance / total_estimate * 100).quantize(Decimal("0.01"))
            if total_estimate != 0
            else None
        )
        value = {
            "total_estimate": str(total_estimate),
            "total_actual": str(total_actual),
            "variance": str(variance),
            "variance_pct": str(pct) if pct is not None else None,
        }
    return _metric(value, count)


def _accuracy_value(
    total_estimate: Decimal, total_actual: Decimal, count: int
) -> dict[str, Any] | None:
    if count == 0:
        return None
    variance = total_actual - total_estimate
    pct = (
        (variance / total_estimate * 100).quantize(Decimal("0.01"))
        if total_estimate != 0
        else None
    )
    return {
        "total_estimate": str(total_estimate),
        "total_actual": str(total_actual),
        "variance": str(variance),
        "variance_pct": str(pct) if pct is not None else None,
        "records": count,
    }


def _accuracy_groups(
    managed_job_ids: Iterable[int] | None,
    key_for,
) -> list[dict[str, Any]]:
    """Group accepted-estimate vs actual accuracy by an arbitrary key."""
    groups: dict[Any, dict[str, Any]] = {}
    for event in latest_actuals_per_job(managed_job_ids).values():
        metadata = event.metadata or {}
        estimate = _dec((metadata.get("estimate") or {}).get("production_cost"))
        actual = _dec(metadata.get("actual_production_cost"))
        if estimate is None or actual is None:
            continue
        job = getattr(event, "managed_job", None)
        if job is None:
            continue
        key, label = key_for(event, metadata, job)
        if key is None:
            continue
        bucket_key = tuple(sorted(key.items()))
        bucket = groups.setdefault(
            bucket_key,
            {
                "key": key,
                "label": label,
                "estimate": Decimal("0"),
                "actual": Decimal("0"),
                "count": 0,
            },
        )
        bucket["estimate"] += estimate
        bucket["actual"] += actual
        bucket["count"] += 1

    threshold = min_sample()
    rows = []
    for bucket in groups.values():
        rows.append(
            {
                **bucket["key"],
                "label": bucket["label"],
                "sample_size": bucket["count"],
                "sufficient": bucket["count"] >= threshold,
                "min_sample": threshold,
                "value": (
                    _accuracy_value(bucket["estimate"], bucket["actual"], bucket["count"])
                    if bucket["count"] >= threshold
                    else None
                ),
            }
        )
    rows.sort(key=lambda row: (-row["sample_size"], str(row["label"])))
    return rows


def estimate_accuracy_by_printer(
    managed_job_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Estimate accuracy per printer shop (law 10.2/10.3)."""
    def key_for(event, metadata, job):
        printer_id = metadata.get("production_shop_id") or job.assigned_shop_id
        if printer_id is None:
            return None, None
        shop = getattr(job, "assigned_shop", None)
        label = getattr(shop, "name", "") or f"Printer {printer_id}"
        return {"printer_id": printer_id}, label

    return {
        "groups": _accuracy_groups(managed_job_ids, key_for),
        "min_sample": min_sample(),
    }


def estimate_accuracy_by_product(
    managed_job_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Estimate accuracy per product (law 10.2/10.3)."""
    from jobs.completion_actuals import products_for_job

    def key_for(event, metadata, job):
        product = metadata.get("product")
        if not product:
            products = products_for_job(job)
            product = " + ".join(products) or None
        if not product:
            return None, None
        return {"product": product}, product

    return {
        "groups": _accuracy_groups(managed_job_ids, key_for),
        "min_sample": min_sample(),
    }


def reprints(managed_job_ids: Iterable[int] | None = None) -> dict[str, Any]:
    """Total reprints recorded across completed jobs."""
    total = 0
    count = 0
    for event in latest_actuals_per_job(managed_job_ids).values():
        recorded = (event.metadata or {}).get("reprints")
        if recorded is None:
            continue
        try:
            total += int(recorded)
        except (ValueError, TypeError):
            continue
        count += 1
    value = {"total_reprints": total} if count else None
    return _metric(value, count)


def waste(managed_job_ids: Iterable[int] | None = None) -> dict[str, Any]:
    """Total spoil/waste sheets recorded across completed jobs."""
    total = 0
    count = 0
    for event in latest_actuals_per_job(managed_job_ids).values():
        recorded = (event.metadata or {}).get("spoilage_sheets")
        if recorded is None:
            continue
        try:
            total += int(recorded)
        except (ValueError, TypeError):
            continue
        count += 1
    value = {"total_waste_sheets": total} if count else None
    return _metric(value, count)


def turnaround(
    managed_job_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Average turnaround in hours, from captured duration or real timestamps.

    A duration explicitly recorded on the job's actuals wins; otherwise it is
    derived from ``accepted_at`` → ``delivered_at``/``completed_at``. Jobs with
    neither are skipped, never counted as zero.
    """
    recorded: dict[int, float] = {}
    for job_id, event in latest_actuals_per_job(managed_job_ids).items():
        hours = _dec((event.metadata or {}).get("turnaround_hours"))
        if hours is not None:
            recorded[job_id] = float(hours)

    total = sum(recorded.values())
    count = len(recorded)

    jobs = ManagedJob.objects.all()
    if managed_job_ids is not None:
        jobs = jobs.filter(id__in=list(managed_job_ids))

    rows = jobs.filter(accepted_at__isnull=False).values_list(
        "id", "accepted_at", "delivered_at", "completed_at"
    )
    for job_id, accepted_at, delivered_at, completed_at in rows:
        if job_id in recorded:
            continue
        end = delivered_at or completed_at
        if accepted_at is None or end is None:
            continue
        seconds = (end - accepted_at).total_seconds()
        if seconds < 0:
            continue
        total += seconds / 3600.0
        count += 1

    value = {"average_hours": round(total / count, 2)} if count else None
    return _metric(value, count)


def quote_acceptance(quote_versions) -> dict[str, Any]:
    """Quote acceptance rate over decided versions (open/superseded excluded)."""
    outcomes = list(
        quote_versions.filter(outcome__in=DECIDED_QUOTE_OUTCOMES).values_list(
            "outcome", flat=True
        )
    )
    decided = len(outcomes)
    accepted = sum(1 for outcome in outcomes if outcome == QuoteVersionOutcome.ACCEPTED)
    value = (
        {
            "accepted": accepted,
            "decided": decided,
            "rate": round(accepted / decided, 4),
        }
        if decided
        else None
    )
    return _metric(value, decided)


def summarize(*, managed_job_ids=None, quote_versions=None) -> dict[str, Any]:
    """All internal metrics, each carrying its own sample size + sufficiency."""
    summary = {
        "estimate_vs_actual": estimate_vs_actual(managed_job_ids),
        "estimate_accuracy_by_printer": estimate_accuracy_by_printer(managed_job_ids),
        "estimate_accuracy_by_product": estimate_accuracy_by_product(managed_job_ids),
        "turnaround": turnaround(managed_job_ids),
        "reprints": reprints(managed_job_ids),
        "waste": waste(managed_job_ids),
    }
    if quote_versions is not None:
        summary["quote_acceptance"] = quote_acceptance(quote_versions)
    return summary
