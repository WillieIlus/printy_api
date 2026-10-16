"""Law 7.10a: "printer price" means the printer's own rate-card quote.

Law 7.10a requires:

  1. ``printer_quoted_price`` is what the printer charges on their own rate
     card, BEFORE any Printy printer-side fee is deducted and BEFORE any manager
     markup / Printy fee is added.
  2. It is a distinct field from ``printer_payout`` (what the printer receives
     after the printer-side fee).
  3. It must not move when Printy fee settings or the manager markup change.
  4. The manager find-printer response must carry no ``printer_payout``,
     ``printer_side_fee``, manager markup, Printy fee or buyer price.
  5. The manager comparison ranks shops by ``printer_quoted_price``.

These tests assert those properties against the real matching service and the
live manager shop-options endpoint.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from pricing.services.platform_fee_policy import calculate_financial_split
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from services.pricing.engine import calculate_sheet_pricing
from services.pricing.role_projection import (
    BUYER_FORBIDDEN_FIELDS,
    ROLE_BUYER,
    ROLE_MANAGER,
    ROLE_PRINTER,
    build_canonical_result,
    project_canonical,
)
from services.production_matching import build_partner_production_matches
from shops.models import Shop


def _payload(quantity=100):
    return {
        "product_type": "business_card",
        "quantity": quantity,
        "finished_size": "90x55mm",
        "requested_paper_category": "gloss",
        "requested_gsm": 300,
        "print_sides": "SIMPLEX",
        "color_mode": "COLOR",
    }


class Law710APrinterPriceTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Law 7.10a policy",
            is_active=True,
            add_platform_fee_on_top=False,
        )

        self.manager = User.objects.create_user(
            email="law710a-manager@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.end_client = User.objects.create_user(
            email="law710a-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )

        self.cheap_shop, self.cheap_paper, self.cheap_machine = self._create_shop(
            "Cheap Press", "law710a-cheap", paper_selling="20.00", print_single="35.00"
        )
        self.expensive_shop, self.expensive_paper, self.expensive_machine = self._create_shop(
            "Expensive Press", "law710a-expensive", paper_selling="40.00", print_single="55.00"
        )

        self.quote_request = QuoteRequest.objects.create(
            shop=None,
            created_by=self.end_client,
            assigned_manager=self.manager,
            customer_name="End Client",
            customer_email=self.end_client.email,
            status=QuoteStatus.SUBMITTED,
            request_snapshot={
                "source": "manager_led_intake",
                "calculator_inputs": {
                    "product_type": "business_card",
                    "quantity": 100,
                    "finished_size": "90x55mm",
                    "requested_paper_category": "gloss",
                    "requested_gsm": 300,
                    "print_sides": "SIMPLEX",
                    "color_mode": "COLOR",
                },
            },
        )

    def _create_shop(self, name, slug, *, paper_selling, print_single):
        owner = User.objects.create_user(
            email=f"{slug}@test.com", password="pass12345", role=User.Role.PRODUCTION
        )
        shop = Shop.objects.create(
            owner=owner,
            name=name,
            slug=slug,
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        machine = Machine.objects.create(
            shop=shop, name=name, max_width_mm=320, max_height_mm=450, is_active=True
        )
        paper = Paper.objects.create(
            shop=shop,
            name="SRA3 Card",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("1.00"),
            selling_price=Decimal(paper_selling),
            width_mm=320,
            height_mm=450,
            is_active=True,
            is_default=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal(print_single),
            double_price=Decimal(print_single) * 2,
            is_active=True,
            is_default=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Cutting",
            slug=f"cutting-{slug}",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )
        return shop, paper, machine

    def _independent_rate_card_price(self, shop, paper, machine, quantity=100):
        cutting = FinishingRate.objects.get(shop=shop, slug=f"cutting-{shop.slug}")
        result = calculate_sheet_pricing(
            shop=shop,
            quantity=quantity,
            paper=paper,
            machine=machine,
            color_mode="COLOR",
            sides="SIMPLEX",
            finishing_selections=[{"rule": cutting, "selected_side": "both"}],
            width_mm=90,
            height_mm=55,
        )
        return Decimal(str(result.to_dict()["totals"]["subtotal"]))

    def _row(self, response, shop):
        return next(row for row in response["results"] if row["shop_id"] == shop.id)

    # -- item 1: the quoted price is the rate-card price -------------------

    def test_printer_quoted_price_equals_independent_rate_card_price(self):
        response = build_partner_production_matches(_payload())
        row = self._row(response, self.cheap_shop)
        expected = self._independent_rate_card_price(
            self.cheap_shop, self.cheap_paper, self.cheap_machine
        )
        self.assertEqual(Decimal(str(row["printer_quoted_price"])), expected)
        self.assertEqual(row["printer_quoted_price"], row["production_cost"])
        self.assertEqual(
            Decimal(str(row["printer_quoted_price_per_piece"])),
            (expected / Decimal("100")).quantize(Decimal("0.01")),
        )

    # -- item 3: fee settings do not move the quoted price -----------------

    def test_printer_quoted_price_unchanged_when_printy_fee_changes(self):
        before = Decimal(
            str(self._row(build_partner_production_matches(_payload()), self.cheap_shop)["printer_quoted_price"])
        )
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Law 7.10a policy v2",
            is_active=True,
            printer_fee_rate=Decimal("0.40"),
            broker_margin_fee_rate=Decimal("0.15"),
            add_platform_fee_on_top=False,
        )
        after = Decimal(
            str(self._row(build_partner_production_matches(_payload()), self.cheap_shop)["printer_quoted_price"])
        )
        self.assertEqual(before, after)

        # The printer payout *does* move with the fee rate, which is exactly why
        # the quoted price must be tracked as a separate field.
        base_policy = calculate_financial_split(
            production_cost=Decimal("1000.00"), manager_markup=Decimal("750.00")
        )
        self.assertNotEqual(base_policy.shop_payout, base_policy.production_cost)

    def test_printer_quoted_price_unchanged_when_manager_markup_changes(self):
        engine_payload = calculate_sheet_pricing(
            shop=self.cheap_shop,
            quantity=100,
            paper=self.cheap_paper,
            machine=self.cheap_machine,
            color_mode="COLOR",
            sides="SIMPLEX",
            width_mm=90,
            height_mm=55,
        ).to_dict()

        canonical_a = build_canonical_result(
            engine_payload,
            financials={
                "production_cost": "1000.00",
                "manager_margin": "100.00",
                "printer_side_fee": "50.00",
                "printer_payout": "950.00",
            },
        )
        canonical_b = build_canonical_result(
            engine_payload,
            financials={
                "production_cost": "1000.00",
                "manager_margin": "900.00",
                "printer_side_fee": "500.00",
                "printer_payout": "500.00",
            },
        )

        self.assertEqual(canonical_a["printer_quoted_price"], canonical_b["printer_quoted_price"])
        self.assertNotEqual(canonical_a["manager_margin"], canonical_b["manager_margin"])
        self.assertNotEqual(canonical_a["printer_payout"], canonical_b["printer_payout"])

        buyer = project_canonical(canonical_a, ROLE_BUYER)
        self.assertNotIn("printer_quoted_price", buyer)
        self.assertIn("printer_quoted_price", BUYER_FORBIDDEN_FIELDS)
        self.assertIn("printer_quoted_price", project_canonical(canonical_a, ROLE_MANAGER))
        self.assertIn("printer_quoted_price", project_canonical(canonical_a, ROLE_PRINTER))

    # -- item 3: quoted price and payout are distinct figures --------------

    def test_quoted_price_and_payout_are_distinct_figures(self):
        split = calculate_financial_split(
            production_cost=Decimal("1000.00"), manager_markup=Decimal("750.00")
        )
        # Law 3.2: printer payout = price - manager margin - fees. The rate-card
        # quote is the input BEFORE those adjustments, so it must differ from the
        # payout once any printer-side fee applies.
        self.assertEqual(split.production_cost, Decimal("1000.00"))
        self.assertNotEqual(split.shop_payout, split.production_cost)

    # -- items 4 + 5: the manager find-printer HTTP response ---------------

    def _shop_options(self):
        self.client.force_authenticate(user=self.manager)
        return self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            {},
            format="json",
        )

    def test_find_printer_response_has_no_payout_fee_or_buyer_price(self):
        response = self._shop_options()
        self.assertEqual(response.status_code, 200)
        payload_text = str(response.json()).lower()
        for forbidden in (
            "printer_payout",
            "printer_side_fee",
            "manager_margin",
            "markup",
            "printy_fee",
            "platform_fee",
            "client_total",
            "client_price",
            "shop_payout",
        ):
            self.assertNotIn(forbidden, payload_text, forbidden)
        self.assertIn("printer_quoted_price", str(response.json()))

    def test_manager_comparison_ranks_by_printer_quoted_price(self):
        response = self._shop_options()
        self.assertEqual(response.status_code, 200)
        rows = [row for row in response.json()["results"] if row["price_status"] == "priced"]
        quotes = [Decimal(str(row["printer_quoted_price"])) for row in rows]
        self.assertEqual(quotes, sorted(quotes))
        self.assertEqual(rows[0]["shop_id"], self.cheap_shop.id)
        self.assertTrue(rows[0]["is_recommended"])
