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
                    "requested_paper_category": "gloss",
                    "requested_gsm": 300,
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
        first_result = payload["results"][0]
        self.assertEqual(payload["product_type"], "business_card")
        self.assertEqual(payload["visibility"]["audience"], "manager")
        self.assertTrue(payload["visibility"]["exposes_shop_identity"])
        self.assertTrue(payload["visibility"]["exposes_internal_economics"])
        self.assertEqual(first_result["shop_name"], "Cheapest Shop")
        self.assertEqual(first_result["price_status"], "priced")
        self.assertEqual(first_result["pricing_source"], "canonical_rate")
        self.assertEqual(first_result["recommendation_label"], "Recommended")
        self.assertIn("production_breakdown", first_result)
        self.assertGreaterEqual(len(first_result["production_breakdown"]["breakdown"]), 3)
        first_line = first_result["production_breakdown"]["breakdown"][0]
        self.assertEqual(
            set(first_line).intersection({"component", "label", "quantity", "unit_price_kes", "total_kes", "source"}),
            {"component", "label", "quantity", "unit_price_kes", "total_kes", "source"},
        )
        self.assertTrue(first_result["production_breakdown"]["breakdown_reconciles"])
        self.assertEqual(payload["results"][-1]["price_status"], "missing_pricing")
        self.assertIn("selected_shops", payload["pricing_snapshot"])

    def test_manager_breakdown_contains_component_lines_for_matched_option(self):
        response = self._shop_options(self.manager)

        self.assertEqual(response.status_code, 200)
        priced = next(row for row in response.json()["results"] if row["price_status"] == "priced")
        breakdown = priced["production_breakdown"]
        components = {line["component"] for line in breakdown["breakdown"]}

        self.assertIn("paper", components)
        self.assertIn("printing", components)
        self.assertIn("cutting", components)
        self.assertEqual(breakdown["production_cost"], priced["production_cost"])
        self.assertEqual(breakdown["breakdown_total_kes"], priced["production_cost"])

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
                "requested_paper_category": "gloss",
                "requested_gsm": 300,
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

    def test_diagnostic_rows_expose_eligibility_reason_and_contact(self):
        """Manager needs exactly what is lacking per shop plus a contact to nudge."""
        self.client.force_authenticate(user=self.manager)

        response = self.client.post(
            "/api/partner/production-matches/",
            {
                "calculator_context": "broker_dashboard",
                "intent": "source_production",
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "85x55mm",
                "requested_paper_category": "gloss",
                "requested_gsm": 300,
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        rows = {row["shop_name"]: row for row in response.json()["results"]}

        priced = rows["Cheapest Shop"]
        self.assertIs(priced["eligible"], True)
        self.assertEqual(priced["missing_requirements"], [])

        diagnostic = rows["Needs Setup Shop"]
        self.assertIs(diagnostic["eligible"], False)
        self.assertTrue(diagnostic["ineligible_reason"])
        self.assertEqual(diagnostic["ineligible_reason"], diagnostic["explanation"] or diagnostic["reason"])
        self.assertIn("pricing", diagnostic["missing_requirements"])
        self.assertTrue(diagnostic["shop_contact"])
        self.assertIn(diagnostic["shop_contact_label"], {"WhatsApp", "Phone"})

    def test_other_manager_cannot_fetch_assigned_request_options(self):
        response = self._shop_options(self.other_manager)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("production_breakdown", str(response.json()))

    def test_client_cannot_fetch_assigned_request_options(self):
        response = self._shop_options(self.end_client)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("production_breakdown", str(response.json()))

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

    def _create_shop_bare(self, name: str, slug: str, *, with_cutting: bool = False, with_matte_lamination: bool = False):
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
            selling_price=Decimal("20.00"),
            width_mm=320,
            height_mm=450,
            is_active=True,
        )
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="COLOR",
            single_price=Decimal("35.00"),
            double_price=Decimal("70.00"),
            is_active=True,
        )
        if with_cutting:
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
        if with_matte_lamination:
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

    def test_found_shops_show_prices_even_without_a_cutting_finishing_rate(self):
        """Regression: a shop that can price a business card must not be shown as
        found-but-unpriced just because it has no catalogued cutting finishing rate.
        Cutting is an imposition concern (pieces are cut out of the imposed parent
        sheet), so it is priced when a rate exists and never excludes a shop."""
        self._create_shop_bare("No Cutting Rate Shop", "phase-d1-no-cutting-rate")

        response = self._shop_options(self.manager)
        self.assertEqual(response.status_code, 200)
        row = next(
            row for row in response.json()["results"]
            if row["shop_name"] == "No Cutting Rate Shop"
        )
        self.assertEqual(row["price_status"], "priced")
        self.assertTrue(row["price_available"])
        self.assertIsNotNone(row["production_cost"])
        self.assertNotIn("cutting", row["missing_requirements"])

    def test_explicitly_requested_lamination_still_blocks_shop_without_finishing_rate(self):
        """Regression guard: structural cutting must be non-blocking, but a finishing
        the request explicitly asks for (lamination) still excludes shops that lack it."""
        self._create_shop_bare("Lam No", "phase-d1-lam-no", with_cutting=True, with_matte_lamination=False)
        self._create_shop_bare("Lam Yes", "phase-d1-lam-yes", with_cutting=True, with_matte_lamination=True)

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
                    "finished_size": "85x55mm",
                    "requested_paper_category": "gloss",
                    "requested_gsm": 300,
                    "print_sides": "SIMPLEX",
                    "color_mode": "COLOR",
                    "lamination": "matt-lamination",
                },
            },
        )

        response = self._shop_options(self.manager, quote_request=quote_request)
        self.assertEqual(response.status_code, 200)
        rows = {row["shop_name"]: row for row in response.json()["results"]}
        self.assertEqual(rows["Lam No"]["price_status"], "insufficient_data")
        self.assertIn("finishing", rows["Lam No"]["missing_requirements"])
        self.assertIn("matt-lamination", rows["Lam No"]["missing_requirements"])
        self.assertIsNone(rows["Lam No"]["production_cost"])
        self.assertEqual(rows["Lam Yes"]["price_status"], "priced")
        self.assertIsNotNone(rows["Lam Yes"]["production_cost"])

    def test_buyer_calculator_prices_shop_without_cutting_finishing_rate(self):
        """Regression: the public calculator path must price a shop that lacks a
        cutting finishing rate too (the deployed symptom: choosing a 300gsm stock
        still shows 'Ready to price — almost')."""
        self._create_shop_bare("Public No Cutting Shop", "phase-d1-public-no-cutting")

        response = self.client.post(
            "/api/calculator/public-preview/",
            {
                "product_type": "business_card",
                "quantity": 100,
                "finished_size": "85x55mm",
                "requested_paper_category": "gloss",
                "requested_gsm": 300,
                "print_sides": "SIMPLEX",
                "color_mode": "COLOR",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["can_calculate"])
        self.assertGreaterEqual(payload["matches_count"], 1)
        self.assertIsNotNone(payload["display_price_text"])
        self.assertIsNotNone(payload.get("market_range"))
        self.assertIsNotNone(payload["market_range"]["min"])
        self.assertIsNotNone(payload["market_range"]["median"])

    def test_missing_pricing_data_returns_diagnostics_without_crashing(self):
        response = self._shop_options(self.manager)
        rows = {row["shop_name"]: row for row in response.json()["results"]}

        self.assertEqual(rows["Needs Setup Shop"]["price_status"], "missing_pricing")
        self.assertIn("pricing", rows["Needs Setup Shop"]["missing_requirements"])
        self.assertFalse(rows["Needs Setup Shop"]["price_available"])
        self.assertIsNone(rows["Needs Setup Shop"]["production_breakdown"])

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
        self.assertNotIn(
            "finished_size",
            payload["missing_fields"],
            "finished size is not a shop-matching requirement; it is used only for "
            "imposition and cutting, which happen on the shop's default sheet.",
        )
        self.assertNotIn(
            "requested_paper_category",
            payload["missing_fields"],
            "paper is assumed from the product default (business_card -> 300gsm artcard); "
            "only truly absent specs block the search.",
        )
        self.assertGreaterEqual(payload["results_count"], 1)

    def _prefill_labelled_body(self):
        """Mirrors ManagerQuotesView.specsBody(): forwards the prefill's labels verbatim."""
        prefill = self.client.get(
            f"/api/dashboard/manager/quote-requests/{self.quote_request.id}/prefill/"
        ).json()
        size = prefill["size"] if isinstance(prefill["size"], dict) else {}
        return {
            "quantity": prefill["quantity"],
            "requested_gsm": prefill["paper"]["gsm"],
            "requested_paper_category": prefill["paper"]["type"] or None,
            "print_sides": prefill["print"]["sides"],
            "color_mode": prefill["print"]["color_mode"],
            "finished_size": size.get("label") or None,
            "width_mm": size.get("width_mm"),
            "height_mm": size.get("height_mm"),
        }

    def test_prefill_to_shop_options_round_trip_fetches_options_despite_shops(self):
        """Reproduces the reported bug: "We couldn't fetch production options." despite shops.

        The prefill endpoint advertises a human-readable label space
        (print.sides 'single'/'double', color_mode 'black_only'/'full_color').
        ManagerQuotesView.specsBody() forwards those labels verbatim to
        shop-options/, whose serializer only accepts SIMPLEX/DUPLEX and BW/COLOR,
        so the POST returns 400 — which the UI hides behind the generic
        "We couldn't fetch production options." error.
        """
        self.client.force_authenticate(user=self.manager)

        prefill = self.client.get(
            f"/api/dashboard/manager/quote-requests/{self.quote_request.id}/prefill/"
        ).json()
        self.assertEqual(prefill["print"]["sides"], "single")
        self.assertEqual(prefill["print"]["color_mode"], "full_color")
        forwarded = self._prefill_labelled_body()
        self.assertEqual(forwarded["print_sides"], "single")
        self.assertEqual(forwarded["color_mode"], "full_color")

        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            forwarded,
            format="json",
        )

        assert response.status_code == 200, (
            f"shop-options returned {response.status_code} {response.json()} — the prefill labels "
            "('single'/'full_color') are rejected by PartnerAssignedRequestShopOptionsSerializer "
            "(expects SIMPLEX/DUPLEX and BW/COLOR), which the manager store surfaces as "
            "'We couldn't fetch production options.' even though priced shops exist."
        )
        self.assertGreaterEqual(response.json()["results_count"], 1)
        self.assertGreaterEqual(response.json()["matched_count"], 1)

    def test_same_request_succeeds_when_labels_are_canonical_codes(self):
        """Isolation proof: with canonical codes the identical request is priced —
        so the only blocker is the prefill label space, not the shops."""
        self.client.force_authenticate(user=self.manager)

        body = self._prefill_labelled_body()
        body["print_sides"] = "SIMPLEX"
        body["color_mode"] = "COLOR"

        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            body,
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["product_type"], "business_card")
        self.assertGreaterEqual(payload["results_count"], 1)
        self.assertGreaterEqual(payload["matched_count"], 1)

    def test_preview_pricing_with_prefill_labels_prices_shops(self):
        """The label space must also price on preview-pricing, which bypasses the
        serializer and feeds specs straight into the matcher."""
        self.client.force_authenticate(user=self.manager)

        specs = self._prefill_labelled_body()
        response = self.client.post(
            f"/api/dashboard/manager/quote-requests/{self.quote_request.id}/preview-pricing/",
            {"specs": specs, "markup_pct": "75"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        priced = [shop for shop in payload["eligible_shops"] if shop["eligible"]]
        self.assertGreaterEqual(len(priced), 1, "label specs must still match a priced shop")
        self.assertIsNotNone(payload["breakdown"])

    def test_shop_options_still_reject_unknown_side_or_colour_codes(self):
        """Garbage codes must keep failing validation instead of silently no-op'ing."""
        self.client.force_authenticate(user=self.manager)

        body = self._prefill_labelled_body()
        body["print_sides"] = "OCTOPUS"
        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            body,
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("print_sides", response.json().get("field_errors", {}))

    def _black_only_body(self) -> dict:
        body = self._prefill_labelled_body()
        body.update(
            {
                "print_sides": "double",
                "color_mode": "black_only",
                "width_mm": 85,
                "height_mm": 55,
                "finished_size": "85x55mm",
            }
        )
        return body

    def test_black_only_is_not_offered_when_shop_has_no_bw_rate(self):
        """B&W is optional: COLOR-only shops must NOT silently price a black_only
        spec at COLOR rates. Every row must be a diagnostic (cannot produce)."""
        self.client.force_authenticate(user=self.manager)

        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            self._black_only_body(),
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["matched_count"], 0, payload)
        for row in payload["results"]:
            self.assertNotEqual(row["price_status"], "priced", row)
            self.assertFalse(row["can_produce"], row)

    def test_resolve_returns_none_for_bw_when_only_color_rates_exist(self):
        machine = self.cheapest_shop.machines.first()
        self.assertTrue(
            machine.printing_rates.filter(color_mode="COLOR", is_active=True).exists()
        )
        resolved_rate, price = PrintingRate.resolve(machine, "SRA3", "BW", "DUPLEX")
        self.assertIsNone(resolved_rate)
        self.assertIsNone(price)

    def test_black_only_prices_once_a_bw_rate_is_added(self):
        """Adding even one optional BW rate row makes the same shop price B&W."""
        machine = self.cheapest_shop.machines.first()
        PrintingRate.objects.create(
            machine=machine,
            sheet_size="SRA3",
            color_mode="BW",
            single_price=Decimal("5.00"),
            double_price=Decimal("8.00"),
            is_active=True,
        )

        self.client.force_authenticate(user=self.manager)
        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            self._black_only_body(),
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertGreaterEqual(payload["matched_count"], 1, payload)
        priced = [row for row in payload["results"] if row["price_status"] == "priced"]
        self.assertTrue(priced, payload)
        color_double_total = None
        color_body = self._prefill_labelled_body()
        color_body["print_sides"] = "double"
        color_response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            color_body,
            format="json",
        )
        for row in color_response.json()["results"]:
            if row["shop_id"] == self.cheapest_shop.id and row["price_status"] == "priced":
                color_double_total = row["production_cost"]
        bw_total = priced[0]["production_cost"] if priced[0]["shop_id"] == self.cheapest_shop.id else None
        if color_double_total and bw_total:
            self.assertLess(
                Decimal(bw_total),
                Decimal(color_double_total),
                "B&W pricing must be below the COLOR price for the same spec",
            )

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
        self.assertNotIn("production_breakdown", payload_text)
        self.assertNotIn("unit_price_kes", payload_text)
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

    def _prepare_assigned_quote(self, *, user=None, shop=None, pricing_snapshot=None, markup_pct="75.00"):
        self.client.force_authenticate(user=user or self.manager)
        payload = {
            "shop": (shop or self.cheapest_shop).id,
            "pricing_snapshot": pricing_snapshot if pricing_snapshot is not None else self._shop_options(self.manager).json()["pricing_snapshot"],
            "markup_pct": markup_pct,
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

    def test_assigned_prepare_accepts_high_percent_markup_instead_of_rejecting(self):
        """The manager UI enters markup as a PERCENT. 99% must not be read as a
        99.00 KES amount (which would sit below the 5% floor relative to a
        ~7750 production cost) and rejected with 'Markup cannot be below 5%'."""
        options = self._shop_options(self.manager).json()
        entry = next(row for row in options["results"] if row["is_recommended"])

        response = self._prepare_assigned_quote(
            shop=Shop.objects.get(pk=entry["shop_id"]),
            pricing_snapshot=options["pricing_snapshot"],
            markup_pct="99.00",
        )

        self.assertEqual(response.status_code, 201, response.content)
        quote = Quote.objects.get(pk=response.json()["quote"]["id"])
        split = quote.financial_split
        production = split.production_cost
        expected_markup = (production * Decimal("99") / Decimal("100")).quantize(Decimal("0.01"))
        self.assertEqual(split.manager_markup, expected_markup, f"{split.production_cost} / {split.manager_markup}")

    def test_assigned_prepare_still_rejects_below_five_percent_markup(self):
        response = self._prepare_assigned_quote(markup_pct="3.00")

        self.assertEqual(response.status_code, 400)
        field_errors = response.json()["field_errors"]
        self.assertIn("markup_pct", field_errors)
        self.assertEqual(field_errors["markup_pct"][0], "Markup cannot be below 5%.")
        self.assertEqual(self.quote_request.quotes.count(), 0)

    def test_assigned_prepare_requires_selected_shop(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/prepare/",
            {
                "pricing_snapshot": self._shop_options(self.manager).json()["pricing_snapshot"],
                "markup_pct": "75.00",
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
        self.assertIn("markup_pct", response.json()["field_errors"])

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
        self.assertNotIn("production_breakdown", str(quote.response_snapshot))
        self.assertNotIn("unit_price_kes", str(quote.response_snapshot))
        self.assertNotIn(self.cheapest_shop.name, str(quote.response_snapshot))

    def _request_without_paper_spec(self):
        """Mirrors live QR-1/QR-2: manager-led intake captured NO paper fields."""
        return QuoteRequest.objects.create(
            shop=None,
            created_by=self.end_client,
            assigned_manager=self.manager,
            customer_name="End Client",
            customer_email=self.end_client.email,
            status=QuoteStatus.SUBMITTED,
            request_snapshot={
                "source": "manager_led_intake",
                "calculator_inputs": {
                    "quantity": 100,
                    "color_mode": "COLOR",
                    "lamination": "none",
                    "print_sides": "DUPLEX",
                    "product_type": "business_card",
                    "finished_size": "90x55mm",
                    "corner_rounding": False,
                },
            },
        )

    def test_shop_options_imply_default_paper_when_request_has_no_paper_spec(self):
        """Live QR-1/QR-2 have no paper fields. Instead of reporting a missing
        paper spec, matching implies the product's recommended paper
        (business_card -> 300gsm artcard) so the manager sees priced
        printer shops — without mutating the stored request."""
        self.client.force_authenticate(user=self.manager)
        request = self._request_without_paper_spec()

        response = self.client.post(
            f"/api/dashboard/partner/quotes/{request.id}/shop-options/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.json())
        payload = response.json()
        self.assertNotIn("paper_stock", payload["missing_fields"], payload)
        self.assertEqual(payload["spec_snapshot"]["requested_gsm"], 300, payload)
        self.assertEqual(payload["spec_snapshot"]["requested_paper_category"], "artcard", payload)
        self.assertGreaterEqual(payload["matched_count"], 1, payload)
        self.assertTrue(any(row["price_status"] == "priced" for row in payload["results"]), payload)

    def test_matcher_never_overrides_an_explicit_paper_request(self):
        from services.production_matching import build_partner_production_matches

        payload = build_partner_production_matches(
            {
                "quantity": 100,
                "color_mode": "COLOR",
                "print_sides": "DUPLEX",
                "product_type": "business_card",
                "finished_size": "90x55mm",
                "requested_paper_category": "matt",
                "requested_gsm": 250,
            }
        )
        snapshot = payload["spec_snapshot"]
        self.assertNotIn("paper_stock", payload["missing_fields"], payload)
        self.assertEqual(snapshot["requested_paper_category"], "matt")
        self.assertEqual(snapshot["requested_gsm"], 250)
        self.assertNotIn("paper_stock", snapshot, "matching never materializes a paper_stock key")
        self.assertGreaterEqual(payload["matched_count"], 1, payload)

    def test_refresh_shop_pricing_ready_is_data_driven_not_flag_driven(self):
        from shops.services import refresh_shop_pricing_ready

        shop = self.cheapest_shop
        Shop.objects.filter(pk=shop.pk).update(pricing_ready=True)

        Machine.objects.filter(shop=shop, is_active=True).update(is_active=False)
        self.assertFalse(refresh_shop_pricing_ready(shop))
        self.assertFalse(Shop.objects.get(pk=shop.pk).pricing_ready)

        Machine.objects.filter(shop=shop, is_active=False).update(is_active=True)
        self.assertTrue(refresh_shop_pricing_ready(shop))
        self.assertTrue(Shop.objects.get(pk=shop.pk).pricing_ready)

        Paper.objects.filter(shop=shop, selling_price__gt=0).update(selling_price=Decimal("0"))
        self.assertFalse(refresh_shop_pricing_ready(shop))
        self.assertFalse(Shop.objects.get(pk=shop.pk).pricing_ready)
