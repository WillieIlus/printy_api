"""Tests for law Part 2 (section 2) + 3.4: stored overrides, role recompute,
price-update acknowledgement, paid/accepted lock, and quote versions."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from payments.models import Payment
from quotes.acceptance import accept_quote_for_payment
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import (
    ProductionOption,
    Quote,
    QuoteOverride,
    QuoteRequest,
    QuoteVersion,
)
from quotes.overrides import (
    QuoteLockedError,
    acknowledge_price_update,
    active_overrides,
    apply_overrides_to_canonical,
    create_quote_version,
    is_quote_locked,
    record_override,
)
from services.pricing.role_projection import build_canonical_result, project_canonical
from shops.models import Shop


class Law2OverrideTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@test.com", password="pass12345")
        self.client_user = User.objects.create_user(email="buyer@test.com", password="pass12345")
        self.shop = Shop.objects.create(
            owner=self.owner,
            name="Override Shop",
            slug="override-shop",
            is_active=True,
            currency="KES",
        )
        self.request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client_user,
            customer_name="Client",
            status=QuoteStatus.QUOTED,
        )
        self.quote = Quote.objects.create(
            quote_request=self.request,
            shop=self.shop,
            created_by=self.owner,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1000.00"),
            sent_at=timezone.now(),
        )

    # --- 2.1 stored override record ------------------------------------
    def test_override_records_who_when_field_old_new(self):
        override = record_override(
            quote_request=self.request,
            field="spoilage",
            old_value={"variable_waste_sheets": 5},
            new_value={"variable_waste_sheets": 8},
            user=self.owner,
            reason="Waste higher than estimated",
        )
        override.refresh_from_db()
        self.assertEqual(override.quote_request_id, self.request.id)
        self.assertEqual(override.field, "spoilage")
        self.assertEqual(override.old_value, {"variable_waste_sheets": 5})
        self.assertEqual(override.new_value, {"variable_waste_sheets": 8})
        self.assertEqual(override.created_by_id, self.owner.id)
        self.assertIsNotNone(override.effective_at)
        self.assertEqual(active_overrides(self.request), [override])

    def test_unknown_override_field_is_rejected(self):
        with self.assertRaises(ValidationError):
            record_override(
                quote_request=self.request,
                field="not_a_field",
                new_value=1,
                user=self.owner,
            )

    # --- 2.2 recompute from canonical result + overrides ---------------
    def test_price_and_sheets_recomputed_from_canonical_plus_overrides(self):
        canonical = build_canonical_result(
            {
                "totals": {"grand_total": "1000.00", "unit_price": "10.00"},
                "quantity": 100,
                "copies_per_sheet": 10,
                "good_sheets": 10,
                "billable_sheets": 12,
                "currency": "KES",
            }
        )
        overrides = [
            QuoteOverride(field="markup", new_value={"amount": "200.00"}),
            QuoteOverride(field="sheet_count", new_value={"sheets_billed": 15}),
        ]
        adjusted = apply_overrides_to_canonical(canonical, overrides)

        buyer = project_canonical(adjusted, "buyer")
        manager = project_canonical(adjusted, "manager")
        printer = project_canonical(adjusted, "printer")

        self.assertEqual(buyer["price"], Decimal("1200.00"))
        self.assertEqual(manager["price"], Decimal("1200.00"))
        self.assertEqual(manager["sheets_billed"], 15)
        self.assertEqual(printer["sheets_billed"], 15)
        self.assertNotIn("sheets_billed", buyer)
        self.assertNotIn("manager_margin", buyer)

    def test_paper_substitution_label_and_price_delta(self):
        canonical = build_canonical_result(
            {
                "totals": {"grand_total": "500.00", "unit_price": "5.00"},
                "quantity": 100,
                "currency": "KES",
            }
        )
        overrides = [
            QuoteOverride(
                field="paper_substitution",
                new_value={"label": "Gloss 150gsm", "price_delta": "50.00"},
            )
        ]
        adjusted = apply_overrides_to_canonical(canonical, overrides)
        self.assertEqual(adjusted["price"], Decimal("550.00"))
        self.assertEqual(adjusted["spec_summary"]["paper"], "Gloss 150gsm")

    # --- 2.3 price-update flag + acknowledgement -----------------------
    def test_markup_override_flags_buyer_price_updated(self):
        record_override(
            quote_request=self.request,
            field="markup",
            old_value={"total": "1000.00"},
            new_value={"total": "1200.00"},
            user=self.owner,
            old_total="1000.00",
            new_total="1200.00",
        )
        self.request.refresh_from_db()
        self.assertTrue(self.request.price_updated)
        self.assertEqual(self.request.price_updated_old_total, Decimal("1000.00"))
        self.assertEqual(self.request.price_updated_new_total, Decimal("1200.00"))

        acknowledge_price_update(quote_request=self.request, user=self.client_user)
        self.request.refresh_from_db()
        self.assertFalse(self.request.price_updated)
        self.assertEqual(self.request.price_acknowledged_by_id, self.client_user.id)

    def test_payment_gate_blocks_until_price_acknowledged(self):
        self.request.price_updated = True
        self.request.price_updated_old_total = Decimal("1000.00")
        self.request.price_updated_new_total = Decimal("1200.00")
        self.request.save(update_fields=["price_updated", "price_updated_old_total", "price_updated_new_total"])

        with self.assertRaises(ValidationError):
            accept_quote_for_payment(quote=self.quote, accepted_by=self.client_user)

        acknowledge_price_update(quote_request=self.request, user=self.client_user)
        option = ProductionOption.objects.create(
            quote_request=self.request,
            shop=self.shop,
            production_cost=Decimal("700.00"),
            status=ProductionOption.SELECTED,
            created_by=self.owner,
        )
        self.quote.production_option = option
        self.quote.save(update_fields=["production_option"])
        quote, payment = accept_quote_for_payment(
            quote=self.quote, accepted_by=self.client_user
        )
        self.assertEqual(quote.status, QuoteOfferStatus.ACCEPTED)
        self.assertIsNotNone(payment)

    # --- 2.4 accepted/paid quotes are locked ---------------------------
    def test_accepted_quote_is_locked(self):
        self.quote.status = QuoteOfferStatus.ACCEPTED
        self.quote.save(update_fields=["status"])
        self.assertTrue(is_quote_locked(self.request))
        with self.assertRaises(QuoteLockedError):
            record_override(
                quote_request=self.request,
                field="markup",
                new_value={"amount": "50.00"},
                user=self.owner,
            )

    def test_paid_quote_is_locked(self):
        Payment.objects.create(
            quote=self.quote,
            payer=self.client_user,
            amount=Decimal("1000.00"),
            status=Payment.STATUS_PAID,
        )
        self.assertTrue(is_quote_locked(self.request))
        with self.assertRaises(QuoteLockedError):
            record_override(
                quote_request=self.request,
                field="sheet_count",
                new_value=20,
                user=self.owner,
            )

    # --- 3.4 quote versions -------------------------------------------
    def test_quote_versions_increment_and_are_unique(self):
        v1 = create_quote_version(
            quote_request=self.request, quote=self.quote, user=self.owner
        )
        v2 = create_quote_version(
            quote_request=self.request, quote=self.quote, user=self.owner
        )
        self.assertEqual(v1.version_number, 1)
        self.assertEqual(v2.version_number, 2)
        self.assertEqual(self.request.versions.count(), 2)
        self.assertEqual(v1.total, Decimal("1000.00"))
        self.assertEqual(QuoteVersion.objects.filter(quote_request=self.request).count(), 2)
