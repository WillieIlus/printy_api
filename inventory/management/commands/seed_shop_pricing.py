"""
Seed a shop with Paper, Machine, and PrintingRate so gallery products can show prices.
Run: python manage.py seed_shop_pricing --shop <slug-or-id>
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from inventory.choices import SheetSize
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import PrintingRate
from shops.models import Shop


class Command(BaseCommand):
    help = "Seed a shop with Paper, Machine, and PrintingRate for gallery pricing"

    def add_arguments(self, parser):
        parser.add_argument("--shop", type=str, required=True, help="Shop ID or slug")

    def handle(self, *args, **options):
        shop_ref = options["shop"]
        if shop_ref.isdigit():
            shop = Shop.objects.filter(pk=int(shop_ref)).first()
        else:
            shop = Shop.objects.filter(slug=shop_ref).first()

        if not shop:
            self.stderr.write(self.style.ERROR(f"Shop not found: {shop_ref}"))
            return

        created = []

        # Paper (SRA3 for business cards)
        paper, p_created = Paper.objects.get_or_create(
            shop=shop,
            sheet_size=SheetSize.SRA3,
            gsm=300,
            paper_type="GLOSS",
            defaults={
                "width_mm": 320,
                "height_mm": 450,
                "buying_price": Decimal("15"),
                "selling_price": Decimal("24"),
                "is_active": True,
            },
        )
        if p_created:
            created.append("Paper (SRA3 300gsm Gloss)")

        # Machine
        machine = Machine.objects.filter(shop=shop, is_active=True).first()
        if not machine:
            machine = Machine.objects.create(
                shop=shop,
                name="Default Digital",
                machine_type="DIGITAL",
                max_width_mm=320,
                max_height_mm=450,
                is_active=True,
            )
            created.append("Machine")
        # PrintingRate
        rate, r_created = PrintingRate.objects.get_or_create(
            machine=machine,
            sheet_size=SheetSize.SRA3,
            color_mode=ColorMode.COLOR,
            defaults={
                "single_price": Decimal("45"),
                "double_price": Decimal("75"),
                "is_active": True,
                "is_default": True,
            },
        )
        if r_created:
            created.append("PrintingRate (SRA3 Color)")

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created for {shop.name}: {', '.join(created)}"))
        else:
            self.stdout.write(f"Shop {shop.name} already has paper, machine, and printing rate.")
