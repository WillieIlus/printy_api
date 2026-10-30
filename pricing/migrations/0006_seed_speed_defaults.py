from decimal import Decimal

from django.db import migrations


TIERS = [
    {
        "key": "standard",
        "label": "Standard",
        "multiplier": Decimal("1.00"),
        "target_turnaround_hours": {"default": 48, "business_card": 48, "flyer": 24, "booklet": 48},
        "sort_order": 10,
    },
    {
        "key": "faster",
        "label": "Faster",
        "multiplier": Decimal("1.50"),
        "target_turnaround_hours": {"default": 24, "business_card": 24, "flyer": 12, "booklet": 24},
        "sort_order": 20,
    },
    {
        "key": "fastest",
        "label": "Fastest",
        "multiplier": Decimal("2.00"),
        "target_turnaround_hours": {"default": 12, "business_card": 12, "flyer": 6, "booklet": 12},
        "sort_order": 30,
    },
]

WINDOWS = [
    {
        "key": "evening",
        "label": "Evening work",
        "multiplier": Decimal("1.50"),
        "weekdays": [1, 2, 3, 4, 5, 6],
        "start_time": "18:00",
        "end_time": "22:00",
    },
    {
        "key": "overnight",
        "label": "Overnight work",
        "multiplier": Decimal("2.00"),
        "weekdays": [],
        "start_time": "22:00",
        "end_time": "08:00",
    },
    {
        "key": "sunday",
        "label": "Sunday work",
        "multiplier": Decimal("2.00"),
        "weekdays": [7],
        "start_time": None,
        "end_time": None,
    },
    {
        "key": "public_holiday",
        "label": "Public holiday work",
        "multiplier": Decimal("2.00"),
        "weekdays": [],
        "start_time": None,
        "end_time": None,
        "covers_public_holidays": True,
    },
]


def seed(apps, schema_editor):
    SpeedTier = apps.get_model("pricing", "SpeedTier")
    TimeWindowRule = apps.get_model("pricing", "TimeWindowRule")
    EstimatePolicy = apps.get_model("pricing", "EstimatePolicy")
    for row in TIERS:
        SpeedTier.objects.get_or_create(key=row["key"], defaults=row)
    for row in WINDOWS:
        TimeWindowRule.objects.get_or_create(key=row["key"], defaults=row)
    if not EstimatePolicy.objects.filter(is_active=True).exists():
        EstimatePolicy.objects.create(name="Default Estimate Policy", minimum_measured_jobs=5)


def unseed(apps, schema_editor):
    SpeedTier = apps.get_model("pricing", "SpeedTier")
    TimeWindowRule = apps.get_model("pricing", "TimeWindowRule")
    SpeedTier.objects.filter(key__in=[row["key"] for row in TIERS]).delete()
    TimeWindowRule.objects.filter(key__in=[row["key"] for row in WINDOWS]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("pricing", "0005_estimatepolicy_publicholiday_speedtier_and_more"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
