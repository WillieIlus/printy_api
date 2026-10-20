"""Law Part 9 / 11: the rate card is ONE resolved table driven by one
resolver, matching the pricing engine exactly.

Covers prompt items 14-25 (the backend half; item 26 is frontend).
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from inventory.models import Machine, Paper
from pricing.choices import ChargeUnit, FinishingBillingBasis, FinishingSideMode
from pricing.models import FinishingRate, PlatformFeePolicy, PrintingRate, ShopRateCardSetup
from quotes.choices import QuoteStatus
from quotes.models import QuoteRequest
from services.pricing.engine import calculate_sheet_pricing
from services.pricing.mvp_rate_card import (
    _activated_default_finishing_rows,
    _build_default_paper_rows,
    build_shop_rate_card_setup,
    complete_shop_rate_card_setup,
    preview_shop_rate_card_table,
    save_shop_rate_card_setup,
)
from services.pricing.rate_card_resolver import finishing_charge, resolve_paper_row, resolve_master_settings
from services.production_matching import build_partner_production_matches
from shops.models import Shop


def _table(paper_rows=None, finishing_rows=None, master=None, **kwargs):
    return preview_shop_rate_card_table(
        paper_rows=paper_rows if paper_rows is not None else _build_default_paper_rows(),
        finishing_rows=finishing_rows or [],
        master_settings=master,
        **kwargs,
    )


def _row(preview, key):
    return next(row for row in preview["rows"] if row["key"] == key)


class Law9PresetTotalsTests(TestCase):
    """Item 14: preset master 15/10@250 -> the pinned per-sheet totals."""

    def test_preset_totals(self):
        preview = _table()
        self.assertEqual(_row(preview, "art_paper_130gsm")["total_one_side"], "20.00")
        self.assertEqual(_row(preview, "art_paper_130gsm")["total_both_sides"], "35.00")
        self.assertEqual(_row(preview, "250gsm_matte_art_card")["total_one_side"], "45.00")
        self.assertEqual(_row(preview, "250gsm_matte_art_card")["total_both_sides"], "60.00")
        self.assertEqual(_row(preview, "ivory_300gsm")["total_one_side"], "65.00")
        self.assertEqual(_row(preview, "ivory_300gsm")["total_both_sides"], "80.00")
        tic_tac = _row(preview, "tic_tac")
        self.assertEqual(tic_tac["total_one_side"], "40.00")
        self.assertIsNone(tic_tac["total_both_sides"])
        self.assertFalse(tic_tac["double_sided_available"])


class Law9MasterInputTests(TestCase):
    """Items 15, 16, 18, 19: master inputs drive every automatic row."""

    def test_main_price_18(self):
        preview = _table(master={"single_print": "18.00"})
        self.assertEqual(_row(preview, "art_paper_130gsm")["total_one_side"], "23.00")
        self.assertEqual(_row(preview, "art_paper_130gsm")["total_both_sides"], "41.00")
        self.assertEqual(_row(preview, "250gsm_matte_art_card")["total_one_side"], "48.00")
        self.assertEqual(_row(preview, "250gsm_matte_art_card")["total_both_sides"], "66.00")

    def test_both_sides_typed_25_and_blank_returns_to_2x(self):
        preview = _table(master={"double_print": "25.00"})
        self.assertEqual(_row(preview, "art_paper_130gsm")["total_both_sides"], "30.00")
        blank = _table(master={"double_print": None})
        self.assertEqual(_row(blank, "art_paper_130gsm")["total_both_sides"], "35.00")

    def test_threshold_300_moves_surcharge(self):
        preview = _table(master={"surcharge_threshold_gsm": 300})
        gsm250 = _row(preview, "250gsm_matte_art_card")
        gsm300 = _row(preview, "300gsm_matte_art_card")
        self.assertFalse(gsm250["surcharge_applies"])
        self.assertEqual(gsm250["total_one_side"], "35.00")
        self.assertTrue(gsm300["surcharge_applies"])
        self.assertEqual(gsm300["total_one_side"], "50.00")

    def test_lamination_multiplier_3_gloss_6(self):
        master = {"gloss_first_side": "6.00", "lamination_second_multiplier": "3"}
        preview = _table(finishing_rows=_activated_default_finishing_rows(), master=master)
        finishings = {row["key"]: row for row in preview["finishings"]}
        self.assertEqual(finishings["gloss_lamination_double"]["rate"], "24.00")
        self.assertEqual(finishings["matte_lamination_double"]["rate"], "60.00")


class Law9OverrideTests(TestCase):
    """Item 17: a row override changes that row only; Reset restores master."""

    def test_row_override_and_reset(self):
        base = _table()
        self.assertEqual(_row(base, "art_paper_130gsm")["total_one_side"], "20.00")
        rows = [
            dict(row, override_single_print="20.00")
            for row in _build_default_paper_rows()
            if row["key"] == "art_paper_130gsm"
        ]
        rows += [row for row in _build_default_paper_rows() if row["key"] != "art_paper_130gsm"]
        overridden = _table(paper_rows=rows)
        self.assertEqual(_row(overridden, "art_paper_130gsm")["total_one_side"], "25.00")
        # Other rows are untouched.
        self.assertEqual(_row(overridden, "250gsm_matte_art_card")["total_one_side"], "45.00")
        # Reset (null override) restores the master-driven value.
        reset_rows = [dict(row, override_single_print=None) for row in rows]
        reset_rows += [row for row in _build_default_paper_rows() if row["key"] != "art_paper_130gsm"]
        reset = _table(paper_rows=reset_rows)
        self.assertEqual(_row(reset, "art_paper_130gsm")["total_one_side"], "20.00")


class Law9FinishingRulesTests(TestCase):
    """Item 20: per-unit minimum, flat once, and no-minimum rules."""

    ROWS = [
        {"key": "perfect_binding", "price": "50.00", "minimum_charge": "200.00", "pricing_mode": "per_piece", "active": True},
        {"key": "creasing", "price": "300.00", "minimum_charge": "300.00", "pricing_mode": "flat_per_job", "active": True},
        {"key": "standard_cutting", "price": "50.00", "minimum_charge": "50.00", "pricing_mode": "flat_per_job", "active": True},
        {"key": "round_cornering_book", "price": "200.00", "minimum_charge": None, "pricing_mode": "per_book", "active": True},
    ]

    def test_finishing_rules(self):
        binding = next(row for row in self.ROWS if row["key"] == "perfect_binding")
        self.assertEqual(finishing_charge(binding, quantity=10), Decimal("500.00"))
        self.assertEqual(finishing_charge(binding, quantity=2), Decimal("200.00"))
        creasing = next(row for row in self.ROWS if row["key"] == "creasing")
        cutting = next(row for row in self.ROWS if row["key"] == "standard_cutting")
        self.assertEqual(finishing_charge(creasing, quantity=1), Decimal("300.00"))
        self.assertEqual(finishing_charge(creasing, quantity=7), Decimal("300.00"))
        self.assertEqual(finishing_charge(cutting, quantity=1), Decimal("50.00"))
        self.assertEqual(finishing_charge(cutting, quantity=99), Decimal("50.00"))
        round_book = next(row for row in self.ROWS if row["key"] == "round_cornering_book")
        self.assertEqual(finishing_charge(round_book, quantity=1), Decimal("200.00"))
        self.assertEqual(finishing_charge(round_book, quantity=5), Decimal("1000.00"))


class Law9EngineConsistencyTests(TestCase):
    """Item 21: table totals x billable sheets == engine paper-and-print cost,
    and printer_quoted_price (law 7.10a) uses the same resolved rates."""

    def setUp(self):
        PlatformFeePolicy.objects.update(is_active=False)
        self.owner = User.objects.create_user(
            email="law9-owner@test.com", password="pass12345", role=User.Role.PRODUCTION
        )
        self.manager = User.objects.create_user(
            email="law9-manager@test.com", password="pass12345", role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.shop = Shop.objects.create(
            owner=self.owner, name="Law9 Press", slug="law9-press",
            is_active=True, is_public=True, city="Nairobi", service_area="Westlands",
        )
        self.machine = Machine.objects.create(
            shop=self.shop, name="Law9 Machine", max_width_mm=320, max_height_mm=450, is_active=True
        )
        self.paper = Paper.objects.create(
            shop=self.shop, name="SRA3 300gsm", sheet_size="SRA3", gsm=300,
            paper_type="Matte", category="gloss", buying_price=Decimal("1.00"),
            selling_price=Decimal("25.00"), width_mm=320, height_mm=450,
            is_active=True, is_default=True,
        )
        # Same values the rate card persists for the 300 gsm row (paper 25,
        # print 15, surcharge 10 above the 250 gsm threshold).
        PrintingRate.objects.create(
            machine=self.machine, sheet_size="SRA3", color_mode="COLOR",
            single_price=Decimal("15.00"), double_price=None,
            duplex_surcharge=Decimal("10.00"), duplex_surcharge_enabled=True,
            duplex_surcharge_min_gsm=250, is_active=True, is_default=True,
        )
        FinishingRate.objects.create(
            shop=self.shop, name="Cutting", slug="cutting-law9",
            charge_unit=ChargeUnit.FLAT, billing_basis=FinishingBillingBasis.FLAT_PER_JOB,
            side_mode=FinishingSideMode.IGNORE_SIDES, price=Decimal("50.00"), is_active=True,
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=None, created_by=self.manager, assigned_manager=self.manager,
            customer_name="End Client", status=QuoteStatus.SUBMITTED,
            request_snapshot={"calculator_inputs": {
                "product_type": "business_card", "quantity": 100,
                "finished_size": "90x55mm", "requested_paper_category": "gloss",
                "requested_gsm": 300, "print_sides": "SIMPLEX", "color_mode": "COLOR",
            }},
        )

    def test_table_totals_match_engine_paper_and_print(self):
        preview = _table()
        row = _row(preview, "300gsm_matte_art_card")
        one_side = Decimal(row["total_one_side"])
        both_sides = Decimal(row["total_both_sides"])
        self.assertEqual(one_side, Decimal("50.00"))
        self.assertEqual(both_sides, Decimal("65.00"))

        cutting = FinishingRate.objects.get(shop=self.shop, slug="cutting-law9")
        for sides, expected_per_sheet in (("SIMPLEX", one_side), ("DUPLEX", both_sides)):
            result = calculate_sheet_pricing(
                shop=self.shop, quantity=100, paper=self.paper, machine=self.machine,
                color_mode="COLOR", sides=sides,
                finishing_selections=[{"rule": cutting, "selected_side": "both"}],
                width_mm=90, height_mm=55,
            ).to_dict()
            totals = result["totals"]
            sheets = Decimal(str(result["billable_sheets"]))
            paper_cost = Decimal(str(totals["paper_cost"]))
            print_cost = Decimal(str(totals["print_cost"]))
            # Table total x billable sheets == engine paper-and-print cost.
            self.assertEqual(
                (paper_cost + print_cost) / sheets,
                expected_per_sheet,
            )

    def test_printer_quoted_price_uses_same_resolved_rates(self):
        response = build_partner_production_matches({
            "product_type": "business_card", "quantity": 100,
            "finished_size": "90x55mm", "requested_paper_category": "gloss",
            "requested_gsm": 300, "print_sides": "SIMPLEX", "color_mode": "COLOR",
        })
        row = next(r for r in response["results"] if r["shop_id"] == self.shop.id)
        cutting = FinishingRate.objects.get(shop=self.shop, slug="cutting-law9")
        engine = calculate_sheet_pricing(
            shop=self.shop, quantity=100, paper=self.paper, machine=self.machine,
            color_mode="COLOR", sides="SIMPLEX",
            finishing_selections=[{"rule": cutting, "selected_side": "both"}],
            width_mm=90, height_mm=55,
        ).to_dict()
        self.assertEqual(
            Decimal(str(row["printer_quoted_price"])),
            Decimal(str(engine["totals"]["subtotal"])),
        )


class Law9PreviewEqualsSavedTests(TestCase):
    """Item 22: the preview result equals the saved result for the same
    inputs."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="law92-owner@test.com", password="pass12345", role=User.Role.PRODUCTION
        )
        self.shop = Shop.objects.create(
            owner=self.owner, name="Law92 Press", slug="law92-press",
            is_active=True, is_public=True, city="Nairobi", service_area="Westlands",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_preview_equals_saved(self):
        master = {"single_print": "18.00"}
        rows = _build_default_paper_rows()
        finishing_rows = []
        preview = preview_shop_rate_card_table(
            paper_rows=rows, finishing_rows=finishing_rows, master_settings=master
        )
        saved = save_shop_rate_card_setup(
            self.shop, paper_rows=rows, finishing_rows=finishing_rows,
            completed=False, master_settings=master,
        )
        for preview_row, saved_row in zip(preview["rows"], saved["table_preview"]["rows"]):
            self.assertEqual(preview_row["key"], saved_row["key"])
            self.assertEqual(preview_row["total_one_side"], saved_row["total_one_side"])
            self.assertEqual(preview_row["total_both_sides"], saved_row["total_both_sides"])
        setup = ShopRateCardSetup.objects.get(shop=self.shop)
        self.assertEqual(setup.master_settings["single_print"], "18.00")


class Law9VersionImmutabilityTests(TestCase):
    """Item 23: a rate-card change does not move existing quote versions."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="law93-owner@test.com", password="pass12345", role=User.Role.PRODUCTION
        )
        self.manager = User.objects.create_user(
            email="law93-manager@test.com", password="pass12345", role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.shop = Shop.objects.create(
            owner=self.owner, name="Law93 Press", slug="law93-press",
            is_active=True, is_public=True, city="Nairobi", service_area="Westlands",
        )

    def test_version_increments_and_existing_snapshot_is_immutable(self):
        rows = _build_default_paper_rows()
        first = save_shop_rate_card_setup(
            self.shop, paper_rows=rows, finishing_rows=[], completed=False
        )
        self.assertEqual(first["version"], "1")
        setup = ShopRateCardSetup.objects.get(shop=self.shop)
        self.assertEqual(setup.rate_card_version, "1")
        # Phase 3A / D1: a plain save is a draft and does NOT confirm.
        self.assertFalse(setup.is_confirmed)

        second = save_shop_rate_card_setup(
            self.shop, paper_rows=rows, finishing_rows=[], completed=False,
            expected_version="1",
        )
        self.assertEqual(second["version"], "2")
        setup.refresh_from_db()
        self.assertEqual(setup.rate_card_version, "2")
        self.assertNotEqual(first["version"], second["version"])


class Law9PrivacyTests(TestCase):
    """Item 24: other roles cannot read or change a printer's rate card, and
    the manager find-printer response carries no rate-card table."""

    def setUp(self):
        self.owner = User.objects.create_user(
            email="law94-owner@test.com", password="pass12345", role=User.Role.PRODUCTION
        )
        self.other_printer = User.objects.create_user(
            email="law94-other@test.com", password="pass12345", role=User.Role.PRODUCTION
        )
        self.buyer = User.objects.create_user(
            email="law94-buyer@test.com", password="pass12345", role=User.Role.CLIENT
        )
        self.manager = User.objects.create_user(
            email="law94-manager@test.com", password="pass12345", role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.shop = Shop.objects.create(
            owner=self.owner, name="Law94 Press", slug="law94-press",
            is_active=True, is_public=True, city="Nairobi", service_area="Westlands",
        )
        # The other printer owns their own shop, so the bootstrap path is not
        # taken and the ownership gate is what protects this card.
        Shop.objects.create(
            owner=self.other_printer, name="Other Press", slug="law94-other-press",
            is_active=True, is_public=True, city="Nairobi", service_area="Karen",
        )
        save_shop_rate_card_setup(
            self.shop, paper_rows=_build_default_paper_rows(), finishing_rows=[], completed=True
        )
        self.client = APIClient()
        self.quote_request = QuoteRequest.objects.create(
            shop=None, created_by=self.buyer, assigned_manager=self.manager,
            customer_name="End Client", status=QuoteStatus.SUBMITTED,
            request_snapshot={"calculator_inputs": {
                "product_type": "business_card", "quantity": 100,
                "finished_size": "90x55mm", "requested_paper_category": "gloss",
                "requested_gsm": 300, "print_sides": "SIMPLEX", "color_mode": "COLOR",
            }},
        )

    def test_other_printer_cannot_read_or_write(self):
        self.client.force_authenticate(user=self.other_printer)
        read = self.client.get("/api/shops/rate-card/setup/", {"shop_slug": self.shop.slug})
        self.assertEqual(read.status_code, 404)
        write = self.client.patch(
            f"/api/shops/rate-card/setup/?shop_slug={self.shop.slug}",
            {"paper_rows": []}, format="json",
        )
        self.assertEqual(write.status_code, 404)

    def test_buyer_cannot_read_rate_card(self):
        self.client.force_authenticate(user=self.buyer)
        read = self.client.get("/api/shops/rate-card/setup/", {"shop_slug": self.shop.slug})
        self.assertEqual(read.status_code, 404)

    def test_manager_find_printer_response_has_no_rate_card_table(self):
        self.client.force_authenticate(user=self.manager)
        response = self.client.post(
            f"/api/dashboard/partner/quotes/{self.quote_request.id}/shop-options/",
            {}, format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = str(response.json()).lower()
        for forbidden in ("paper_rows", "finishing_rows", "master_settings", "table_preview"):
            self.assertNotIn(forbidden, body, forbidden)


class Law9ConfirmationGateTests(TestCase):
    """Item 25: an unconfirmed printer is excluded from find-printer;
    confirming (first save) includes them."""

    def setUp(self):
        PlatformFeePolicy.objects.update(is_active=False)
        self.owner = User.objects.create_user(
            email="law95-owner@test.com", password="pass12345", role=User.Role.PRODUCTION
        )
        self.manager = User.objects.create_user(
            email="law95-manager@test.com", password="pass12345", role=User.Role.PARTNER,
            partner_profile_enabled=True,
        )
        self.shop = Shop.objects.create(
            owner=self.owner, name="Law95 Press", slug="law95-press",
            is_active=True, is_public=True, city="Nairobi", service_area="Westlands",
        )
        self.machine = Machine.objects.create(
            shop=self.shop, name="Law95 Machine", max_width_mm=320, max_height_mm=450, is_active=True
        )
        Paper.objects.create(
            shop=self.shop, name="SRA3 300gsm", sheet_size="SRA3", gsm=300,
            paper_type="Matte", category="gloss", buying_price=Decimal("1.00"),
            selling_price=Decimal("25.00"), width_mm=320, height_mm=450,
            is_active=True, is_default=True,
        )
        PrintingRate.objects.create(
            machine=self.machine, sheet_size="SRA3", color_mode="COLOR",
            single_price=Decimal("15.00"), double_price=Decimal("30.00"),
            is_active=True, is_default=True,
        )
        self.quote_request = QuoteRequest.objects.create(
            shop=None, created_by=self.manager, assigned_manager=self.manager,
            customer_name="End Client", status=QuoteStatus.SUBMITTED,
            request_snapshot={"calculator_inputs": {
                "product_type": "business_card", "quantity": 100,
                "finished_size": "90x55mm", "requested_paper_category": "gloss",
                "requested_gsm": 300, "print_sides": "SIMPLEX", "color_mode": "COLOR",
            }},
        )

    def _matches(self):
        return build_partner_production_matches({
            "product_type": "business_card", "quantity": 100,
            "finished_size": "90x55mm", "requested_paper_category": "gloss",
            "requested_gsm": 300, "print_sides": "SIMPLEX", "color_mode": "COLOR",
        })

    def test_unconfirmed_excluded_confirmed_included(self):
        # Before any save: no setup record, so the shop is still visible.
        shop_ids = [row["shop_id"] for row in self._matches()["results"]]
        self.assertIn(self.shop.id, shop_ids)

        # An unconfirmed setup record hides the shop from find-printer.
        ShopRateCardSetup.objects.create(shop=self.shop, is_confirmed=False)
        shop_ids = [row["shop_id"] for row in self._matches()["results"]]
        self.assertNotIn(self.shop.id, shop_ids)

        # Phase 3A / D1: a plain save stays a draft and does NOT confirm,
        # so the shop stays hidden from find-printer.
        save_shop_rate_card_setup(
            self.shop, paper_rows=_build_default_paper_rows(), finishing_rows=[], completed=False
        )
        setup = ShopRateCardSetup.objects.get(shop=self.shop)
        self.assertFalse(setup.is_confirmed)
        shop_ids = [row["shop_id"] for row in self._matches()["results"]]
        self.assertNotIn(self.shop.id, shop_ids)

        # Only the owner's explicit confirm action grants confirmation (when
        # the live pricing is usable), so the shop reappears.
        complete_shop_rate_card_setup(self.shop)
        setup.refresh_from_db()
        self.assertTrue(setup.is_confirmed)
        shop_ids = [row["shop_id"] for row in self._matches()["results"]]
        self.assertIn(self.shop.id, shop_ids)
