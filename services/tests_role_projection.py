"""Tests for the single per-role pricing projection (law 1.1-1.3)."""

from __future__ import annotations

from django.test import SimpleTestCase

from services.pricing.role_projection import (
    ANONYMOUS_FORBIDDEN_FIELDS,
    BUYER_FORBIDDEN_FIELDS,
    CANONICAL_FIELDS,
    ROLE_ALLOWLISTS,
    ROLE_ANONYMOUS,
    ROLE_BUYER,
    ROLE_MANAGER,
    ROLE_PRINTER,
    ROLES,
    allowlist_for,
    assert_projection_is_clean,
    build_canonical_result,
    project_canonical,
)


ENGINE_PAYLOAD = {
    "pricing_mode": "SHEET",
    "quantity": 100,
    "currency": "KES",
    "totals": {
        "subtotal": "1000.00",
        "paper_cost": "400.00",
        "print_cost": "600.00",
        "grand_total": "1150.00",
        "unit_price": "11.50",
    },
    "breakdown": {
        "imposition": {"layout": {"cols": 2, "rows": 5}, "good_sheets": 10},
        "paper": {"sheet_size": "SRA3", "cost_per_sheet": "40.00"},
        "printing": {"machine_name": "Digital Press", "color_mode": "COLOR", "sides": "SIMPLEX"},
        "finishings": [{"name": "Lamination", "total": "0.00"}],
    },
    "copies_per_sheet": 10,
    "good_sheets": 10,
    "billable_sheets": 12,
    "fixed_waste_sheets": 1,
    "variable_waste_sheets": 1,
    "waste_sheets_added": 2,
    "maximum_spoilage_rate": "50",
    "maximum_spoilage_sheets": 5,
    "spoilage_capped": False,
    "parent_sheet_name": "SRA3",
    "can_calculate": True,
    "reason": "",
}


class CanonicalResultTests(SimpleTestCase):
    def test_canonical_has_every_law_field(self):
        canonical = build_canonical_result(
            ENGINE_PAYLOAD,
            spec_summary="500 business cards",
            ready_date="2026-11-01",
            delivery="Nairobi, KES 250",
            job_status="quoted",
            current_holder="Print Manager",
            financials={
                "manager_margin": "100.00",
                "platform_service_percent": "10",
                "shop_payout": "600.00",
                "printer_side_fee": "60.00",
            },
        )
        for field in CANONICAL_FIELDS:
            self.assertIn(field, canonical, field)
        self.assertEqual(canonical["price"], "1150.00")
        self.assertEqual(canonical["per_piece"], "11.50")
        self.assertEqual(canonical["ups"], 10)
        self.assertEqual(canonical["good_sheets"], 10)
        self.assertEqual(canonical["sheets_billed"], 12)
        self.assertEqual(canonical["press_sheet"], "SRA3")
        self.assertEqual(canonical["layout"], {"cols": 2, "rows": 5})
        self.assertEqual(canonical["manager_margin"], "100.00")
        self.assertEqual(canonical["printer_payout"], "600.00")
        self.assertEqual(canonical["printer_side_fee"], "60.00")
        self.assertEqual(canonical["spec_summary"], "500 business cards")

    def test_spoilage_and_method_present(self):
        canonical = build_canonical_result(ENGINE_PAYLOAD)
        self.assertEqual(canonical["spoilage"]["fixed_waste_sheets"], 1)
        self.assertEqual(canonical["method"]["machine_name"], "Digital Press")


class AllowlistTests(SimpleTestCase):
    def test_every_role_has_an_allowlist(self):
        for role in ROLES:
            self.assertIn(role, ROLE_ALLOWLISTS)

    def test_unknown_role_falls_back_to_anonymous(self):
        self.assertEqual(allowlist_for("nope"), allowlist_for(ROLE_ANONYMOUS))

    def test_buyer_never_sees_imposition_or_financials(self):
        self.assertTrue(BUYER_FORBIDDEN_FIELDS.isdisjoint(allowlist_for(ROLE_BUYER)))
        self.assertTrue(ANONYMOUS_FORBIDDEN_FIELDS.isdisjoint(allowlist_for(ROLE_ANONYMOUS)))

    def test_manager_sees_everything(self):
        for field in (
            "press_sheet",
            "layout",
            "ups",
            "good_sheets",
            "spoilage",
            "sheets_billed",
            "method",
            "cost_breakdown",
            "manager_margin",
            "fee_tier",
            "printer_payout",
            "printer_side_fee",
        ):
            self.assertIn(field, allowlist_for(ROLE_MANAGER), field)

    def test_printer_sees_production_and_own_payout_only(self):
        printer = allowlist_for(ROLE_PRINTER)
        self.assertIn("good_sheets", printer)
        self.assertIn("printer_payout", printer)
        for forbidden in ("price", "per_piece", "manager_margin", "fee_tier", "cost_breakdown", "printer_side_fee"):
            self.assertNotIn(forbidden, printer, forbidden)

    def test_projection_strips_forbidden_fields(self):
        canonical = build_canonical_result(ENGINE_PAYLOAD)
        buyer = project_canonical(canonical, ROLE_BUYER)
        self.assertNotIn("good_sheets", buyer)
        self.assertNotIn("layout", buyer)
        self.assertNotIn("cost_breakdown", buyer)
        self.assertIn("price", buyer)
        self.assertIn("spec_summary", buyer)

    def test_assert_projection_is_clean_detects_leak(self):
        with self.assertRaises(ValueError):
            assert_projection_is_clean({"price": 1, "good_sheets": 10}, ROLE_ANONYMOUS)
        assert_projection_is_clean({"price": 1}, ROLE_ANONYMOUS)
