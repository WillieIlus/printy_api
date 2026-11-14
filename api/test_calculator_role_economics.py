"""Role-aware calculator economics over the public preview endpoint.

The calculator has ONE canonical split (production cost + manager markup +
Printy fee). Each role receives a projection of it:

* anonymous/buyer  -> the unchanged public marketplace shape, no economics;
* manager          -> production lines plus the manager's markup and the
                      client-inclusive total;
* printer          -> raw production lines and its own payout, never the buyer
                      total, client range, speed prices or margin.

These tests drive the real ``POST /api/calculator/public-preview/`` endpoint so
the role selection (``X-Printy-Active-Role``/account role) and the response
projection are exercised together.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from shops.models import Shop


ENDPOINT = "/api/calculator/public-preview/"


class CalculatorRoleEconomicsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Role economics policy",
            is_active=True,
            printer_fee_rate=Decimal("0.05"),
            broker_margin_fee_rate=Decimal("0.15"),
            add_platform_fee_on_top=False,
        )
        # Two identically priced shops give the median an exact match, so the
        # role lines must reconcile to the split without residual fudging.
        self._create_shop("Role Economics A", "role-economics-a")
        self._create_shop("Role Economics B", "role-economics-b")

        self.buyer = User.objects.create_user(
            email="role-econ-buyer@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.manager = User.objects.create_user(
            email="role-econ-manager@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
        )
        self.printer = User.objects.create_user(
            email="role-econ-printer@test.com",
            password="pass12345",
            role=User.Role.PRODUCTION,
        )

    def _create_shop(self, name, slug):
        owner = User.objects.create_user(
            email=f"{slug}-owner@test.com",
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
        )
        machine = Machine.objects.create(
            shop=shop,
            name=f"{name} Press",
            max_width_mm=320,
            max_height_mm=450,
            is_active=True,
        )
        Paper.objects.create(
            shop=shop,
            name="300gsm Matt",
            sheet_size="SRA3",
            gsm=300,
            paper_type="MATT",
            category="matt",
            buying_price=Decimal("1.00"),
            selling_price=Decimal("20.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
            is_default=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
            is_default=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Matt lamination",
            slug=f"matt-lamination-{slug}",
            charge_unit=ChargeUnit.PER_PIECE,
            billing_basis=FinishingBillingBasis.PER_PIECE,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("0.10"),
            is_active=True,
        )
        return shop

    def _payload(self):
        return {
            "product_type": "business_card",
            "quantity": 100,
            "finished_size": "90x55mm",
            "requested_paper_category": "matt",
            "requested_gsm": 300,
            "print_sides": "DUPLEX",
            "color_mode": "COLOR",
            "lamination": "matt-lamination",
        }

    def _post(self, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        response = client.post(ENDPOINT, self._payload(), format="json")
        return response

    def _assert_no_role_economics(self, payload):
        self.assertNotIn("role_economics", payload, payload)
        self.assertIsNone(payload.get("production_preview"))
        # The buyer/anonymous calculator keeps the public range and speed prices.
        self.assertIn("market_range", payload)
        self.assertIn("speed_options", payload)

    def test_anonymous_preview_has_no_role_economics(self):
        response = self._post()
        self.assertEqual(response.status_code, 200, response.json())
        self._assert_no_role_economics(response.json())

    def test_buyer_preview_matches_the_anonymous_shape(self):
        response = self._post(self.buyer)
        self.assertEqual(response.status_code, 200, response.json())
        payload = response.json()
        self._assert_no_role_economics(payload)
        anonymous = self._post().json()
        self.assertEqual(set(payload), set(anonymous))
        self.assertEqual(payload["market_range"], anonymous["market_range"])

    def test_manager_receives_cost_plus_markup_and_client_inclusive_total(self):
        response = self._post(self.manager)
        self.assertEqual(response.status_code, 200, response.json())
        payload = response.json()

        # Managers still get the client range/speed choices plus production
        # detail; the difference is the canonical split beneath them.
        self.assertIn("market_range", payload)
        self.assertIn("speed_options", payload)
        self.assertTrue(payload.get("production_preview"), payload)

        economics = payload.get("role_economics")
        self.assertIsNotNone(economics, payload)
        self.assertEqual(economics["role"], "manager")
        self.assertTrue(economics["lines"])

        lines = economics["lines"]
        production = Decimal(str(economics["production_total"]))
        markup = Decimal(str(economics["markup_total"]))
        manager_total = Decimal(str(economics["manager_total"]))
        self.assertGreater(production, Decimal("0.00"))
        self.assertGreater(markup, Decimal("0.00"))
        self.assertEqual(production + markup, manager_total)
        # The canonical public markup multiple is 1.75x production; whole-KES
        # rounding in the split moves the reported percentage by a few cents.
        expected_markup_percent = (markup / production * Decimal("100")).quantize(
            Decimal("0.01")
        )
        self.assertEqual(
            Decimal(str(economics["markup_percent"])), expected_markup_percent
        )
        self.assertGreater(Decimal(str(economics["markup_percent"])), Decimal("70.00"))
        self.assertEqual(manager_total, Decimal(str(economics["client_total_inclusive"])))

        line_production = sum(Decimal(str(line["production_total"])) for line in lines)
        line_markup = sum(Decimal(str(line["markup_amount"])) for line in lines)
        line_billed = sum(Decimal(str(line["total"])) for line in lines)
        self.assertEqual(line_production + line_markup, manager_total)
        self.assertEqual(line_billed, manager_total)
        for line in lines:
            self.assertEqual(
                Decimal(str(line["total"])),
                Decimal(str(line["production_total"])) + Decimal(str(line["markup_amount"])),
            )
            # The manager sees the raw printer rate behind each billed rate.
            self.assertTrue(line["label"])
            self.assertIsNotNone(line.get("production_rate"))
            self.assertIsNotNone(line.get("rate"))

    def test_printer_receives_raw_production_lines_and_payout_only(self):
        response = self._post(self.printer)
        self.assertEqual(response.status_code, 200, response.json())
        payload = response.json()

        # A printer never receives the buyer's total, range, median or speed
        # prices; their calculator is priced from production cost instead.
        for forbidden in (
            "total",
            "min_price",
            "max_price",
            "estimate_min",
            "estimate_max",
            "estimate_median",
            "median_price",
            "display_price_text",
            "display_mode",
            "market_range",
            "speed_options",
        ):
            self.assertNotIn(forbidden, payload, forbidden)

        self.assertTrue(payload.get("production_preview"), payload)

        economics = payload.get("role_economics")
        self.assertIsNotNone(economics, payload)
        self.assertEqual(economics["role"], "printer")
        self.assertTrue(economics["lines"])

        lines = economics["lines"]
        production = Decimal(str(economics["production_total"]))
        self.assertEqual(
            sum(Decimal(str(line["total"])) for line in lines),
            production,
        )
        for line in lines:
            self.assertTrue(line["label"])
            # Raw production rates only: no billed figure, no markup.
            self.assertIsNotNone(line.get("rate"))
            self.assertNotIn("markup_amount", line)
            self.assertNotIn("production_rate", line)

        self.assertIn("printer_payout", economics)
        self.assertGreater(Decimal(str(economics["printer_payout"])), Decimal("0.00"))

        # Manager-only and buyer-only money never reaches a printer.
        for forbidden in (
            "markup_percent",
            "markup_total",
            "manager_total",
            "printy_fee",
            "client_total_inclusive",
        ):
            self.assertNotIn(forbidden, economics, forbidden)

    def test_manager_and_printer_agree_on_production_total(self):
        manager = self._post(self.manager).json()["role_economics"]
        printer = self._post(self.printer).json()["role_economics"]
        self.assertEqual(
            Decimal(str(manager["production_total"])),
            Decimal(str(printer["production_total"])),
        )
        self.assertEqual(
            sum(Decimal(str(line["production_total"])) for line in manager["lines"]),
            sum(Decimal(str(line["total"])) for line in printer["lines"]),
        )

    def test_get_preview_also_projects_role_economics(self):
        client = APIClient()
        client.force_authenticate(user=self.manager)
        response = client.get(ENDPOINT, self._payload())
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["role_economics"]["role"], "manager")
