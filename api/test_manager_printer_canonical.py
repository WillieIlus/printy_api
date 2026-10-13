"""Law 1.3 manager/printer job projection + law 2.x manager adjustments.

The manager and printer job views must render the same canonical result
projected to their allowlist: the manager sees production + cost breakdown,
own margin, fee tier and printer payout; the printer sees production + own
payout only, never the buyer total, per-piece price, manager margin, cost
breakdown or fee tier. Manager adjustments are stored overrides (law 2.1) and
must preview the buyer-facing price impact before saving (law 2.3).
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from api.canonical_jobs import build_job_canonical, build_job_projection
from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobAssignmentStatus,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
)
from jobs.models import JobAssignment, ManagedJob
from jobs.serializers import JobAssignmentSerializer
from pricing.models import PlatformFeePolicy
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import ProductionOption, Quote, QuoteOverride, QuoteRequest
from services.pricing.role_projection import (
    ROLE_BUYER,
    ROLE_MANAGER,
    ROLE_PRINTER,
)
from shops.models import Shop

User = get_user_model()

PRODUCTION_PREVIEW = {
    "quantity": 100,
    "currency": "KES",
    "totals": {"grand_total": "1500.00", "unit_price": "15.00"},
    "breakdown": {
        "imposition": {"layout": {"cols": 2, "rows": 5, "orientation": "normal"}},
        "paper": {"sheet_size": "SRA3", "quantity": 20, "unit_price": "10.00", "total": "200.00"},
        "printing": {
            "machine_name": "Digital Press",
            "color_mode": "COLOR",
            "sides": "DUPLEX",
            "unit_price": "5.00",
            "total": "100.00",
        },
        "finishings": [],
    },
    "copies_per_sheet": 10,
    "good_sheets": 10,
    "billable_sheets": 12,
    "parent_sheet_name": "SRA3",
    "fixed_waste_sheets": 1,
    "variable_waste_sheets": 1,
    "waste_sheets_added": 2,
    "maximum_spoilage_rate": "50",
    "maximum_spoilage_sheets": 5,
    "spoilage_capped": False,
    "can_calculate": True,
    "reason": "",
}


class ManagerPrinterCanonicalTestCase(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            email="law13-manager@example.com", password="pass", role=User.Role.PARTNER
        )
        self.client_user = User.objects.create_user(
            email="law13-client@example.com", password="pass", role=User.Role.CLIENT
        )
        self.shop_owner = User.objects.create_user(
            email="law13-shop@example.com", password="pass", role=User.Role.PRODUCTION
        )
        self.shop = Shop.objects.create(
            owner=self.shop_owner, name="Law 1.3 Print Shop", slug="law13-shop", is_active=True
        )
        self.policy = PlatformFeePolicy.objects.create(
            name="Law 1.3 policy",
            is_active=True,
            printer_fee_rate=Decimal("0.0500"),
            broker_margin_fee_rate=Decimal("0.1500"),
            add_platform_fee_on_top=False,
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            assigned_manager=self.manager,
            customer_name="Law 1.3 Client",
            status=QuoteStatus.QUOTED,
            request_snapshot={
                "calculator_inputs": {"product_type": "business_cards", "quantity": 100},
                "production_preview_snapshot": PRODUCTION_PREVIEW,
            },
        )
        self.production_option = ProductionOption.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_cost=Decimal("1000.00"),
            created_by=self.manager,
            status=ProductionOption.SELECTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.quote_request,
            shop=self.shop,
            production_option=self.production_option,
            created_by=self.manager,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1500.00"),
            sent_at=timezone.now(),
            response_snapshot={
                "production_preview": PRODUCTION_PREVIEW,
                "totals": {"grand_total": "1500.00", "unit_price": "15.00"},
                "pricing": {"grand_total": "1500.00"},
            },
        )
        create_quote_financial_split(
            quote=self.quote,
            production_option=self.production_option,
            production_cost=Decimal("1000.00"),
            broker_client_price=Decimal("1500.00"),
            policy=self.policy,
        )
        self.job = ManagedJob.objects.create(
            source_quote_request=self.quote_request,
            source_quote=self.quote,
            client=self.client_user,
            broker=self.manager,
            assigned_shop=self.shop,
            status=ManagedJobStatus.ASSIGNED,
            payment_status=ManagedJobPaymentStatus.CONFIRMED,
            assignment_status=ManagedJobAssignmentStatus.ASSIGNED,
            title="Law 1.3 job",
        )
        self.assignment = JobAssignment.objects.create(
            managed_job=self.job,
            assigned_shop=self.shop,
            status=JobAssignmentStatus.PENDING,
            shop_payout=self.quote.financial_split.shop_payout,
        )

    # --- Manager projection (item 1) ---------------------------------------
    def test_manager_projection_carries_production_and_financials(self):
        manager = build_job_projection(self.job, ROLE_MANAGER)

        self.assertEqual(manager["press_sheet"], "SRA3")
        self.assertEqual(manager["ups"], 10)
        self.assertEqual(manager["good_sheets"], 10)
        self.assertEqual(manager["sheets_billed"], 12)
        self.assertEqual(manager["layout"], {"cols": 2, "rows": 5, "orientation": "normal"})
        self.assertEqual(manager["method"]["machine_name"], "Digital Press")
        self.assertIsNotNone(manager["manager_margin"])
        self.assertIsNotNone(manager["printer_payout"])
        self.assertIsNotNone(manager["fee_tier"])
        self.assertEqual(manager["price"], "1500.00")

    def test_manager_detail_endpoint_includes_canonical_projection(self):
        self.client.force_login(self.manager)

        response = self.client.get(
            reverse("dashboard-partner-job-detail", kwargs={"pk": self.job.pk})
        )

        self.assertEqual(response.status_code, 200)
        canonical = response.json()["canonical"]
        self.assertEqual(canonical["press_sheet"], "SRA3")
        self.assertEqual(canonical["sheets_billed"], 12)
        self.assertIn("cost_breakdown", canonical)
        self.assertIn("manager_margin", canonical)
        self.assertIn("printer_payout", canonical)

    # --- Printer projection (item 2) ---------------------------------------
    def test_printer_sees_production_and_own_payout_only(self):
        printer = build_job_projection(self.job, ROLE_PRINTER)

        for present in ("press_sheet", "layout", "ups", "good_sheets", "sheets_billed", "method", "printer_payout", "spec_summary"):
            self.assertIn(present, printer, present)
        for forbidden in ("price", "per_piece", "manager_margin", "fee_tier", "cost_breakdown", "printer_side_fee"):
            self.assertNotIn(forbidden, printer, forbidden)

    def test_printer_assignment_serializer_exposes_canonical_only_for_printer(self):
        request = APIRequestFactory().get("/")
        request.user = self.shop_owner

        data = JobAssignmentSerializer(self.assignment, context={"request": request}).data

        canonical = data["canonical"]
        self.assertEqual(canonical["good_sheets"], 10)
        self.assertIsNotNone(canonical["printer_payout"])
        self.assertNotIn("price", canonical)
        self.assertNotIn("manager_margin", canonical)
        self.assertNotIn("fee_tier", canonical)
        # The buyer price never rides along on the assignment row itself.
        self.assertNotIn("client_total", data)

    # --- Same backend result (item 4) --------------------------------------
    def test_manager_and_printer_project_the_same_canonical_result(self):
        canonical = build_job_canonical(self.job)

        manager = build_job_projection(self.job, ROLE_MANAGER)
        printer = build_job_projection(self.job, ROLE_PRINTER)

        # Both projections are subsets of the one canonical object.
        self.assertEqual(manager["price"], canonical["price"])
        self.assertEqual(printer["sheets_billed"], canonical["sheets_billed"])
        self.assertEqual(manager["good_sheets"], printer["good_sheets"])

    # --- Manager adjustments (item 3) --------------------------------------
    def test_override_preview_shows_buyer_impact_without_persisting(self):
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("dashboard-partner-job-override-preview", kwargs={"job_id": self.job.pk}),
            {"field": "markup", "new_value": {"amount": "100.00"}},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["buyer_price_impact"]["changed"])
        self.assertEqual(Decimal(body["buyer_price_impact"]["old_total"]), Decimal("1500.00"))
        self.assertEqual(Decimal(body["buyer_price_impact"]["new_total"]), Decimal("1600.00"))
        self.assertEqual(Decimal(body["buyer"]["price"]), Decimal("1600.00"))
        self.assertEqual(QuoteOverride.objects.count(), 0)
        self.quote_request.refresh_from_db()
        self.assertFalse(self.quote_request.price_updated)

    def test_override_create_persists_and_flags_buyer_price_update(self):
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("dashboard-partner-job-override", kwargs={"job_id": self.job.pk}),
            {"field": "markup", "new_value": {"amount": "100.00"}, "reason": "Rush"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(QuoteOverride.objects.filter(quote_request=self.quote_request).count(), 1)
        self.quote_request.refresh_from_db()
        self.assertTrue(self.quote_request.price_updated)
        self.assertEqual(Decimal(body["buyer"]["price"]), Decimal("1600.00"))
        self.assertEqual(Decimal(str(body["manager"]["manager_margin"])), Decimal("100.00"))

    def test_override_rejects_unknown_field(self):
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("dashboard-partner-job-override-preview", kwargs={"job_id": self.job.pk}),
            {"field": "not_a_field", "new_value": 1},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_printer_cannot_preview_manager_overrides(self):
        self.client.force_login(self.shop_owner)

        response = self.client.post(
            reverse("dashboard-partner-job-override-preview", kwargs={"job_id": self.job.pk}),
            {"field": "markup", "new_value": {"amount": "100.00"}},
            content_type="application/json",
        )

        self.assertIn(response.status_code, (403, 404))
