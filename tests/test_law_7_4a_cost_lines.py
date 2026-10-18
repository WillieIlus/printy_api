"""Law 7.4a (data side): the manager's printer option is never nameless and
carries itemized cost lines that sum to the printer's quoted price.

Law 7.4a requires each printer option in the manager view to show, in order:
the printer shop's name (never blank), the itemized cost lines, the printer's
quote, and the imposition component. Law 7.9: buyer and anonymous payloads
carry no printer identity. Law 1.3: cost lines, imposition/sheet data and shop
names appear for managers only.

These tests exercise the real matching service, the live manager shop-options
endpoint and the role projection.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from services.pricing.engine import calculate_sheet_pricing
from services.pricing.role_projection import (
    ROLE_ANONYMOUS,
    ROLE_BUYER,
    ROLE_MANAGER,
    ROLE_PRINTER,
    build_canonical_result,
    project_canonical,
)
from services.production_matching import build_partner_production_matches
from shops.models import Shop


def _base_inputs(**overrides):
    inputs = {
        "product_type": "business_card",
        "quantity": 100,
        "finished_size": "90x55mm",
        "requested_paper_category": "gloss",
        "requested_gsm": 300,
        "print_sides": "SIMPLEX",
        "color_mode": "COLOR",
    }
    inputs.update(overrides)
    return inputs


def _match_payload(**overrides):
    return {
        "product_type": "business_card",
        "quantity": 100,
        "finished_size": "90x55mm",
        "requested_paper_category": "gloss",
        "requested_gsm": 300,
        "print_sides": "SIMPLEX",
        "color_mode": "COLOR",
        **overrides,
    }


class Law74ACostLineTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Law 7.4a policy",
            is_active=True,
            add_platform_fee_on_top=False,
        )

        self.manager = User.objects.create_user(
            email="law74a-manager@test.com",
            password="pass12345",
            role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.end_client = User.objects.create_user(
            email="law74a-client@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )
        self.buyer = User.objects.create_user(
            email="law74a-buyer@test.com",
            password="pass12345",
            role=User.Role.CLIENT,
        )

        self.shop = self._create_shop("Law 7.4a Press", "law74a-press")
        self.paper = Paper.objects.filter(shop=self.shop).first()
        self.machine = Machine.objects.filter(shop=self.shop).first()

        self.quote_request = QuoteRequest.objects.create(
            shop=None,
            created_by=self.end_client,
            assigned_manager=self.manager,
            customer_name="End Client",
            customer_email=self.end_client.email,
            status=QuoteStatus.SUBMITTED,
            request_snapshot={
                "source": "manager_led_intake",
                "calculator_inputs": _base_inputs(lamination="matte-lamination"),
            },
        )

    def _create_shop(self, name, slug):
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
            shop=shop, name=f"{name} Press", max_width_mm=320, max_height_mm=450, is_active=True
        )
        paper = Paper.objects.create(
            shop=shop,
            name="300gsm Art Card",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("10.00"),
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
            name="Cutting",
            slug=f"cutting-{slug}",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )
        FinishingRate.objects.create(
            shop=shop,
            name="Matt Lamination",
            slug="matte-lamination",
            charge_unit=ChargeUnit.PER_SHEET,
            billing_basis=FinishingBillingBasis.PER_SHEET,
            side_mode=FinishingSideMode.PER_SELECTED_SIDE,
            price=Decimal("25.00"),
            is_active=True,
        )
        return shop

    def _shop_options(self, *, overrides=None):
        self.client.force_authenticate(user=self.manager)
        return self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            overrides or {},
            format="json",
        )

    def _priced_row(self, response, shop=None):
        shop = shop or self.shop
        rows = [row for row in response["results"] if row["shop_id"] == shop.id]
        self.assertTrue(rows, "expected a result for the shop")
        return rows[0]

    # -- item 1: the printer option carries the shop name -----------------

    def test_manager_option_includes_shop_name_for_verified_shop(self):
        response = self._shop_options()
        self.assertEqual(response.status_code, 200, response.json())
        row = self._priced_row(response.json())
        self.assertEqual(row["price_status"], "priced")
        # The manager view reads `name`; it must never be blank.
        self.assertEqual(row["name"], self.shop.name)
        self.assertEqual(row["shop_name"], self.shop.name)
        self.assertTrue(row["name"].strip())

    def test_pricing_snapshot_selected_shops_carry_shop_name(self):
        response = self._shop_options()
        self.assertEqual(response.status_code, 200, response.json())
        snapshot = response.json()["pricing_snapshot"]
        selected = snapshot["selected_shops"]
        self.assertTrue(selected, "expected the shop in the pricing snapshot")
        entry = next(item for item in selected if item["shop_id"] == self.shop.id)
        self.assertEqual(entry["name"], self.shop.name)
        self.assertEqual(entry["shop_name"], self.shop.name)

    def test_blank_shop_name_is_logged_not_rendered_blank(self):
        self.shop.name = "   "
        self.shop.save(update_fields=["name"])
        with self.assertLogs("services.production_matching", level="ERROR") as logs:
            response = build_partner_production_matches(_match_payload())
        self.assertTrue(any("blank name" in message for message in logs.output))
        row = self._priced_row(response)
        self.assertTrue(row["name"].strip())
        self.assertNotEqual(row["name"], "—")

    # -- item 2: itemized cost lines --------------------------------------

    def test_manager_option_carries_itemized_cost_lines(self):
        response = self._shop_options()
        row = self._priced_row(response.json())
        lines = row["cost_lines"]
        self.assertTrue(lines, "expected itemized cost lines")
        for line in lines:
            self.assertIn("label", line)
            self.assertIn("quantity", line)
            self.assertIn("unit_price_kes", line)
            self.assertIn("total_kes", line)
        components = {line["component"] for line in lines}
        # Sheets, printing, lamination and cutting must all be itemized.
        self.assertIn("paper", components)
        self.assertIn("printing", components)
        self.assertIn("cutting", components)
        self.assertTrue(components & {"finishing", "lamination"})
        paper = next(line for line in lines if line["component"] == "paper")
        self.assertIn("SRA3", paper["spec"])

    def test_manager_projection_has_cost_lines_others_do_not(self):
        engine_payload = calculate_sheet_pricing(
            shop=self.shop,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="SIMPLEX",
            width_mm=90,
            height_mm=55,
        ).to_dict()
        canonical = build_canonical_result(engine_payload)
        self.assertIn("cost_lines", project_canonical(canonical, ROLE_MANAGER))
        for role in (ROLE_BUYER, ROLE_PRINTER, ROLE_ANONYMOUS):
            self.assertNotIn("cost_lines", project_canonical(canonical, role))

    # -- item 3: the lines sum to the printer's quoted price exactly ------

    def _assert_lines_sum_to_quote(self, row):
        lines = row["cost_lines"]
        self.assertTrue(lines, "expected cost lines")
        line_total = sum((Decimal(str(line["total_kes"])) for line in lines), Decimal("0.00"))
        quoted = Decimal(str(row["printer_quoted_price"]))
        self.assertEqual(
            line_total,
            quoted,
            f"cost lines {line_total} != printer_quoted_price {quoted}",
        )
        # The reconciling breakdown the engine already computes must agree.
        self.assertTrue(row["production_breakdown"]["breakdown_reconciles"])

    def test_cost_lines_sum_to_quoted_price_simple_spec(self):
        response = build_partner_production_matches(_match_payload())
        self._assert_lines_sum_to_quote(self._priced_row(response))

    def test_cost_lines_sum_to_quoted_price_double_sided_spec(self):
        response = build_partner_production_matches(
            _match_payload(print_sides="DUPLEX")
        )
        self._assert_lines_sum_to_quote(self._priced_row(response))

    def test_cost_lines_sum_to_quoted_price_with_lamination_and_cutting(self):
        response = build_partner_production_matches(
            _match_payload(lamination="matte-lamination")
        )
        row = self._priced_row(response)
        components = {line["component"] for line in row["cost_lines"]}
        self.assertIn("cutting", components)
        self.assertTrue(components & {"finishing", "lamination"})
        self._assert_lines_sum_to_quote(row)

    def test_cost_lines_sum_to_quoted_price_via_endpoint(self):
        response = self._shop_options()
        self._assert_lines_sum_to_quote(self._priced_row(response.json()))

    # -- item 5: role payload tests (law 1.3) -----------------------------

    def test_buyer_and_anonymous_never_see_lines_sheets_or_shop_names(self):
        response = self._shop_options()
        manager_row = self._priced_row(response.json())
        self.assertIn("cost_lines", manager_row)
        self.assertEqual(manager_row["shop_name"], self.shop.name)

        # Law 7.9: the manager option projected for a buyer/anonymous caller
        # never exposes real printer identity or cost lines.
        from api.visibility import (
            CLIENT_ACTOR,
            PUBLIC_ACTOR,
            project_match_summary,
            scrub_response_for_role,
        )

        for actor in (CLIENT_ACTOR, PUBLIC_ACTOR):
            projected = project_match_summary(manager_row, actor=actor, include_identity=False)
            self.assertNotEqual(projected["name"], self.shop.name)
            self.assertNotEqual(projected["shop_name"], self.shop.name)
            self.assertNotIn(self.shop.name, str(projected))
            self.assertNotIn("cost_lines", projected)

        # Law 1.3: a raw manager option scrubbed for a buyer/anonymous caller
        # loses its cost lines and nested imposition/sheet keys.
        for role in (ROLE_BUYER, ROLE_ANONYMOUS):
            scrubbed = scrub_response_for_role(manager_row, role)
            self.assertNotIn("cost_lines", scrubbed)
            self.assertNotIn("good_sheets", scrubbed)
            self.assertNotIn("press_sheet", scrubbed)
