"""
Seed a printer shop with a full-color (COLOR) printing rate card that also
supports double sides (DUPLEX) — single_price for simplex, double_price for
duplex. Mirrors the MVP rate-card semantics and the seed_shop_pricing
conventions.

Run: python manage.py seed_printer_full_color_duplex [--shop <slug-or-id>]
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from inventory.choices import PaperCategory, PaperType, SheetSize
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import FinishingRate, PrintingRate
from shops.models import Shop

DEFAULT_EMAIL = "ava@avaprint.co.ke"
DEFAULT_USER_PASSWORD = "printy1234!"


class Command(BaseCommand):
    help = "Seed a printer shop with full-color + double-sided printing rate card"

    def add_arguments(self, parser):
        parser.add_argument(
            "--shop",
            type=str,
            help="Shop ID or slug to seed. Defaults to first active shop, else creates one.",
        )
        parser.add_argument(
            "--include-bw",
            action="store_true",
            help="Also create optional black & white (BW) printing rate rows. "
            "Without this, B&W is not offered by the shop (black_only specs won't price).",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        shop = self._resolve_shop(options["shop"], User)
        created = []

        machine, m_created = Machine.objects.get_or_create(
            shop=shop,
            name="Ava Digital Duplex",
            defaults={
                "machine_type": "DIGITAL",
                "max_width_mm": 320,
                "max_height_mm": 450,
                "min_gsm": 80,
                "max_gsm": 400,
                "is_active": True,
            },
        )
        if m_created:
            created.append("Machine 'Ava Digital Duplex'")

        paper_defs = [
            ("SRA3 300g Art Card Gloss", SheetSize.SRA3, 300, PaperType.GLOSS,
             PaperCategory.ARTCARD, "320x450", "15", "24", True),
            ("SRA3 250g Art Card Gloss", SheetSize.SRA3, 250, PaperType.GLOSS,
             PaperCategory.ARTCARD, "320x450", "13", "20", False),
            ("SRA3 130g Matt", SheetSize.SRA3, 130, PaperType.MATTE,
             PaperCategory.MATTE, "320x450", "8", "12", False),
            ("A3 200g Matt", SheetSize.A3, 200, PaperType.MATTE,
             PaperCategory.MATTE, "297x420", "9", "16", False),
            ("A4 80g Bond", SheetSize.A4, 80, PaperType.UNCOATED,
             PaperCategory.BOND, "210x297", "4", "8", False),
        ]
        for name, sheet, gsm, ptype, pcat, size, buying, selling, is_default in paper_defs:
            width, height = size.split("x")
            paper, p_created = Paper.objects.get_or_create(
                shop=shop,
                sheet_size=sheet,
                gsm=gsm,
                paper_type=ptype,
                defaults={
                    "name": name,
                    "category": pcat,
                    "width_mm": int(width),
                    "height_mm": int(height),
                    "buying_price": Decimal(buying),
                    "selling_price": Decimal(selling),
                    "is_active": True,
                    "is_default": is_default,
                },
            )
            if p_created:
                created.append(f"Paper {name}")

        rate_defs = [
            # Full color (COLOR). single_price = simplex, double_price = both sides.
            (SheetSize.SRA3, "45", "75", True),
            (SheetSize.A3, "60", "100", False),
            (SheetSize.A4, "35", "55", False),
        ]
        for sheet, single, double, is_default in rate_defs:
            rate, r_created = PrintingRate.objects.get_or_create(
                machine=machine,
                sheet_size=sheet,
                color_mode=ColorMode.COLOR,
                defaults={
                    "single_price": Decimal(single),
                    "double_price": Decimal(double),
                    "duplex_surcharge_enabled": False,
                    "is_active": True,
                    "is_default": is_default,
                },
            )
            if r_created:
                created.append(f"PrintingRate {sheet} COLOR (single {single} / double {double})")

        if options.get("include_bw"):
            bw_defs = [
                (SheetSize.SRA3, "6", "10"),
                (SheetSize.A3, "8", "14"),
                (SheetSize.A4, "4", "6"),
            ]
            for sheet, single, double in bw_defs:
                rate, r_created = PrintingRate.objects.get_or_create(
                    machine=machine,
                    sheet_size=sheet,
                    color_mode=ColorMode.BW,
                    defaults={
                        "single_price": Decimal(single),
                        "double_price": Decimal(double),
                        "duplex_surcharge_enabled": False,
                        "is_active": True,
                        "is_default": False,
                    },
                )
                if r_created:
                    created.append(f"PrintingRate {sheet} BW (single {single} / double {double})")
        else:
            self.stdout.write(
                "B&W not offered (no BW printing rates). Re-run with --include-bw to enable black_only."
            )

        finishing_defs = [
            ("Lamination 50um", "lamination", "10", "15", ["business_card"]),
            ("Cutting", "cutting", "3", None, None),
            ("Corner Rounding", "corner_rounding", "2", None, ["business_card"]),
        ]
        for name, slug, price, dbl, applies in finishing_defs:
            defaults = {
                "name": name,
                "price": Decimal(price),
                "is_active": True,
            }
            if dbl is not None:
                defaults["double_side_price"] = Decimal(dbl)
            if applies:
                defaults["applies_to_product_types"] = applies
            rate, f_created = FinishingRate.objects.get_or_create(
                shop=shop, slug=slug, defaults=defaults
            )
            if f_created:
                created.append(f"FinishingRate {name}")

        if created:
            self.stdout.write(self.style.SUCCESS(f"Seeded {shop.name} (slug={shop.slug}): {len(created)} new rows"))
            for item in created:
                self.stdout.write(f"  - {item}")
        else:
            self.stdout.write(f"Shop {shop.name} already fully seeded (printer, papers, COLOR rate card, finishings).")

        from shops.services import refresh_shop_pricing_ready

        ready = refresh_shop_pricing_ready(shop)
        self.stdout.write(
            self.style.SUCCESS(
                f"Print-ready flag recomputed from live data: pricing_ready={ready} "
                "(active priced paper stock AND active printing rates)."
            )
        )

    def _resolve_shop(self, shop_ref: str | None, User) -> Shop:
        if shop_ref:
            shop = (
                Shop.objects.filter(pk=int(shop_ref)).first()
                if shop_ref.isdigit()
                else Shop.objects.filter(slug=shop_ref).first()
            )
            if not shop:
                raise CommandError(f"Shop not found: {shop_ref}")
            return shop

        shop = Shop.objects.filter(is_active=True).order_by("id").first()
        if shop:
            return shop

        user, _ = User.objects.get_or_create(
            email=DEFAULT_EMAIL,
            defaults={
                "name": "Ava Print Studios",
                "password": DEFAULT_USER_PASSWORD,
                "role": User.Role.PRINTER,
                "is_staff": False,
            },
        )
        shop, created = Shop.objects.get_or_create(
            owner=user,
            defaults={
                "name": "Ava Print Studios",
                "slug": "ava-print-studios",
                "currency": "KES",
                "is_active": True,
                "is_public": True,
                "service_area": "Nairobi",
                "city": "Nairobi",
            },
        )
        if created:
            self.stdout.write(f"Created shop {shop.name} owned by {user.email}")
        return shop