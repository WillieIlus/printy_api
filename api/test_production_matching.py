from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PrintingRate
from quotes.choices import QuoteStatus
from quotes.models import Quote, QuoteRequest
from jobs.models import ManagedJob
from shops.models import Shop


class ProductionMatchingPhaseD1TestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.manager = User.objects.create_user(
            email="phase-d1-manager@test.com",
            password="pass12345",
            role="broker",
            partner_profile_enabled=True,
            name="Phase D1 Manager",
        )
        self.other_manager = User.objects.create_user(
            email="phase-d1-other@test.com",
            password="pass12345",
            role="broker",
            partner_profile_enabled=True,
            name="Other Manager",
        )
        self.admin = User.objects.create_user(
            email="phase-d1-admin@test.com",
            password="pass12345",
            role="admin",
            is_staff=True,
            name="Admin",
        )
        self.end_client = User.objects.create_user(
            email="phase-d1-client@test.com",
            password="pass12345",
            role="client",
            name="End Client",
        )
        self.cheapest_shop = self._create_shop_with_pricing("Cheapest Shop", "phase-d1-cheapest", paper_price="20.00", single_price="35.00")
        self.expensive_shop = self._create_shop_with_pricing("Expensive Shop", "phase-d1-expensive", paper_price="40.00", single_price="55.00")
        self.unpriced_shop = self._create_shop_with_pricing("Needs Setup Shop", "phase-d1-needs-setup", paper_price="25.00", single_price=None)
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
                    "finished_size": "85x55mm",
                    "paper_stock": "300gsm",
                    "print_sides": "SIMPLEX",
                    "color_mode": "COLOR",
                },
            },
        )

    def _create_shop_with_pricing(self, name: str, slug: str, *, paper_price: str, single_price: str | None):
        owner = User.objects.create_user(email=f"{slug}@test.com", password="pass12345", role="shop_owner")
        shop = Shop.objects.create(
            owner=owner,
            name=name,
            slug=slug,
            is_active=True,
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
            name="300gsm Art Card",
            sheet_size="SRA3",
            gsm=300,
            paper_type="GLOSS",
            buying_price=Decimal("10.00"),
            selling_price=Decimal(paper_price),
            width_mm=320,
            height_mm=450,
            is_active=True,
        )
        if single_price is not None:
            PrintingRate.objects.create(
                machine=machine,
                sheet_size="SRA3",
                color_mode="COLOR",
                single_price=Decimal(single_price),
                double_price=Decimal("70.00"),
                is_active=True,
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

    def _shop_options(self, user, quote_request=None):
        self.client.force_authenticate(user=user)
        return self.client.post(
            f"/api/dashboard/partner/quotes/{(quote_request or self.quote_request).id}/shop-options/",
            {},
            format="json",
        )

    def test_assigned_manager_can_fetch_ranked_production_options(self):
        response = self._shop_options(self.manager)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["product_type"], "business_card")
        self.assertEqual(payload["visibility"]["audience"], "manager")
        self.assertTrue(payload["visibility"]["exposes_shop_identity"])
        self.assertTrue(payload["visibility"]["exposes_internal_economics"])
        self.assertEqual(payload["results"][0]["shop_name"], "Cheapest Shop")
        self.assertEqual(payload["results"][0]["price_status"], "priced")
        self.assertEqual(payload["results"][0]["pricing_source"], "canonical_rate")
        self.assertEqual(payload["results"][0]["recommendation_label"], "Recommended")
        self.assertEqual(payload["results"][-1]["price_status"], "missing_pricing")
        self.assertIn("selected_shops", payload["pricing_snapshot"])

    def test_partner_production_matches_endpoint_returns_priced_and_diagnostic_rows(self):
        self.client.force_authenticate(user=self.manager)

        response = self.client.post(
            "/api/partner/production-matches/",
            {
                "calculator_context": "broker_dashboard",
                "intent": "source_production",
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "85x55mm",
                "paper_stock": "300gsm",
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        rows = {row["shop_name"]: row for row in response.json()["results"]}
        self.assertEqual(rows["Cheapest Shop"]["price_status"], "priced")
        self.assertEqual(rows["Cheapest Shop"]["pricing_source"], "canonical_rate")
        self.assertEqual(rows["Needs Setup Shop"]["price_status"], "missing_pricing")

    def test_other_manager_cannot_fetch_assigned_request_options(self):
        response = self._shop_options(self.other_manager)
        self.assertEqual(response.status_code, 404)

    def test_client_cannot_fetch_assigned_request_options(self):
        response = self._shop_options(self.end_client)
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_user_cannot_fetch_assigned_request_options(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            {},
            format="json",
        )
        self.assertIn(response.status_code, {401, 403})

    def test_staff_admin_can_fetch_assigned_request_options(self):
        response = self._shop_options(self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()["matched_count"], 1)

    def test_missing_pricing_data_returns_diagnostics_without_crashing(self):
        response = self._shop_options(self.manager)
        rows = {row["shop_name"]: row for row in response.json()["results"]}

        self.assertEqual(rows["Needs Setup Shop"]["price_status"], "missing_pricing")
        self.assertIn("pricing", rows["Needs Setup Shop"]["missing_requirements"])
        self.assertFalse(rows["Needs Setup Shop"]["price_available"])

    def test_missing_specs_returns_clear_diagnostics_without_shop_leak(self):
        quote_request = QuoteRequest.objects.create(
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
                },
            },
        )

        response = self._shop_options(self.manager, quote_request=quote_request)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("finished_size", payload["missing_fields"])
        self.assertIn("paper_stock", payload["missing_fields"])
        self.assertEqual(payload["results"], [])

    def test_public_calculator_preview_does_not_leak_production_options(self):
        response = self.client.post(
            "/api/public/match-shops/",
            {
                "pricing_mode": "custom",
                "product_family": "flat",
                "quantity": 100,
                "width_mm": 85,
                "height_mm": 55,
                "paper_gsm": 300,
                "paper_type": "GLOSS",
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload_text = str(response.json())
        self.assertNotIn("estimated_production_cost", payload_text)
        self.assertNotIn("estimated_shop_payout", payload_text)
        self.assertNotIn("Cheapest Shop", payload_text)

    def test_manager_prefill_uses_canonical_lamination_slug(self):
        self.quote_request.request_snapshot["calculator_inputs"]["lamination"] = "matte-lamination"
        self.quote_request.save(update_fields=["request_snapshot"])
        self.client.force_authenticate(user=self.manager)

        response = self.client.get(f"/api/dashboard/manager/quote-requests/{self.quote_request.id}/prefill/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["finishing"][0]["slug"], "matt-lamination")

    def test_printshop_breakdown_excludes_manager_pricing_fields(self):
        self.quote_request.request_snapshot["calculator_inputs"]["lamination"] = "matte-lamination"
        self.quote_request.save(update_fields=["request_snapshot"])
        job = ManagedJob.objects.create(
            title="Business cards",
            source_quote_request=self.quote_request,
            broker=self.manager,
            assigned_shop=self.cheapest_shop,
        )
        self.client.force_authenticate(user=self.cheapest_shop.owner)

        response = self.client.get(f"/api/dashboard/printshop/jobs/{job.id}/breakdown/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["specs"]["finishing"][0]["slug"], "matt-lamination")
        payload_text = str(payload).lower()
        self.assertNotIn("markup", payload_text)
        self.assertNotIn("platform_fee", payload_text)
        self.assertNotIn("client_total", payload_text)

    def _prepare_assigned_quote(self, *, user=None, shop=None, pricing_snapshot=None, partner_markup="300.00"):
        self.client.force_authenticate(user=user or self.manager)
        payload = {
            "shop": (shop or self.cheapest_shop).id,
            "pricing_snapshot": pricing_snapshot if pricing_snapshot is not None else self._shop_options(self.manager).json()["pricing_snapshot"],
            "partner_markup": partner_markup,
            "note": "Prepared from unified manager builder.",
        }
        return self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/prepare/",
            payload,
            format="json",
        )

    def test_assigned_manager_can_prepare_quote_with_recommended_shop(self):
        options = self._shop_options(self.manager).json()
        recommended = next(row for row in options["results"] if row["is_recommended"])

        response = self._prepare_assigned_quote(
            shop=Shop.objects.get(pk=recommended["shop_id"]),
            pricing_snapshot=options["pricing_snapshot"],
        )

        self.assertEqual(response.status_code, 201)
        quote = Quote.objects.get(pk=response.json()["quote"]["id"])
        self.quote_request.refresh_from_db()
        self.assertEqual(quote.shop_id, recommended["shop_id"])
        self.assertEqual(self.quote_request.request_snapshot["selected_shop_ids"], [recommended["shop_id"]])
        self.assertTrue(hasattr(quote, "financial_split"))

    def test_assigned_manager_can_prepare_quote_with_overridden_priced_shop(self):
        options = self._shop_options(self.manager).json()
        override = next(row for row in options["results"] if row["shop_id"] == self.expensive_shop.id)

        response = self._prepare_assigned_quote(
            shop=self.expensive_shop,
            pricing_snapshot=options["pricing_snapshot"],
        )

        self.assertEqual(response.status_code, 201)
        quote = Quote.objects.get(pk=response.json()["quote"]["id"])
        self.quote_request.refresh_from_db()
        self.assertEqual(quote.shop_id, override["shop_id"])
        self.assertEqual(self.quote_request.request_snapshot["selected_shop_ids"], [self.expensive_shop.id])

    def test_assigned_prepare_requires_selected_shop(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/prepare/",
            {
                "pricing_snapshot": self._shop_options(self.manager).json()["pricing_snapshot"],
                "partner_markup": "300.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("shop", response.json()["field_errors"])

    def test_assigned_prepare_requires_markup(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/prepare/",
            {
                "shop": self.cheapest_shop.id,
                "pricing_snapshot": self._shop_options(self.manager).json()["pricing_snapshot"],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("partner_markup", response.json()["field_errors"])

    def test_assigned_prepare_rejects_no_eligible_shop_snapshot(self):
        response = self._prepare_assigned_quote(
            pricing_snapshot={"currency": "KES", "selected_shops": [], "pricing_source": "insufficient_data"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("pricing_snapshot", response.json()["field_errors"])
        self.assertEqual(self.quote_request.quotes.count(), 0)

    def test_client_facing_assigned_quote_hides_raw_production_economics(self):
        response = self._prepare_assigned_quote()
        self.assertEqual(response.status_code, 201)
        quote = Quote.objects.get(pk=response.json()["quote"]["id"])

        client_visible_pricing = quote.response_snapshot["customer_pricing"]
        self.assertIn("final_client_price", client_visible_pricing)
        self.assertNotIn("estimated_production_cost", client_visible_pricing)
        self.assertNotIn("estimated_shop_payout", client_visible_pricing)
        self.assertNotIn("production_cost", client_visible_pricing)
        self.assertNotIn("broker_payout", client_visible_pricing)
        self.assertNotIn(self.cheapest_shop.name, str(quote.response_snapshot))
