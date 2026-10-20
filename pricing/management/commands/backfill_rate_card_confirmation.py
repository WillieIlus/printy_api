from __future__ import annotations

from django.core.management.base import BaseCommand

from pricing.models import ShopRateCardSetup
from shops.models import Shop
from shops.services import shop_rate_card_is_usable


class Command(BaseCommand):
    help = (
        "Confirm rate cards that predate the is_confirmed field but whose live "
        "pricing is already usable (working paper stock + active printing rate). "
        "DRY RUN by default; pass --apply to write. Never sets is_confirmed=True "
        "on a setup whose pricing is incomplete, and never downgrades a confirmed "
        "setup. Idempotent."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist is_confirmed=True on eligible setups. Without this nothing is written.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report proposed changes without writing them (default).",
        )

    def handle(self, *args, **options):
        apply = bool(options["apply"]) and not bool(options["dry_run"])
        mode_label = "APPLY" if apply else "DRY RUN"

        to_confirm = []
        incomplete = []
        setups = ShopRateCardSetup.objects.select_related("shop").order_by("id")

        for setup in setups:
            if setup.is_confirmed:
                continue
            if shop_rate_card_is_usable(setup.shop):
                to_confirm.append(setup)
            else:
                incomplete.append(setup)

        self.stdout.write(
            f"{mode_label}: scanned {setups.count()} setup(s); "
            f"{len(to_confirm)} eligible to confirm, "
            f"{len(incomplete)} incomplete (left unconfirmed)."
        )

        for setup in to_confirm:
            self.stdout.write(f"- confirm setup shop={setup.shop.slug} ({setup.shop.name})")
        for setup in incomplete:
            self.stdout.write(
                self.style.WARNING(
                    f"- skip setup shop={setup.shop.slug}: pricing incomplete "
                    "(needs active priced paper stock and an active printing rate)"
                )
            )

        if not apply:
            # Active/public shops that would remain hidden from find-printer.
            still_hidden = [
                setup
                for setup in ShopRateCardSetup.objects.filter(
                    is_confirmed=False, shop__is_active=True, shop__is_public=True
                ).select_related("shop")
                if setup not in to_confirm
            ]
            for setup in still_hidden:
                self.stdout.write(
                    self.style.WARNING(
                        f"- still hidden after apply: shop={setup.shop.slug} "
                        "(incomplete price card)"
                    )
                )
            self.stdout.write(
                f"{mode_label}: {len(still_hidden)} active/public shop(s) "
                "would remain hidden from find-printer."
            )
            self.stdout.write(
                self.style.WARNING("Dry run complete. Re-run with --apply to persist changes.")
            )
            return

        confirmed = 0
        for setup in to_confirm:
            ShopRateCardSetup.objects.filter(pk=setup.pk).update(is_confirmed=True)
            confirmed += 1

        self.stdout.write(
            self.style.SUCCESS(f"Backfill complete: {confirmed} setup(s) confirmed.")
        )
