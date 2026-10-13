"""Law 3.5 omnibus test: one spec through all roles.

Runs a single real spec through the one engine (``calculate_sheet_pricing``),
builds the canonical result, projects it for buyer/manager/printer/anonymous,
and asserts:

  3.1  buyer total == manager price for the same job/version
  3.2  printer payout == price - manager margin - fees
  3.3  sheets billed shown to manager and printer are identical
  plus buyer and anonymous payloads contain none of the forbidden fields in 1.3.

It also posts the same spec to the anonymous public calculator endpoint and
asserts the HTTP payload carries no forbidden fields.
"""

from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate
from services.pricing.engine import calculate_sheet_pricing
from services.pricing.role_projection import (
    BUYER_FORBIDDEN_FIELDS,
    ROLE_ANONYMOUS,
    ROLE_BUYER,
    ROLE_MANAGER,
    ROLE_PRINTER,
    assert_projection_is_clean,
    build_canonical_result,
    project_canonical,
)
from shops.models import Shop


FORBIDDEN_IN_PAYLOAD = (
    "good_sheets",
    "billable_sheets",
    "pieces_per_sheet",
    "sheets_required",
    "press_sheet",
    "cost_breakdown",
    "manager_margin",
    "fee_tier",
    "printer_payout",
    "printer_side_fee",
)


class Law35RoleMatrixTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        PlatformFeePolicy.objects.update(is_active=False)
        PlatformFeePolicy.objects.create(
            name="Law 3.5 policy",
            is_active=True,
            printer_fee_rate=Decimal("0.05"),
            broker_margin_fee_rate=Decimal("0.15"),
            add_platform_fee_on_top=False,
        )
        owner = User.objects.create_user(
            email="law35-owner@test.com", password="pass12345", role=User.Role.PRODUCTION
        )
        self.shop = Shop.objects.create(
            owner=owner,
            name="Law 3.5 Press",
            slug="law35",
            is_active=True,
            is_public=True,
            city="Nairobi",
            service_area="Westlands",
        )
        self.machine = Machine.objects.create(
            shop=self.shop, name="Law 3.5 Press", max_width_mm=320, max_height_mm=450, is_active=True
        )
        self.paper = Paper.objects.create(
            shop=self.shop,
            name="SRA3 Card",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            category="gloss",
            buying_price=Decimal("1.00"),
            selling_price=Decimal("20.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
            is_default=True,
        )
        PrintingRate.objects.create(
            machine=self.machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
            is_default=True,
        )
        FinishingRate.objects.create(
            shop=self.shop,
            name="Cutting",
            slug="cutting-law35",
            charge_unit=ChargeUnit.FLAT,
            billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES,
            price=Decimal("50.00"),
            is_active=True,
        )

    def _engine_result(self):
        return calculate_sheet_pricing(
            shop=self.shop,
            quantity=100,
            paper=self.paper,
            machine=self.machine,
            color_mode="COLOR",
            sides="SIMPLEX",
            width_mm=90,
            height_mm=55,
        )

    def test_one_spec_through_all_roles(self):
        result = self._engine_result()
        engine_payload = result.to_dict()
        price = Decimal(str(engine_payload["totals"]["grand_total"]))
        margin = Decimal("100.00")
        fee = (price * Decimal("0.05")).quantize(Decimal("0.01"))
        payout = price - margin - fee

        canonical = build_canonical_result(
            engine_payload,
            spec_summary="100 business cards, 90x55mm, gloss 300gsm, simplex",
            ready_date="2026-11-01",
            delivery="Nairobi, KES 250",
            job_status="quoted",
            current_holder="Print Manager",
            financials={
                "manager_margin": str(margin),
                "printer_side_fee": str(fee),
                "printer_payout": str(payout),
            },
        )

        buyer = project_canonical(canonical, ROLE_BUYER)
        manager = project_canonical(canonical, ROLE_MANAGER)
        printer = project_canonical(canonical, ROLE_PRINTER)
        anonymous = project_canonical(canonical, ROLE_ANONYMOUS)

        assert_projection_is_clean(buyer, ROLE_BUYER)
        assert_projection_is_clean(anonymous, ROLE_ANONYMOUS)

        # 3.1 buyer total == manager price for the same job/version.
        self.assertEqual(buyer["price"], manager["price"])
        self.assertEqual(Decimal(str(buyer["price"])), price)

        # 3.2 printer payout == price - manager margin - fees.
        self.assertEqual(Decimal(str(printer["printer_payout"])), payout)
        self.assertEqual(
            Decimal(str(printer["printer_payout"])),
            Decimal(str(canonical["price"]))
            - Decimal(str(canonical["manager_margin"]))
            - Decimal(str(canonical["printer_side_fee"])),
        )

        # 3.3 sheets billed shown to manager and printer are identical.
        self.assertIsNotNone(manager["sheets_billed"])
        self.assertEqual(manager["sheets_billed"], printer["sheets_billed"])

        # Buyer and anonymous payloads contain none of the forbidden 1.3 fields.
        for field in BUYER_FORBIDDEN_FIELDS:
            self.assertNotIn(field, buyer, field)
            self.assertNotIn(field, anonymous, field)

    def test_error_response_has_no_forbidden_fields(self):
        # Item 6: a client-facing error must not echo any forbidden pricing field.
        response = self.client.post("/api/calculator/preview/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.json())
        blob = str(response.json())
        for field in FORBIDDEN_IN_PAYLOAD:
            self.assertNotIn(field, blob, field)

    def test_anonymous_public_calculator_payload_has_no_forbidden_fields(self):
        response = self.client.post(
            "/api/calculator/public-preview/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "custom",
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
                "requested_paper_category": "gloss",
                "requested_gsm": 300,
                "width_mm": 90,
                "height_mm": 55,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.json())
        blob = str(response.json())
        for field in FORBIDDEN_IN_PAYLOAD:
            self.assertNotIn(field, blob, field)
