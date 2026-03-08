"""
Load sample demo calculator data.
Run: python manage.py load_demo_data
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from demo.models import (
    DemoPaper,
    DemoPrintingRate,
    DemoFinishingRate,
    DemoMaterial,
    DemoProduct,
    DemoProductFinishingOption,
)


class Command(BaseCommand):
    help = "Load sample data for demo calculator"

    def handle(self, *args, **options):
        self.stdout.write("Loading demo data...")

        # Papers
        papers = [
            ("A4", 80, "UNCOATED", "3.50"),
            ("A4", 130, "GLOSS", "6"),
            ("A4", 150, "GLOSS", "8"),
            ("A4", 300, "GLOSS", "12"),
            ("A4", 350, "COATED", "15"),
            ("A3", 130, "GLOSS", "12"),
            ("A3", 300, "GLOSS", "20"),
            ("SRA3", 150, "GLOSS", "16"),
            ("SRA3", 300, "GLOSS", "24"),
            ("SRA3", 350, "COATED", "28"),
        ]
        for sheet_size, gsm, paper_type, price in papers:
            DemoPaper.objects.update_or_create(
                sheet_size=sheet_size,
                gsm=gsm,
                paper_type=paper_type,
                defaults={"selling_price": price, "is_active": True},
            )
        self.stdout.write(f"  Created/updated {len(papers)} papers")

        # Printing rates
        rates = [
            ("A4", "BW", "10", "18"),
            ("A4", "COLOR", "18", "32"),
            ("A3", "BW", "20", "35"),
            ("A3", "COLOR", "35", "60"),
            ("SRA3", "BW", "25", "42"),
            ("SRA3", "COLOR", "45", "75"),
        ]
        for sheet_size, color_mode, single, double in rates:
            DemoPrintingRate.objects.update_or_create(
                sheet_size=sheet_size,
                color_mode=color_mode,
                defaults={
                    "single_price": single,
                    "double_price": double,
                    "is_active": True,
                },
            )
        self.stdout.write(f"  Created/updated {len(rates)} printing rates")

        # Finishing rates
        finishing = [
            ("Lamination", "PER_SHEET", "25", None, None),
            ("Round Edges", "FLAT", "15", None, None),
            ("Cutting", "PER_PIECE", "1", None, None),
            ("Binding", "FLAT", "120", None, 10),
            ("Folding", "PER_SHEET", "2", None, None),
            ("Eyelets", "PER_PIECE", "20", None, None),
        ]
        finishing_objs = {}
        for name, charge_unit, price, setup_fee, min_qty in finishing:
            obj, _ = DemoFinishingRate.objects.update_or_create(
                name=name,
                defaults={
                    "charge_unit": charge_unit,
                    "price": price,
                    "setup_fee": setup_fee,
                    "min_qty": min_qty,
                    "is_active": True,
                },
            )
            finishing_objs[name] = obj
        self.stdout.write(f"  Created/updated {len(finishing)} finishing rates")

        # Materials
        for mat_type, price in [("Vinyl", "450"), ("Banner", "380"), ("Reflective", "520")]:
            DemoMaterial.objects.update_or_create(
                material_type=mat_type,
                defaults={"selling_price": price, "unit": "SQM", "is_active": True},
            )
        self.stdout.write("  Created/updated 3 materials")

        # Products
        products_data = [
            {
                "name": "Standard Business Card",
                "description": "Classic 90×50 mm cards, ideal for networking.",
                "category": "business_cards",
                "pricing_mode": "SHEET",
                "width": 90,
                "height": 50,
                "sides": "DUPLEX",
                "min_qty": 100,
                "sheet_size": "SRA3",
                "copies": 10,
                "min_gsm": 250,
                "max_gsm": 350,
                "badge": "Popular",
                "order": 1,
                "finishing": ["Lamination", "Round Edges"],
            },
            {
                "name": "Premium Business Card",
                "description": "Thick art card with rounded corners.",
                "category": "business_cards",
                "pricing_mode": "SHEET",
                "width": 90,
                "height": 50,
                "sides": "DUPLEX",
                "min_qty": 100,
                "sheet_size": "SRA3",
                "copies": 10,
                "min_gsm": 300,
                "max_gsm": 350,
                "badge": "",
                "order": 2,
                "finishing": ["Lamination"],
            },
            {
                "name": "A5 Flyer",
                "description": "Compact flyers for events and promotions.",
                "category": "flyers",
                "pricing_mode": "SHEET",
                "width": 148,
                "height": 210,
                "sides": "DUPLEX",
                "min_qty": 100,
                "sheet_size": "SRA3",
                "copies": 4,
                "min_gsm": 130,
                "max_gsm": 300,
                "badge": "Popular",
                "order": 3,
                "finishing": ["Cutting"],
            },
            {
                "name": "A5 Booklet",
                "description": "Saddle-stitched booklet, various paper weights.",
                "category": "booklets",
                "pricing_mode": "SHEET",
                "width": 148,
                "height": 210,
                "sides": "DUPLEX",
                "min_qty": 50,
                "sheet_size": "SRA3",
                "copies": 4,
                "min_gsm": 80,
                "max_gsm": 350,
                "badge": "",
                "order": 4,
                "finishing": ["Binding", "Folding"],
            },
            {
                "name": "A4 Magazine",
                "description": "Glossy magazine, saddle-stitched.",
                "category": "magazines",
                "pricing_mode": "SHEET",
                "width": 210,
                "height": 297,
                "sides": "DUPLEX",
                "min_qty": 50,
                "sheet_size": "SRA3",
                "copies": 2,
                "min_gsm": 130,
                "max_gsm": 170,
                "badge": "",
                "order": 5,
                "finishing": ["Lamination", "Binding"],
            },
            {
                "name": "85×200 cm Roll-up",
                "description": "Standard exhibition roll-up banner.",
                "category": "rollup_banners",
                "pricing_mode": "LARGE_FORMAT",
                "width": 850,
                "height": 2000,
                "sides": "SIMPLEX",
                "min_qty": 1,
                "sheet_size": "",
                "copies": 1,
                "min_gsm": None,
                "max_gsm": None,
                "badge": "Popular",
                "order": 6,
                "finishing": [],
            },
        ]

        for i, pd in enumerate(products_data):
            p, _ = DemoProduct.objects.update_or_create(
                name=pd["name"],
                defaults={
                    "description": pd["description"],
                    "category": pd["category"],
                    "pricing_mode": pd["pricing_mode"],
                    "default_finished_width_mm": pd["width"],
                    "default_finished_height_mm": pd["height"],
                    "default_sides": pd["sides"],
                    "min_quantity": pd["min_qty"],
                    "default_sheet_size": pd["sheet_size"] or "SRA3",
                    "copies_per_sheet": pd["copies"],
                    "min_gsm": pd["min_gsm"],
                    "max_gsm": pd["max_gsm"],
                    "badge": pd["badge"],
                    "display_order": pd["order"],
                    "is_active": True,
                },
            )
            # Clear and re-add finishing options
            DemoProductFinishingOption.objects.filter(product=p).delete()
            for fin_name in pd["finishing"]:
                fr = finishing_objs.get(fin_name)
                if fr:
                    DemoProductFinishingOption.objects.create(
                        product=p,
                        finishing_rate=fr,
                        is_default=(fin_name == pd["finishing"][0]),
                    )

        self.stdout.write(self.style.SUCCESS(f"  Created/updated {len(products_data)} products"))
        self.stdout.write(self.style.SUCCESS("Demo data loaded successfully."))
