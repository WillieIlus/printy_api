"""Move ivory out of finishing and keep it as a paper choice.

Ivory 300gsm is a paper/grammage option, not a finishing service. Older shops
may still carry the seeded "Ivory 300 Duplex" finishing row in their canonical
rates or in the saved rate-card JSON. Deactivate the canonical row and drop it
from the saved JSON so the Finishing step no longer offers it.
"""

from django.db import migrations

IVORY_FIELDS = ("key", "name", "label", "slug")


def _mentions_ivory(row) -> bool:
    text = " ".join(str(row.get(field) or "") for field in IVORY_FIELDS).lower()
    return "ivory" in text


def remove_ivory_finishing(apps, schema_editor):
    FinishingRate = apps.get_model("pricing", "FinishingRate")
    ShopRateCardSetup = apps.get_model("pricing", "ShopRateCardSetup")

    for rate in FinishingRate.objects.all().iterator():
        name = str(getattr(rate, "name", "") or "").lower()
        slug = str(getattr(rate, "slug", "") or "").lower()
        if "ivory" in name or "ivory" in slug:
            if rate.is_active:
                rate.is_active = False
                rate.save(update_fields=["is_active"])

    for setup in ShopRateCardSetup.objects.all().iterator():
        rows = list(setup.finishing_rows or [])
        kept = [row for row in rows if not (isinstance(row, dict) and _mentions_ivory(row))]
        if len(kept) != len(rows):
            setup.finishing_rows = kept
            setup.save(update_fields=["finishing_rows"])


class Migration(migrations.Migration):
    dependencies = [
        ("pricing", "0007_shopspeedtieroptin_target_hours"),
    ]

    operations = [
        migrations.RunPython(remove_ivory_finishing, migrations.RunPython.noop),
    ]
