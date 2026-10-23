"""Lamination is per_sheet, never per_pc (prompt item 2).

A lamination row stored with a per-piece basis would bill every business card
instead of every sheet, overcharging a 100-card job roughly 20x. The pricing
engine now corrects this at charge time; this migration repairs the stored rows
so the printer's rate card and every projection agree. Where a correct per-sheet
row already exists for the same identity, the legacy per-piece row is retired
instead of violating the unique identity constraint.
"""

from django.db import migrations


LAMINATION_TOKENS = ("lamination", "laminate")


def forwards(apps, schema_editor):
    FinishingRate = apps.get_model("pricing", "FinishingRate")
    for rate in FinishingRate.objects.all().iterator():
        haystack = f"{rate.slug or ''} {rate.name or ''}".lower()
        if not any(token in haystack for token in LAMINATION_TOKENS):
            continue
        needs_fix = (
            rate.billing_basis != "per_sheet"
            or rate.charge_unit not in {"PER_SHEET", "PER_SIDE_PER_SHEET"}
            or rate.side_mode != "per_selected_side"
            or rate.display_unit_label != "per sheet"
        )
        if not needs_fix:
            continue

        duplicate = (
            FinishingRate.objects.filter(
                shop_id=rate.shop_id,
                slug=rate.slug,
                billing_basis="per_sheet",
                charge_unit="PER_SHEET",
                side_mode="per_selected_side",
                thickness_microns=rate.thickness_microns,
                is_single_sided_only=rate.is_single_sided_only,
                min_qty=rate.min_qty,
            )
            .exclude(pk=rate.pk)
            .exists()
        )
        if duplicate:
            rate.is_active = False
            rate.save(update_fields=["is_active"])
            continue

        rate.billing_basis = "per_sheet"
        rate.charge_unit = "PER_SHEET"
        rate.side_mode = "per_selected_side"
        rate.display_unit_label = "per sheet"
        rate.save(
            update_fields=[
                "billing_basis",
                "charge_unit",
                "side_mode",
                "display_unit_label",
            ]
        )


def backwards(apps, schema_editor):
    """No-op: the previous unit cannot be recovered per row, and per-sheet is
    the only correct basis for lamination."""


class Migration(migrations.Migration):
    dependencies = [
        ("pricing", "0003_shopratecardsetup_is_confirmed_and_more"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
