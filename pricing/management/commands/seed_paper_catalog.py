from __future__ import annotations

from copy import deepcopy

from django.core.management.base import BaseCommand, CommandError

from pricing.models import ShopRateCardSetup
from services.pricing.mvp_rate_card import (
    DEFAULT_PAPER_DEFINITIONS,
    _decorate_rate_card_rows,
    _normalize_paper_rows,
)
from shops.models import Shop


class Command(BaseCommand):
    help = (
        "Seed editable paper stock reference price rows into shop rate-card setup data. "
        "Existing shop-customized rows are preserved unless --overwrite is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--shop",
            dest="shop_slugs",
            action="append",
            default=[],
            help="Shop slug to seed. Can be passed multiple times. Defaults to all shops.",
        )
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace existing default-catalog paper rows with the current reference prices.",
        )
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Mark seeded reference paper rows active in the editable setup form.",
        )

    def handle(self, *args, **options):
        shop_slugs = options["shop_slugs"]
        shops = Shop.objects.all().order_by("id")
        if shop_slugs:
            shops = shops.filter(slug__in=shop_slugs)
            found_slugs = set(shops.values_list("slug", flat=True))
            missing = sorted(set(shop_slugs) - found_slugs)
            if missing:
                raise CommandError(f"Unknown shop slug(s): {', '.join(missing)}")

        seeded = 0
        updated = 0
        skipped = 0
        for shop in shops:
            result = self._seed_shop(
                shop,
                overwrite=options["overwrite"],
                activate=options["activate"],
            )
            seeded += result["seeded"]
            updated += result["updated"]
            skipped += result["skipped"]

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded paper catalog for {shops.count()} shop(s): "
                f"{seeded} added, {updated} updated, {skipped} preserved."
            )
        )

    def _seed_shop(self, shop, *, overwrite: bool, activate: bool) -> dict[str, int]:
        setup = ShopRateCardSetup.objects.filter(shop=shop).first()
        existing_rows = list(setup.paper_rows if setup and setup.paper_rows else [])
        existing_by_key = {
            str(row.get("key") or "").strip(): deepcopy(row)
            for row in existing_rows
            if row.get("key")
        }
        default_keys = {row["key"] for row in DEFAULT_PAPER_DEFINITIONS}
        merged_rows = []
        seeded = updated = skipped = 0

        for definition in DEFAULT_PAPER_DEFINITIONS:
            existing = existing_by_key.pop(definition["key"], None)
            if existing is not None and not overwrite:
                merged_rows.append(existing)
                skipped += 1
                continue

            row = deepcopy(definition)
            row["active"] = bool(activate)
            merged_rows.append(row)
            if existing is None:
                seeded += 1
            else:
                updated += 1

        custom_rows = [
            row
            for row in existing_rows
            if str(row.get("key") or "").strip() not in default_keys
        ]
        paper_rows, _ = _decorate_rate_card_rows(
            _normalize_paper_rows(merged_rows + custom_rows),
            [],
        )

        ShopRateCardSetup.objects.update_or_create(
            shop=shop,
            defaults={
                "paper_rows": paper_rows,
                "finishing_rows": setup.finishing_rows if setup else [],
                "shop_details": setup.shop_details if setup else {},
                "completed": setup.completed if setup else False,
            },
        )
        return {"seeded": seeded, "updated": updated, "skipped": skipped}
