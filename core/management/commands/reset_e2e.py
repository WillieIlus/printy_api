"""Reset the database to a clean, real-data-only state for the e2e suite.

This command exists for the browser end-to-end run. It:

* flushes every table (schema and migrations stay intact),
* creates one real operator account for the Django admin, so a human can
  verify managers and shops the same way production does,
* seeds only the public catalog and the platform fee policy.

It never creates demo buyers, managers, printers, shops, rosters or fake
rate cards. Every supply and demand account in the e2e run is created through
the real sign-up form.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.choices import PricingMode, ProductKind, ProductStatus
from catalog.models import Product, ProductCategory
from inventory.choices import SheetSize
from pricing.choices import Sides
from pricing.models import PlatformFeePolicy


class Command(BaseCommand):
    help = "Flush the database and seed only the real catalog, fee policy and operator admin."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm the destructive flush. Required.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow the flush when DEBUG is off (never use on production).",
        )
        parser.add_argument("--admin-email", default="ops@printy.test")
        parser.add_argument("--admin-password", default="PrintyOps123!")
        parser.add_argument("--admin-name", default="Printy Ops")

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError("Refusing to flush without --yes.")
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to flush a non-debug database without --force. "
                "This command is for the e2e environment only."
            )

        call_command("flush", interactive=False, verbosity=0)

        with transaction.atomic():
            admin = self._create_operator(options)
            site = self._configure_site()
            policy = self._seed_policy()
            category, product = self._seed_catalog()

        self.stdout.write(
            self.style.SUCCESS(
                "E2E database ready: "
                f"operator={admin.email} site={site.domain} "
                f"policy={policy.pk} category={category.pk} product={product.pk}"
            )
        )

    def _create_operator(self, options):
        User = get_user_model()
        admin = User.objects.create(
            email=options["admin_email"],
            username=options["admin_email"],
            name=options["admin_name"],
            role=User.Role.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )
        admin.set_password(options["admin_password"])
        admin.save(update_fields=["password"])
        return admin

    def _configure_site(self):
        site, _created = Site.objects.update_or_create(
            pk=settings.SITE_ID,
            defaults={
                "domain": settings.SITE_DOMAIN or "localhost:8000",
                "name": settings.SITE_NAME or "Printy E2E",
            },
        )
        return site

    def _seed_policy(self):
        policy, _created = PlatformFeePolicy.objects.update_or_create(
            name="Default Printy Fee Policy",
            defaults={
                "is_active": True,
                "policy_version": "printy-fees-v1",
                "currency": "KES",
                "under_production_threshold": Decimal("1000.00"),
                "under_production_fee_rate": Decimal("0.3000"),
                "standard_production_fee_rate": Decimal("0.2000"),
                "standard_markup_fee_rate": Decimal("0.2000"),
                "high_markup_threshold": Decimal("0.8000"),
                "high_markup_fee_rate": Decimal("0.3000"),
                "high_production_threshold": Decimal("10000.00"),
                "high_production_fee_rate": Decimal("0.3000"),
                "high_production_markup_fee_rate": Decimal("0.4000"),
                "maximum_manager_markup_multiple": Decimal("2.00"),
            },
        )
        return policy

    def _seed_catalog(self):
        category, _created = ProductCategory.objects.update_or_create(
            slug="business-cards",
            shop=None,
            defaults={
                "name": "Business Cards",
                "description": "Standard sheet-fed business cards.",
                "is_active": True,
            },
        )
        product, _created = Product.objects.update_or_create(
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
        return category, product
