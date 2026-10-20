"""Phase 3A Task A6: the backfill is retired to a READ-ONLY report.

D1 (Wilson, binding): there is NO blanket/automatic backfill of is_confirmed.
The report lists shops that are usable but unconfirmed (so owners can be
nudged) and incomplete shops with what each is missing. It must never write,
and must never print raw owner emails or phones.
"""

import io
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ColorMode
from pricing.models import PrintingRate, ShopRateCardSetup
from shops.models import Shop


class ReportRateCardReadinessTests(TestCase):
    def _shop(self, name, slug, *, with_pricing=True):
        owner = User.objects.create_user(
            email=f"owner-{slug}@example.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )
        shop = Shop.objects.create(
            owner=owner,
            name=name,
            slug=slug,
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
            public_whatsapp_number="+254712345678",
        )
        if with_pricing:
            machine = Machine.objects.create(
                shop=shop,
                name=f"{name} Press",
                max_width_mm=320,
                max_height_mm=450,
                is_active=True,
            )
            Paper.objects.create(
                shop=shop,
                name="300gsm Art Card",
                sheet_size="SRA3",
                gsm=300,
                paper_type="GLOSS",
                buying_price=Decimal("10.00"),
                selling_price=Decimal("20.00"),
                width_mm=320,
                height_mm=450,
                is_active=True,
            )
            PrintingRate.objects.create(
                machine=machine,
                sheet_size="SRA3",
                color_mode=ColorMode.COLOR,
                single_price=Decimal("35.00"),
                double_price=Decimal("70.00"),
                is_active=True,
            )
        return shop

    def test_report_lists_usable_unconfirmed_and_incomplete(self):
        usable = self._shop("Usable Shop", "report-usable")
        incomplete = self._shop("Incomplete Shop", "report-incomplete", with_pricing=False)
        ShopRateCardSetup.objects.create(shop=usable, is_confirmed=False)
        ShopRateCardSetup.objects.create(shop=incomplete, is_confirmed=False)

        out = io.StringIO()
        call_command("report_rate_card_readiness", stdout=out)
        text = out.getvalue()

        self.assertIn("report-usable", text)
        self.assertIn("report-incomplete", text)
        # A shop is auto-created with a default machine (shops/signals.py), so
        # the genuinely missing steps for a shop without pricing are the paper
        # stock and the printing rate.
        self.assertIn("Add priced paper stock", text)
        self.assertIn("Set a printing rate", text)

    def test_report_never_writes(self):
        usable = self._shop("Usable Shop", "report-no-write")
        ShopRateCardSetup.objects.create(shop=usable, is_confirmed=False)

        call_command("report_rate_card_readiness", stdout=io.StringIO())

        self.assertFalse(ShopRateCardSetup.objects.get(shop=usable).is_confirmed)

    def test_report_masks_owner_email_and_phone(self):
        usable = self._shop("Usable Shop", "report-mask")
        ShopRateCardSetup.objects.create(shop=usable, is_confirmed=False)

        out = io.StringIO()
        call_command("report_rate_card_readiness", stdout=out)
        text = out.getvalue()

        self.assertNotIn("owner-report-mask@example.com", text)
        self.assertNotIn("+254712345678", text)

    def test_confirmed_shops_are_not_listed(self):
        confirmed = self._shop("Confirmed Shop", "report-confirmed")
        ShopRateCardSetup.objects.create(shop=confirmed, is_confirmed=True)

        out = io.StringIO()
        call_command("report_rate_card_readiness", stdout=out)

        self.assertNotIn("report-confirmed", out.getvalue())
