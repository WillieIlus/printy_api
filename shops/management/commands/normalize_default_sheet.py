"""Normalize existing shops to the standard 320 x 450 mm (SRA3) sheet.

Dry-run by default. `--apply` writes a JSON backup first, so `--reverse` can
restore every changed machine/paper dimension. The command never creates or
changes a price: a shop that still has no SRA3 printing rate is reported as
needing one.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from inventory.choices import SheetSize
from inventory.models import Machine, Paper
from pricing.models import PrintingRate
from shops.models import Shop

STANDARD_SHEET = SheetSize.SRA3
STANDARD_WIDTH = 320
STANDARD_HEIGHT = 450
DEFAULT_BACKUP = "default_sheet_backup.json"


def _backup_path(raw: str | None) -> Path:
    if raw:
        return Path(raw)
    return Path(settings.BASE_DIR) / DEFAULT_BACKUP


def _machine_changes():
    changes = []
    for machine in Machine.objects.filter(is_active=True).select_related("shop"):
        if (machine.max_width_mm, machine.max_height_mm) == (STANDARD_WIDTH, STANDARD_HEIGHT):
            continue
        changes.append(
            {
                "model": "inventory.machine",
                "pk": machine.pk,
                "shop_id": machine.shop_id,
                "shop": machine.shop.name,
                "old": {
                    "max_width_mm": machine.max_width_mm,
                    "max_height_mm": machine.max_height_mm,
                },
            }
        )
    return changes


def _paper_changes():
    changes = []
    for paper in Paper.objects.filter(is_active=True, sheet_size=STANDARD_SHEET).select_related("shop"):
        if (paper.width_mm, paper.height_mm) == (STANDARD_WIDTH, STANDARD_HEIGHT):
            continue
        changes.append(
            {
                "model": "inventory.paper",
                "pk": paper.pk,
                "shop_id": paper.shop_id,
                "shop": paper.shop.name,
                "old": {"width_mm": paper.width_mm, "height_mm": paper.height_mm},
            }
        )
    return changes


def _missing_price_shops():
    missing = []
    for shop in Shop.objects.filter(is_active=True).order_by("id"):
        has_sra3_paper = Paper.objects.filter(
            shop=shop, sheet_size=STANDARD_SHEET, is_active=True, selling_price__gt=0
        ).exists()
        has_sra3_rate = PrintingRate.objects.filter(
            machine__shop=shop, machine__is_active=True, sheet_size=STANDARD_SHEET, is_active=True
        ).exists()
        if has_sra3_paper and not has_sra3_rate:
            missing.append({"shop_id": shop.id, "shop": shop.name, "issue": "needs printing price"})
        elif not has_sra3_paper:
            missing.append({"shop_id": shop.id, "shop": shop.name, "issue": "no active SRA3 paper"})
    return missing


class Command(BaseCommand):
    help = "Normalize existing shops to the standard 320 x 450 mm (SRA3) sheet."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply the normalization.")
        parser.add_argument("--reverse", action="store_true", help="Restore the last backup.")
        parser.add_argument("--backup", default=None, help="Backup JSON path.")

    def handle(self, *args, **options):
        backup_path = _backup_path(options.get("backup"))

        if options["reverse"]:
            if not backup_path.exists():
                self.stderr.write(f"No backup found at {backup_path}")
                return
            payload = json.loads(backup_path.read_text(encoding="utf-8"))
            restored = 0
            for row in payload.get("changes", []):
                model = row["model"]
                if model == "inventory.machine":
                    updated = Machine.objects.filter(pk=row["pk"]).update(**row["old"])
                elif model == "inventory.paper":
                    updated = Paper.objects.filter(pk=row["pk"]).update(**row["old"])
                else:
                    updated = 0
                restored += updated
            self.stdout.write(self.style.SUCCESS(f"Restored {restored} row(s) from {backup_path}"))
            return

        machine_changes = _machine_changes()
        paper_changes = _paper_changes()
        changes = machine_changes + paper_changes
        missing = _missing_price_shops()

        self.stdout.write(f"Shops needing a price or SRA3 paper: {len(missing)}")
        for row in missing:
            self.stdout.write(f"  {row['shop']} (#{row['shop_id']}): {row['issue']}")

        self.stdout.write(f"Machine dimension changes: {len(machine_changes)}")
        for row in machine_changes:
            self.stdout.write(
                f"  machine {row['shop']} (#{row['shop_id']}): "
                f"{row['old']['max_width_mm']}x{row['old']['max_height_mm']} -> 320x450"
            )
        self.stdout.write(f"Paper dimension changes: {len(paper_changes)}")
        for row in paper_changes:
            self.stdout.write(
                f"  paper {row['shop']} (#{row['shop_id']}): "
                f"{row['old']['width_mm']}x{row['old']['height_mm']} -> 320x450"
            )

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Dry run only. Re-run with --apply to write."))
            return

        backup_path.write_text(
            json.dumps({"changes": changes}, indent=2), encoding="utf-8"
        )
        applied = 0
        for row in machine_changes:
            applied += Machine.objects.filter(pk=row["pk"]).update(
                max_width_mm=STANDARD_WIDTH, max_height_mm=STANDARD_HEIGHT
            )
        for row in paper_changes:
            applied += Paper.objects.filter(pk=row["pk"]).update(
                width_mm=STANDARD_WIDTH, height_mm=STANDARD_HEIGHT
            )
        self.stdout.write(self.style.SUCCESS(f"Applied {applied} row(s). Backup: {backup_path}"))
