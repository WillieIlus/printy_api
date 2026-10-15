"""The ONE stage + SLA configuration (law Part 4, 6.2).

Every due date, stage SLA and stage ownership rule lives here and nowhere else.
A due date is *computed* from this config, never typed freehand (law 6.2).

Working time is measured in the shop timezone (Africa/Nairobi). Everything is
*stored* in UTC; the conversion happens only inside the timing helpers.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import NamedTuple
from zoneinfo import ZoneInfo

# --- timezone / working calendar ------------------------------------------
SHOP_TIMEZONE_NAME = "Africa/Nairobi"
SHOP_TIMEZONE = ZoneInfo(SHOP_TIMEZONE_NAME)

# Monday=0 .. Sunday=6. The shop does not work weekends.
WORKING_WEEKDAYS: tuple[int, ...] = (0, 1, 2, 3, 4)
WORK_DAY_START = time(8, 0)   # 08:00 EAT
WORK_DAY_END = time(18, 0)    # 18:00 EAT (10 working hours per day)

# --- holiday calendar (law 6.2) -------------------------------------------
# Kenyan national holidays. Fixed-date holidays are generated per year; movable
# feasts (Easter-based and Islamic) are listed explicitly for the supported
# years. An unlisted day is treated as a working day rather than guessed.
_FIXED_HOLIDAY_MONTH_DAYS: tuple[tuple[int, int], ...] = (
    (1, 1),    # New Year's Day
    (5, 1),    # Labour Day
    (6, 1),    # Madaraka Day
    (10, 10),  # Huduma Day
    (10, 20),  # Mashujaa Day
    (12, 12),  # Jamhuri Day
    (12, 25),  # Christmas Day
    (12, 26),  # Boxing Day / Utamaduni Day
)

_MOVABLE_HOLIDAYS: tuple[date, ...] = (
    # 2024
    date(2024, 3, 29),  # Good Friday
    date(2024, 4, 1),   # Easter Monday
    date(2024, 4, 10),  # Eid al-Fitr (observed)
    date(2024, 6, 17),  # Eid al-Adha (observed)
    # 2025
    date(2025, 3, 31),  # Eid al-Fitr (observed)
    date(2025, 4, 18),  # Good Friday
    date(2025, 4, 21),  # Easter Monday
    date(2025, 6, 7),   # Eid al-Adha (observed)
    # 2026
    date(2026, 3, 20),  # Eid al-Fitr (observed)
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 6),   # Easter Monday
    date(2026, 5, 27),  # Eid al-Adha (observed)
    # 2027
    date(2027, 3, 26),  # Good Friday
    date(2027, 3, 29),  # Easter Monday
)


def _build_holidays() -> frozenset[date]:
    holidays: set[date] = set(_MOVABLE_HOLIDAYS)
    for year in range(2024, 2031):
        for month, day in _FIXED_HOLIDAY_MONTH_DAYS:
            holidays.add(date(year, month, day))
    return frozenset(holidays)


HOLIDAYS: frozenset[date] = _build_holidays()

# --- stage ownership -------------------------------------------------------
OWNER_BUYER = "buyer"
OWNER_MANAGER = "manager"
OWNER_PRINTER = "printer"
OWNERS: tuple[str, ...] = (OWNER_BUYER, OWNER_MANAGER, OWNER_PRINTER)


class StageConfig(NamedTuple):
    key: str
    label: str
    owner: str
    sla_working_hours: Decimal
    next_action: str


# Law 6.2 stages: quote accepted, artwork approval, production, QC, delivery.
# Each stage has exactly one owner at any time.
STAGES: tuple[StageConfig, ...] = (
    StageConfig(
        "quote_accepted",
        "Quote accepted",
        OWNER_BUYER,
        Decimal("4"),
        "Accept the quote and pay into escrow",
    ),
    StageConfig(
        "artwork_approval",
        "Artwork approval",
        OWNER_BUYER,
        Decimal("12"),
        "Upload and approve the artwork",
    ),
    StageConfig(
        "production",
        "Production",
        OWNER_PRINTER,
        Decimal("40"),
        "Print the job",
    ),
    StageConfig(
        "qc",
        "Quality check",
        OWNER_MANAGER,
        Decimal("8"),
        "Check quality before handoff",
    ),
    StageConfig(
        "delivery",
        "Delivery",
        OWNER_PRINTER,
        Decimal("16"),
        "Deliver or hand off the job",
    ),
)

STAGES_BY_KEY: dict[str, StageConfig] = {stage.key: stage for stage in STAGES}
STAGE_ORDER: tuple[str, ...] = tuple(stage.key for stage in STAGES)

# Realistic buffer added on top of the summed stage SLAs so the computed due
# date is honest rather than optimistic (law 6.2).
DUE_DATE_BUFFER_WORKING_HOURS = Decimal("8")

# A manager/printer must have this many completed jobs before an on-time score
# is shown publicly (law 6.7). Below it: no score, never a placeholder.
MIN_JOBS_FOR_PUBLIC_SCORE = 5


# --- managed-job status -> stage ------------------------------------------
_STAGE_FOR_STATUS = {
    "draft": "quote_accepted",
    "quoted": "quote_accepted",
    "awaiting_payment": "quote_accepted",
    "payment_confirmed": "artwork_approval",
    "assigned": "artwork_approval",
    "in_production": "production",
    "finishing": "production",
    "ready": "qc",
    "delivered": "delivery",
    "completed": "delivery",
}


def stage_for_status(status: str | None) -> str:
    """Map a ManagedJob status to its canonical stage key."""
    if not status:
        return STAGE_ORDER[0]
    return _STAGE_FOR_STATUS.get(str(status), STAGE_ORDER[0])


__all__ = [
    "SHOP_TIMEZONE",
    "SHOP_TIMEZONE_NAME",
    "WORKING_WEEKDAYS",
    "WORK_DAY_START",
    "WORK_DAY_END",
    "HOLIDAYS",
    "OWNER_BUYER",
    "OWNER_MANAGER",
    "OWNER_PRINTER",
    "OWNERS",
    "StageConfig",
    "STAGES",
    "STAGES_BY_KEY",
    "STAGE_ORDER",
    "DUE_DATE_BUFFER_WORKING_HOURS",
    "MIN_JOBS_FOR_PUBLIC_SCORE",
    "stage_for_status",
]
