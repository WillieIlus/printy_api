from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from catalog.choices import BindingType, PricingMode, ProductKind, ProductStatus
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
from pricing.models import (
    FinishingRate,
    PlatformFeePolicy,
    PrintingRate,
    QuantityPricingTier,
    SetupCostPolicy,
    VolumeDiscount,
    WastePolicy,
)
from shops.models import Shop


class Command(BaseCommand):
    help = "Seed realistic Step 8 starting data for backend testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default="PrintySeed123!",
            help="Password assigned to newly created seed users.",
        )

    def handle(self, *args, **options):
        password = options["password"]
        User = get_user_model()

        broker = self._user(
            User,
            email="manager@printy.local",
            password=password,
            name="Printy Test Manager",
            role=User.Role.BROKER,
            is_staff=True,
            partner_profile_enabled=True,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        shop_owner = self._user(
            User,
            email="production@printy.local",
            password=password,
            name="Nairobi Print House Owner",
            role=User.Role.PRODUCTION,
            is_staff=True,
        )

        policy, _ = PlatformFeePolicy.objects.update_or_create(
            name="Default Printy Fee Policy",
            defaults={
                "is_active": True,
                "printer_fee_rate": Decimal("0.0500"),
                "broker_margin_fee_rate": Decimal("0.1500"),
                "small_job_limit": Decimal("2000.00"),
                "medium_job_limit": Decimal("10000.00"),
                "small_job_max_multiple": Decimal("8.00"),
                "medium_job_max_multiple": Decimal("3.00"),
                "bulk_job_max_multiple": Decimal("2.00"),
                "add_platform_fee_on_top": False,
            },
        )
        waste_policy, _ = WastePolicy.objects.update_or_create(
            name="Default Waste Policy",
            defaults={
                "is_active": True,
                "fixed_waste_sheets": 2,
                "variable_waste_rate": Decimal("0.1000"),
                "minimum_billable_sheets": 3,
            },
        )
        setup_policy, _ = SetupCostPolicy.objects.update_or_create(
            name="Default Setup Cost Policy",
            defaults={
                "is_active": True,
                "setup_minutes": 10,
                "labor_rate_per_hour": Decimal("500.00"),
                "machine_setup_fee": Decimal("75.00"),
                "admin_handling_fee": Decimal("50.00"),
                "file_check_fee": Decimal("50.00"),
            },
        )
        tiers = [
            self._quantity_tier("1-5 sheets", 1, 5, "6.00", "1500.00"),
            self._quantity_tier("6-20 sheets", 6, 20, "3.50", "1200.00"),
            self._quantity_tier("21-100 sheets", 21, 100, "2.00", "800.00"),
            self._quantity_tier("101+ sheets", 101, None, "1.50", "500.00"),
        ]

        shop, _ = Shop.objects.update_or_create(
            slug="nairobi-print-house",
            defaults={
                "name": "Nairobi Print House",
                "owner": shop_owner,
                "currency": "KES",
                "is_active": True,
                "is_public": True,
                "service_area": "Nairobi CBD, Westlands, Kilimani",
                "turnaround_statement": "Same-day digital print, 2-3 day booklet jobs",
                "opening_hours_text": "Mon-Sat, 8am-6pm",
                "business_email": "production@printy.local",
                "public_email": "production@printy.local",
                "phone_number": "+254700100200",
                "public_whatsapp_number": "+254700100200",
                "address_line": "Moi Avenue",
                "city": "Nairobi",
                "state": "Nairobi",
                "country": "Kenya",
                "zip_code": "00100",
                "supports_custom_requests": True,
                "supports_catalog_requests": True,
            },
        )

        digital = self._upsert_machine(
            shop=shop,
            name="Digital A3 Production Press",
            machine_type=MachineType.DIGITAL,
            max_width_mm=330,
            max_height_mm=488,
            min_gsm=70,
            max_gsm=350,
        )
        offset = self._upsert_machine(
            shop=shop,
            name="Heidelberg Offset Press",
            machine_type=MachineType.OFFSET,
            max_width_mm=520,
            max_height_mm=720,
            min_gsm=80,
            max_gsm=400,
        )
        Machine.objects.filter(shop=shop, name="Primary Digital Press").delete()

        bond_80 = self._upsert_paper(
            shop=shop,
            name="Bond 80gsm",
            sheet_size=SheetSize.A4,
            gsm=80,
            paper_type=PaperType.UNCOATED,
            category=PaperCategory.BOND,
            buying_price="2.50",
            selling_price="5.00",
            is_default=False,
            is_insert_stock=True,
        )
        gloss_150 = self._upsert_paper(
            shop=shop,
            name="Gloss Art 150gsm",
            sheet_size=SheetSize.A3,
            gsm=150,
            paper_type=PaperType.GLOSS,
            category=PaperCategory.GLOSS,
            buying_price="10.00",
            selling_price="18.00",
            is_default=False,
            is_insert_stock=True,
        )
        board_350 = self._upsert_paper(
            shop=shop,
            name="Art Card 350gsm",
            sheet_size=SheetSize.SRA3,
            gsm=350,
            paper_type=PaperType.GLOSS,
            category=PaperCategory.ARTCARD,
            buying_price="24.00",
            selling_price="38.00",
            is_default=True,
            is_cover_stock=True,
        )

        categories = {
            "flyers": self._category("flyers", "Flyers", "Leaflets and promotional handouts."),
            "business-cards": self._category("business-cards", "Business Cards", "Personal and company cards."),
            "booklets": self._category("booklets", "Booklets", "Stapled and bound multipage products."),
            "banners": self._category("banners", "Banners", "Large-format banner and poster products."),
            "stickers": self._category("stickers", "Stickers", "Labels, decals, and sticker sheets."),
            "branding": self._category("branding", "Branding", "Business stationery and branded collateral."),
        }

        products = {
            "a5-flyer": self._product(
                slug="a5-flyer",
                name="A5 Flyer",
                category=categories["flyers"],
                description="A5 promotional flyer for short-run campaigns.",
                width=148,
                height=210,
                sheet_size=SheetSize.A3,
                sides=Sides.SIMPLEX,
                min_quantity=100,
                min_gsm=130,
                max_gsm=170,
            ),
            "standard-business-card": self._product(
                slug="standard-business-card",
                name="Standard Business Card",
                category=categories["business-cards"],
                description="Standard 90 x 55mm business card on heavy board.",
                width=90,
                height=55,
                sheet_size=SheetSize.SRA3,
                sides=Sides.DUPLEX,
                min_quantity=100,
                min_gsm=250,
                max_gsm=350,
            ),
            "a4-booklet": self._product(
                slug="a4-booklet",
                name="A4 Booklet",
                category=categories["booklets"],
                description="A4 saddle-stitched booklet with cover and inserts.",
                width=210,
                height=297,
                sheet_size=SheetSize.A3,
                sides=Sides.DUPLEX,
                min_quantity=25,
                min_gsm=80,
                max_gsm=350,
                product_kind=ProductKind.BOOKLET,
                binding_type=BindingType.SADDLE_STITCH,
            ),
            "pull-up-banner": self._product(
                slug="pull-up-banner",
                name="Pull-up Banner",
                category=categories["banners"],
                description="Standard exhibition pull-up banner artwork and print product.",
                width=850,
                height=2000,
                pricing_mode=PricingMode.LARGE_FORMAT,
                min_quantity=1,
                min_area_m2=Decimal("1.70"),
            ),
            "sticker-sheet": self._product(
                slug="sticker-sheet",
                name="Sticker Sheet",
                category=categories["stickers"],
                description="A4 sticker sheet for labels and packaging.",
                width=210,
                height=297,
                sheet_size=SheetSize.A4,
                sides=Sides.SIMPLEX,
                min_quantity=50,
                min_gsm=80,
                max_gsm=150,
            ),
            "branded-letterhead": self._product(
                slug="branded-letterhead",
                name="Branded Letterhead",
                category=categories["branding"],
                description="A4 corporate letterhead on bond paper.",
                width=210,
                height=297,
                sheet_size=SheetSize.A4,
                sides=Sides.SIMPLEX,
                min_quantity=100,
                min_gsm=80,
                max_gsm=120,
            ),
        }

        rates = [
            self._printing_rate(digital, SheetSize.A4, ColorMode.COLOR, "18.00", "30.00"),
            self._printing_rate(digital, SheetSize.A3, ColorMode.COLOR, "40.00", "70.00"),
            self._printing_rate(digital, SheetSize.SRA3, ColorMode.COLOR, "55.00", "95.00", is_default=True),
            self._printing_rate(offset, SheetSize.A3, ColorMode.COLOR, "18.00", "30.00"),
        ]

        lamination = self._finishing_rate(
            shop=shop,
            slug="matte-lamination",
            name="Matte Lamination",
            charge_unit=ChargeUnit.PER_SHEET,
            billing_basis=FinishingBillingBasis.PER_SHEET,
            side_mode=FinishingSideMode.PER_SELECTED_SIDE,
            price="25.00",
            double_side_price="45.00",
            minimum_charge="150.00",
            display_unit_label="per sheet",
        )
        binding = self._finishing_rate(
            shop=shop,
            slug="saddle-stitch-binding",
            name="Saddle-stitch Binding",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price="300.00",
            double_side_price=None,
            minimum_charge="300.00",
            display_unit_label="per job",
        )

        self._product_finishing(products["standard-business-card"], lamination, FinishingSides.BOTH)
        self._product_finishing(products["a4-booklet"], binding, FinishingSides.BOTH)

        discounts = [
            self._discount(shop, "Bulk 500+", 500, "10.00"),
            self._discount(shop, "Bulk 1000+", 1000, "15.00"),
        ]

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded Step 8 data: "
                f"policy={policy.id} rates=({policy.printer_fee_rate}, {policy.broker_margin_fee_rate}); "
                f"waste_policy={waste_policy.id}; setup_policy={setup_policy.id}; "
                f"quantity_tiers={','.join(str(tier.id) for tier in tiers)}; "
                f"shop={shop.slug}; broker={broker.email}; machines={digital.id},{offset.id}; "
                f"papers={bond_80.id},{gloss_150.id},{board_350.id}; "
                f"products={','.join(p.slug for p in products.values())}; "
                f"printing_rates={','.join(str(rate.id) for rate in rates)}; "
                f"finishings={lamination.id},{binding.id}; discounts={','.join(str(d.id) for d in discounts)}"
            )
        )

    def _user(self, User, *, email, password, **defaults):
        user, created = User.objects.update_or_create(email=email, defaults=defaults)
        if created or not user.has_usable_password():
            user.set_password(password)
            user.save(update_fields=["password"])
        return user

    def _upsert_machine(self, *, shop, name, machine_type, max_width_mm, max_height_mm, min_gsm, max_gsm):
        machine, _ = Machine.objects.update_or_create(
            shop=shop,
            name=name,
            defaults={
                "machine_type": machine_type,
                "max_width_mm": max_width_mm,
                "max_height_mm": max_height_mm,
                "min_gsm": min_gsm,
                "max_gsm": max_gsm,
                "is_active": True,
            },
        )
        return machine

    def _upsert_paper(
        self,
        *,
        shop,
        name,
        sheet_size,
        gsm,
        paper_type,
        category,
        buying_price,
        selling_price,
        is_default,
        is_cover_stock=False,
        is_insert_stock=False,
        is_sticker_stock=False,
    ):
        paper, _ = Paper.objects.update_or_create(
            shop=shop,
            sheet_size=sheet_size,
            gsm=gsm,
            paper_type=paper_type,
            defaults={
                "name": name,
                "category": category,
                "buying_price": Decimal(buying_price),
                "selling_price": Decimal(selling_price),
                "quantity_in_stock": 2000,
                "is_default": is_default,
                "is_cover_stock": is_cover_stock,
                "is_insert_stock": is_insert_stock,
                "is_sticker_stock": is_sticker_stock,
                "is_active": True,
            },
        )
        return paper

    def _category(self, slug, name, description):
        category, _ = ProductCategory.objects.update_or_create(
            slug=slug,
            shop=None,
            defaults={"name": name, "description": description, "is_active": True},
        )
        return category

    def _product(
        self,
        *,
        slug,
        name,
        category,
        description,
        width,
        height,
        pricing_mode=PricingMode.SHEET,
        sheet_size="",
        sides=Sides.SIMPLEX,
        min_quantity=1,
        min_gsm=None,
        max_gsm=None,
        min_area_m2=None,
        product_kind=ProductKind.FLAT,
        binding_type="",
    ):
        product, _ = Product.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "category": category,
                "description": description,
                "pricing_mode": pricing_mode,
                "product_kind": product_kind,
                "status": ProductStatus.PUBLISHED,
                "is_active": True,
                "is_public": True,
                "default_finished_width_mm": width,
                "default_finished_height_mm": height,
                "default_sheet_size": sheet_size,
                "default_sides": sides,
                "min_quantity": min_quantity,
                "min_gsm": min_gsm,
                "max_gsm": max_gsm,
                "allowed_sheet_sizes": [sheet_size] if sheet_size else None,
                "allow_simplex": True,
                "allow_duplex": True,
                "min_area_m2": min_area_m2,
                "default_binding_type": binding_type,
                "standard_turnaround_hours": 48,
                "rush_available": True,
                "rush_turnaround_hours": 24,
            },
        )
        return product

    def _printing_rate(self, machine, sheet_size, color_mode, single_price, double_price, *, is_default=False):
        rate, _ = PrintingRate.objects.update_or_create(
            machine=machine,
            sheet_size=sheet_size,
            color_mode=color_mode,
            defaults={
                "single_price": Decimal(single_price),
                "double_price": Decimal(double_price),
                "duplex_surcharge_enabled": False,
                "duplex_surcharge": Decimal("0.00"),
                "is_active": True,
                "is_default": is_default,
            },
        )
        return rate

    def _finishing_rate(
        self,
        *,
        shop,
        slug,
        name,
        charge_unit,
        billing_basis,
        side_mode,
        price,
        double_side_price,
        minimum_charge,
        display_unit_label,
    ):
        rate, _ = FinishingRate.objects.update_or_create(
            shop=shop,
            slug=slug,
            defaults={
                "name": name,
                "charge_unit": charge_unit,
                "billing_basis": billing_basis,
                "side_mode": side_mode,
                "price": Decimal(price),
                "double_side_price": Decimal(double_side_price) if double_side_price is not None else None,
                "minimum_charge": Decimal(minimum_charge),
                "display_unit_label": display_unit_label,
                "help_text": f"Seeded {name.lower()} starting rate.",
                "is_active": True,
            },
        )
        return rate

    def _product_finishing(self, product, finishing_rate, sides):
        option, _ = ProductFinishingOption.objects.update_or_create(
            product=product,
            finishing_rate=finishing_rate,
            defaults={"apply_to_sides": sides, "is_default": False},
        )
        return option

    def _discount(self, shop, name, min_quantity, discount_percent):
        discount, _ = VolumeDiscount.objects.update_or_create(
            shop=shop,
            name=name,
            defaults={
                "min_quantity": min_quantity,
                "discount_percent": Decimal(discount_percent),
                "is_active": True,
            },
        )
        return discount

    def _quantity_tier(self, name, min_sheets, max_sheets, multiplier, floor):
        tier, _ = QuantityPricingTier.objects.update_or_create(
            name=name,
            defaults={
                "is_active": True,
                "min_sheets": min_sheets,
                "max_sheets": max_sheets,
                "multiplier": Decimal(multiplier),
                "minimum_order_floor": Decimal(floor),
            },
        )
        return tier
