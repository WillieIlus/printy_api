from django.core.management.base import BaseCommand
from shops.models import Shop
from pricing.models import Material
from decimal import Decimal

class Command(BaseCommand):
    help = 'Seeds default large format materials for active public shops'

    def handle(self, *args, **options):
        shops = Shop.objects.filter(is_active=True, is_public=True)
        if not shops.exists():
            self.stdout.write(self.style.WARNING('No active public shops found.'))
            return

        materials_to_seed = [
            {'type': 'Vinyl Sticker', 'buying': 500, 'selling': 1200},
            {'type': 'PVC Banner', 'buying': 400, 'selling': 1000},
            {'type': 'Roll-up Media', 'buying': 800, 'selling': 2500},
            {'type': 'One Way Vision', 'buying': 600, 'selling': 1800},
            {'type': 'Satin Paper', 'buying': 300, 'selling': 1500},
        ]

        created_count = 0
        for shop in shops:
            for m in materials_to_seed:
                material, created = Material.objects.get_or_create(
                    shop=shop,
                    material_type=m['type'],
                    defaults={
                        'unit': 'SQM',
                        'buying_price': Decimal(m['buying']),
                        'selling_price': Decimal(m['selling']),
                        'print_price_per_sqm': Decimal(200),
                        'is_active': True
                    }
                )
                if created:
                    created_count += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully seeded {created_count} material records across {shops.count()} shops.'))
