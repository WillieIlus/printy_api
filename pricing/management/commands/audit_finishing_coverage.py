"""Audit every configured finishing option against the ONE finishing resolver.

Output is the gap list: each row is a shop that configured a finishing family but
whose buyer-facing option key cannot be resolved to one of its rate-card rows.
When the resolver is correct the command prints zero gap rows.

Usage:
    python manage.py audit_finishing_coverage
    python manage.py audit_finishing_coverage --shop <slug>
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from pricing.models import FinishingRate
from services.pricing.finishing_normalization import (
    finishing_family,
    lamination_tone,
    resolve_finishing_rate_for_slug,
)
from shops.models import Shop


# Every finishing option a buyer can choose, per product, in the vocabulary the
# calculator sends. A shop that configured the option's finishing family must be
# able to price it.
PRODUCT_FINISHING_OPTIONS: tuple[tuple[str, str, str], ...] = (
    ("business_card", "gloss-lamination", "Gloss lamination"),
    ("business_card", "matt-lamination", "Matt lamination"),
    ("business_card", "corner-rounding", "Rounded corners"),
    ("business_card", "cutting", "Cutting"),
    ("flyer", "gloss-lamination", "Gloss lamination"),
    ("flyer", "matt-lamination", "Matt lamination"),
    ("flyer", "half-fold", "Half fold"),
    ("flyer", "tri-fold", "Tri-fold"),
    ("flyer", "cutting", "Cutting"),
    ("label_sticker", "gloss-lamination", "Gloss lamination"),
    ("label_sticker", "matt-lamination", "Matt lamination"),
    ("label_sticker", "kiss-cut", "Kiss cut"),
    ("label_sticker", "die-cut", "Die cut"),
    ("label_sticker", "straight-cut", "Straight cut"),
    ("label_sticker", "cutting", "Cutting"),
    ("letterhead", "cutting", "Cutting"),
    ("poster", "gloss-lamination", "Gloss lamination"),
    ("poster", "matt-lamination", "Matt lamination"),
    ("poster", "cutting", "Cutting"),
    ("invitation_card", "gloss-lamination", "Gloss lamination"),
    ("invitation_card", "matt-lamination", "Matt lamination"),
    ("invitation_card", "corner-rounding", "Rounded corners"),
    ("invitation_card", "cutting", "Cutting"),
    ("certificate", "cutting", "Cutting"),
    ("sticker", "cutting", "Cutting"),
    ("booklet", "gloss-lamination", "Cover gloss lamination"),
    ("booklet", "matt-lamination", "Cover matt lamination"),
    ("booklet", "saddle_stitch", "Saddle stitch"),
    ("booklet", "perfect_bind", "Perfect bind"),
    ("booklet", "wire_o", "Wire-O"),
    ("booklet", "stitching", "Stitching"),
    ("booklet", "cutting", "Cutting"),
    ("brochure", "gloss-lamination", "Gloss lamination"),
    ("brochure", "matt-lamination", "Matt lamination"),
    ("brochure", "saddle-stitch", "Saddle stitch"),
    ("brochure", "perfect-bind", "Perfect bind"),
    ("brochure", "wire-o", "Wire-O"),
    ("brochure", "stitching", "Stitching"),
    ("brochure", "binding", "Binding"),
    ("brochure", "cutting", "Cutting"),
)


def _row_tone(row: FinishingRate) -> str | None:
    return lamination_tone(row.slug) or lamination_tone(row.name)


def audit_shop(shop: Shop) -> list[dict[str, str]]:
    rows = list(FinishingRate.objects.filter(shop=shop, is_active=True).order_by("id"))
    if not rows:
        return []
    gaps: list[dict[str, str]] = []
    for product_type, option_key, option_label in PRODUCT_FINISHING_OPTIONS:
        family = finishing_family(option_key)
        if not family:
            continue
        family_rows = [
            row
            for row in rows
            if family
            in {finishing_family(row.slug), finishing_family(row.name)}
        ]
        if not family_rows:
            # The shop does not configure this finishing at all: not a gap.
            continue
        if family == "lamination":
            tone = lamination_tone(option_key)
            if tone and not any(_row_tone(row) == tone for row in family_rows):
                # Configured lamination, but not this tone: not a gap.
                continue
        if resolve_finishing_rate_for_slug(shop, option_key) is not None:
            continue
        gaps.append(
            {
                "shop": shop.slug or str(shop.id),
                "product": product_type,
                "option": option_label,
                "option_key": option_key,
                "configured_rows": ", ".join(
                    f"{row.slug or row.name} ({row.billing_basis})" for row in family_rows
                ),
            }
        )
    return gaps


class Command(BaseCommand):
    help = "List configured finishing options the resolver cannot match (zero rows when clean)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--shop",
            dest="shop",
            default="",
            help="Only audit the shop with this slug.",
        )

    def handle(self, *args, **options):
        shops = Shop.objects.filter(is_active=True, rate_card_setup__is_confirmed=True).order_by("id")
        if options["shop"]:
            shops = shops.filter(slug=options["shop"])
        total = 0
        shop_count = 0
        for shop in shops:
            gaps = audit_shop(shop)
            if not gaps:
                continue
            shop_count += 1
            for gap in gaps:
                total += 1
                self.stdout.write(
                    f"{gap['shop']} | {gap['product']} | {gap['option']} "
                    f"({gap['option_key']}) -> not resolvable to {gap['configured_rows']}"
                )
        if total:
            self.stdout.write(
                self.style.ERROR(f"{total} configured finishing gap(s) across {shop_count} shop(s).")
            )
        else:
            self.stdout.write(self.style.SUCCESS("0 configured finishing gaps."))
