from __future__ import annotations

from django.core.management.base import BaseCommand

from pricing.models import ShopRateCardSetup
from shops.models import Shop
from shops.services import rate_card_missing_steps, shop_rate_card_is_usable


def _mask_email(email: str) -> str:
    """Mask an email so operators can nudge owners without seeing raw PII."""
    value = (email or "").strip()
    if not value or "@" not in value:
        return "(none)"
    local, _, domain = value.partition("@")
    if len(local) <= 2:
        masked_local = local[:1] + "*"
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{domain}"


def _mask_phone(phone: str) -> str:
    value = (phone or "").strip()
    if not value:
        return "(none)"
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


class Command(BaseCommand):
    """Phase 3A / D1: READ-ONLY readiness report.

    Confirmation is now an explicit owner action, so there is no backfill.
    This report lists shops that are usable but unconfirmed (candidates for a
    nudge) and shops that are incomplete, with exactly what each is missing.
    Owner contact details are masked so the output can be shared safely.
    """

    help = (
        "READ-ONLY: report rate-card readiness. Lists shops that are usable but "
        "unconfirmed, and incomplete shops with what each is missing. Never "
        "writes anything. Owner email/phone are masked."
    )

    def handle(self, *args, **options):
        usable_unconfirmed: list[tuple[Shop, ShopRateCardSetup]] = []
        incomplete: list[tuple[Shop, ShopRateCardSetup]] = []

        setups = ShopRateCardSetup.objects.select_related("shop", "shop__owner").order_by("id")
        total = setups.count()
        for setup in setups:
            if setup.is_confirmed:
                continue
            if shop_rate_card_is_usable(setup.shop):
                usable_unconfirmed.append((setup.shop, setup))
            else:
                incomplete.append((setup.shop, setup))

        self.stdout.write(
            f"Scanned {total} setup(s): {len(usable_unconfirmed)} usable but unconfirmed, "
            f"{len(incomplete)} incomplete. This report writes nothing."
        )

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("USABLE BUT UNCONFIRMED (nudge the owner to confirm):"))
        if not usable_unconfirmed:
            self.stdout.write("- none")
        for shop, _setup in usable_unconfirmed:
            self.stdout.write(
                f"- shop={shop.slug} name={shop.name} "
                f"owner={_mask_email(getattr(shop.owner, 'email', ''))} "
                f"phone={_mask_phone(getattr(shop, 'public_whatsapp_number', '') or getattr(shop, 'phone_number', ''))}"
            )

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("INCOMPLETE (owner must finish setup before going live):"))
        if not incomplete:
            self.stdout.write("- none")
        for shop, _setup in incomplete:
            missing = rate_card_missing_steps(shop)
            missing_labels = ", ".join(step["title"] for step in missing) or "unknown"
            self.stdout.write(
                f"- shop={shop.slug} name={shop.name} "
                f"owner={_mask_email(getattr(shop.owner, 'email', ''))} "
                f"missing: {missing_labels}"
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                "Readiness report complete. No changes were made."
            )
        )
