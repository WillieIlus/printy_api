from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from catalog.choices import PricingMode, ProductKind, ProductStatus
from catalog.models import Product, ProductCategory, ProductFinishingOption
from inventory.choices import MachineType, PaperCategory, PaperType, SheetSize
from inventory.models import Machine, Paper
from pricing.choices import (
    ChargeUnit,
    ColorMode,
    FinishingBillingBasis,
    FinishingSideMode,
    FinishingSides,
    Sides,
)
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate, VolumeDiscount
from shops.models import Shop


class Command(BaseCommand):
    help = "Seed canonical MVP data for a freshly migrated reset database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="PrintyReset123!",
            help="Password assigned to seeded users that do not already exist.",
        )

    def handle(self, *args, **options):
        password = options["password"]
        User = get_user_model()

        admin = self._user(
            User,
            email="admin@printy.local",
            password=password,
            name="Printy Admin",
            role=User.Role.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        manager = self._user(
            User,
            email="manager@printy.local",
            password=password,
            name="Printy Manager",
            role=User.Role.BROKER,
            is_staff=True,
            capability_overrides={"can_source_jobs": True, "can_manage_quotes": True},
        )
        client = self._user(
            User,
            email="client@printy.local",
            password=password,
            name="Printy Client",
            role=User.Role.CLIENT,
        )
        shop_user = self._user(
            User,
            email="shop@printy.local",
            password=password,
            name="Printy Production Shop",
            role=User.Role.PRODUCTION,
            is_staff=True,
        )

        policy, _ = PlatformFeePolicy.objects.update_or_create(
            name="Default Printy Fee Policy",
            defaults={
                "is_active": True,
                "printer_fee_rate": Decimal("0.0000"),
                "broker_margin_fee_rate": Decimal("0.0000"),
                "small_job_limit": Decimal("2000.00"),
                "medium_job_limit": Decimal("10000.00"),
                "small_job_max_multiple": Decimal("4.00"),
                "medium_job_max_multiple": Decimal("3.00"),
                "bulk_job_max_multiple": Decimal("2.00"),
                "add_platform_fee_on_top": False,
            },
        )

        shop, _ = Shop.objects.update_or_create(
            slug="printy-demo-production",
            defaults={
                "name": "Printy Demo Production",
                "owner": shop_user,
                "currency": "KES",
                "is_active": True,
                "is_public": True,
                "city": "Nairobi",
                "country": "Kenya",
                "service_area": "Nairobi",
                "turnaround_statement": "Standard 24-48 hour production",
                "business_email": "shop@printy.local",
                "phone_number": "+254700000000",
            },
        )

        category, _ = ProductCategory.objects.update_or_create(
            slug="business-cards",
            shop=None,
            defaults={
                "name": "Business Cards",
                "description": "Standard sheet-fed business cards.",
                "is_active": True,
            },
        )

        product, _ = Product.objects.update_or_create(
            slug="business-cards-standard",
            defaults={
                "name": "Business Cards - Standard",
                "description": "Two-sided business cards on SRA3 card stock.",
                "category": category,
                "pricing_mode": PricingMode.SHEET,
                "product_kind": ProductKind.FLAT,
                "status": ProductStatus.PUBLISHED,
                "is_active": True,
                "is_public": True,
                "default_finished_width_mm": 90,
                "default_finished_height_mm": 55,
                "default_sheet_size": SheetSize.SRA3,
                "default_sides": Sides.DUPLEX,
                "min_quantity": 100,
                "min_gsm": 250,
                "max_gsm": 350,
                "allowed_sheet_sizes": [SheetSize.SRA3],
                "allow_simplex": True,
                "allow_duplex": True,
                "standard_turnaround_hours": 48,
                "rush_available": True,
                "rush_turnaround_hours": 24,
            },
        )

        machine = Machine.objects.filter(shop=shop, name="Primary Digital Press").first()
        if machine:
            for field, value in {
                "machine_type": MachineType.DIGITAL,
                "max_width_mm": 320,
                "max_height_mm": 450,
                "min_gsm": 80,
                "max_gsm": 350,
                "is_active": True,
            }.items():
                setattr(machine, field, value)
            machine.save()
        else:
            machine, _ = Machine.objects.update_or_create(
                shop=shop,
                name="Primary Digital Press",
                defaults={
                    "machine_type": MachineType.DIGITAL,
                    "max_width_mm": 320,
                    "max_height_mm": 450,
                    "min_gsm": 80,
                    "max_gsm": 350,
                    "is_active": True,
                },
            )

        paper, _ = Paper.objects.update_or_create(
            shop=shop,
            sheet_size=SheetSize.SRA3,
            gsm=300,
            paper_type=PaperType.GLOSS,
            defaults={
                "name": "Gloss Art Card 300gsm",
                "category": PaperCategory.ARTCARD,
                "buying_price": Decimal("15.00"),
                "selling_price": Decimal("24.00"),
                "quantity_in_stock": 1000,
                "is_cover_stock": True,
                "is_active": True,
                "is_default": True,
            },
        )

        printing_rate, _ = PrintingRate.objects.update_or_create(
            machine=machine,
            sheet_size=SheetSize.SRA3,
            color_mode=ColorMode.COLOR,
            defaults={
                "single_price": Decimal("45.00"),
                "double_price": Decimal("75.00"),
                "duplex_surcharge_enabled": False,
                "duplex_surcharge": Decimal("0.00"),
                "is_active": True,
                "is_default": True,
            },
        )

        finishing_rate, _ = FinishingRate.objects.update_or_create(
            shop=shop,
            slug="matte-lamination",
            defaults={
                "name": "Matte Lamination",
                "charge_unit": ChargeUnit.PER_SHEET,
                "billing_basis": FinishingBillingBasis.PER_SHEET,
                "side_mode": FinishingSideMode.PER_SELECTED_SIDE,
                "price": Decimal("20.00"),
                "double_side_price": Decimal("35.00"),
                "minimum_charge": Decimal("100.00"),
                "display_unit_label": "per sheet",
                "help_text": "Charged per sheet. Choose one side or both sides.",
                "is_active": True,
            },
        )

        product_finishing, _ = ProductFinishingOption.objects.update_or_create(
            product=product,
            finishing_rate=finishing_rate,
            defaults={
                "apply_to_sides": FinishingSides.BOTH,
                "is_default": False,
            },
        )

        discount, _ = VolumeDiscount.objects.update_or_create(
            shop=shop,
            name="Bulk 500+",
            defaults={
                "min_quantity": 500,
                "discount_percent": Decimal("5.00"),
                "is_active": True,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded MVP data: "
                f"policy={policy.pk}, admin={admin.pk}, manager={manager.pk}, "
                f"client={client.pk}, shop_user={shop_user.pk}, shop={shop.pk}, "
                f"category={category.pk}, product={product.pk}, machine={machine.pk}, "
                f"paper={paper.pk}, printing_rate={printing_rate.pk}, "
                f"finishing_rate={finishing_rate.pk}, product_finishing={product_finishing.pk}, "
                f"discount={discount.pk}"
            )
        )

    def _user(self, User, *, email, password, **defaults):
        user, created = User.objects.update_or_create(email=email, defaults=defaults)
        if created or not user.has_usable_password():
            user.set_password(password)
            user.save(update_fields=["password"])
        return user
