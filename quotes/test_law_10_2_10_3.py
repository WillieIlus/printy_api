"""Law 10.2 / 10.3 history, ledger, accuracy and pooled-range tests (law 10.8).

Every assertion uses real stored rows and the same services the application
uses; nothing is mocked to fake a price, a confirmation, or a completed job.
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from catalog.models import Product
from inventory.choices import PaperCategory, PaperType, SheetSize
from inventory.models import Paper
from jobs.analytics import estimate_accuracy_by_product, estimate_accuracy_by_printer
from jobs.completion_actuals import (
    COMPLETION_ACTUALS_EVENT,
    record_completion_actuals,
)
from jobs.models import JobEvent, ManagedJob
from payments.models import Payment
from payments.services import mark_payment_paid
from pricing.models import FinishingRate
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import (
    PriceTransaction,
    Quote,
    QuoteItem,
    QuoteItemFinishing,
    QuoteRequest,
    QuoteVersion,
    QuoteVersionOutcome,
)
from quotes.public_ranges import (
    build_public_price_range,
    trim_outliers,
)
from quotes.snapshots import (
    compute_spec_fingerprint,
    mark_quote_outcome,
    record_quote_version,
)


class Law102And103Tests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
        self.client_user = User.objects.create_user(
            email="client@example.com", password="pw12345678"
        )
        self.shop_a = ShopFactory.create(self.owner, "Shop A", "shop-a")
        self.shop_b = ShopFactory.create(self.owner, "Shop B", "shop-b")
        self.shop_c = ShopFactory.create(self.owner, "Shop C", "shop-c")
        self.product = Product.objects.create(
            name="Business Cards",
            slug="business-cards",
            default_finished_width_mm=90,
            default_finished_height_mm=55,
            min_quantity=100,
        )
        self.product_b = Product.objects.create(
            name="Flyers",
            slug="flyers",
            default_finished_width_mm=210,
            default_finished_height_mm=297,
            min_quantity=100,
        )
        self.paper_a = self._paper(self.shop_a, gsm=350)
        self.paper_a_300 = self._paper(self.shop_a, gsm=300)
        self.paper_b = self._paper(self.shop_b, gsm=350)
        self.paper_c = self._paper(self.shop_c, gsm=350)
        self.api = APIClient()

    # ---------------------------------------------------------------- helpers
    def _paper(self, shop, *, gsm=350):
        return Paper.objects.create(
            shop=shop,
            sheet_size=SheetSize.SRA3,
            gsm=gsm,
            paper_type=PaperType.MATTE,
            category=PaperCategory.MATTE,
            buying_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
        )

    def _request_and_item(
        self,
        *,
        shop=None,
        product=None,
        paper=None,
        quantity=100,
        sides="SIMPLEX",
        color_mode="COLOR",
        width=90,
        height=55,
    ):
        shop = shop or self.shop_a
        product = product or self.product
        paper = paper or self.paper_a
        request = QuoteRequest.objects.create(
            shop=shop,
            created_by=self.client_user,
            customer_name="Client",
            status=QuoteStatus.QUOTED,
            request_snapshot={"matched_specs": [{"label": "Business cards"}]},
        )
        item = QuoteItem.objects.create(
            quote_request=request,
            item_type="PRODUCT",
            product=product,
            quantity=quantity,
            pricing_mode="SHEET",
            paper=paper,
            chosen_width_mm=width,
            chosen_height_mm=height,
            sides=sides,
            color_mode=color_mode,
        )
        return request, item

    def _quote(
        self,
        request,
        *,
        shop=None,
        total=Decimal("1000.00"),
        status=QuoteOfferStatus.SENT,
        with_split=False,
        production_cost=Decimal("700.00"),
        manager_markup=Decimal("300.00"),
    ):
        shop = shop or request.shop
        quote = Quote.objects.create(
            quote_request=request,
            shop=shop,
            created_by=self.owner,
            status=status,
            total=total,
            sent_at=timezone.now(),
            estimated_ready_at=timezone.now() + timedelta(days=3),
        )
        if with_split:
            create_quote_financial_split(
                quote=quote,
                production_cost=production_cost,
                manager_markup=manager_markup,
            )
            quote.total = quote.financial_split.client_total
            quote.save(update_fields=["total", "updated_at"])
        return quote

    def _priced_version(self, *, shop, total, product=None, paper=None, quantity=100):
        papers = {
            self.shop_a: self.paper_a,
            self.shop_b: self.paper_b,
            self.shop_c: self.paper_c,
        }
        request, _item = self._request_and_item(
            shop=shop,
            product=product or self.product,
            paper=paper or papers[shop],
            quantity=quantity,
        )
        quote = self._quote(request, shop=shop, total=Decimal(total))
        return record_quote_version(
            quote_request=request, quote=quote, user=self.owner, reason="quote sent"
        )

    # -------------------------------------------------- 1. one fingerprint
    def test_one_spec_fingerprint_covers_all_eight_law_dimensions(self):
        def fingerprint(**kwargs):
            request, _item = self._request_and_item(**kwargs)
            return compute_spec_fingerprint(request)

        base = fingerprint()
        variations = {
            "product": dict(product=self.product_b),
            "size": dict(width=105, height=74),
            "quantity band": dict(quantity=500),
            "paper": dict(paper=self.paper_a_300),
            "gsm": dict(paper=self.paper_a_300),
            "sides": dict(sides="DUPLEX"),
            "colours": dict(color_mode="BW"),
        }
        for label, kwargs in variations.items():
            with self.subTest(dimension=label):
                self.assertNotEqual(base, fingerprint(**kwargs))

        # Finishing is the eighth dimension.
        request, item = self._request_and_item()
        before = compute_spec_fingerprint(request)
        finish = FinishingRate.objects.create(
            shop=self.shop_a, name="Lamination", slug="lamination", price=Decimal("10.00")
        )
        QuoteItemFinishing.objects.create(quote_item=item, finishing_rate=finish)
        self.assertNotEqual(before, compute_spec_fingerprint(request))

    def test_fingerprint_is_stable_and_shared_by_every_dataset(self):
        request, _item = self._request_and_item()
        quote = self._quote(request, with_split=True)
        version = record_quote_version(quote_request=request, quote=quote)
        self.assertEqual(version.spec_fingerprint, compute_spec_fingerprint(request))
        self.assertEqual(
            version.spec_fingerprint,
            compute_spec_fingerprint(QuoteRequest.objects.get(pk=request.pk)),
        )

    # ------------------------------------------- 2. version terms/outcomes
    def test_quote_version_stores_law_10_2a_terms(self):
        request, _item = self._request_and_item()
        quote = self._quote(request, with_split=True)
        request.due_date = timezone.now() + timedelta(days=5)
        request.save(update_fields=["due_date", "updated_at"])

        version = record_quote_version(quote_request=request, quote=quote)
        split = quote.financial_split

        self.assertEqual(version.outcome, QuoteVersionOutcome.OPEN)
        self.assertEqual(version.printer, self.shop_a)
        self.assertEqual(version.printer_quoted_price, split.production_cost)
        self.assertEqual(version.manager_margin, split.manager_markup)
        self.assertEqual(version.fees, split.printy_fee)
        self.assertEqual(version.buyer_price, split.client_total)
        self.assertEqual(version.due_date, request.due_date)
        self.assertIsNotNone(version.created_at)
        self.assertEqual(version.spec_fingerprint, compute_spec_fingerprint(request))
        self.assertEqual(version.snapshot["printer_id"], self.shop_a.id)
        self.assertEqual(version.snapshot["buyer_price"], str(split.client_total))

    def test_price_update_appends_a_version_and_keeps_the_old_one(self):
        request, _item = self._request_and_item()
        quote = self._quote(request, total=Decimal("1000.00"))
        first = record_quote_version(quote_request=request, quote=quote, reason="quote sent")

        quote.total = Decimal("1250.00")
        quote.save(update_fields=["total", "updated_at"])
        second = record_quote_version(quote_request=request, quote=quote, reason="quote revised")

        first.refresh_from_db()
        self.assertEqual(QuoteVersion.objects.filter(quote_request=request).count(), 2)
        self.assertEqual(first.outcome, QuoteVersionOutcome.SUPERSEDED)
        self.assertEqual(first.buyer_price, Decimal("1000.00"))
        self.assertEqual(second.outcome, QuoteVersionOutcome.OPEN)
        self.assertEqual(second.buyer_price, Decimal("1250.00"))

    def test_lost_and_expired_quotes_are_kept(self):
        lost_request, _ = self._request_and_item()
        lost_quote = self._quote(lost_request, total=Decimal("900.00"))
        lost_version = record_quote_version(quote_request=lost_request, quote=lost_quote)
        mark_quote_outcome(
            quote_request=lost_request,
            quote=lost_quote,
            outcome=QuoteVersionOutcome.LOST,
            reason="client chose another shop",
        )

        expired_request, _ = self._request_and_item()
        expired_quote = self._quote(expired_request, total=Decimal("950.00"))
        expired_version = record_quote_version(quote_request=expired_request, quote=expired_quote)

        with override_settings(QUOTE_VALIDITY_DAYS=0):
            call_command("expire_quotes", stdout=StringIO())

        lost_version.refresh_from_db()
        expired_version.refresh_from_db()
        self.assertEqual(lost_version.outcome, QuoteVersionOutcome.LOST)
        self.assertEqual(expired_version.outcome, QuoteVersionOutcome.EXPIRED)
        self.assertEqual(QuoteVersion.objects.filter(pk__in=[lost_version.pk, expired_version.pk]).count(), 2)
        self.assertIsNotNone(lost_version.snapshot)
        self.assertIsNotNone(expired_version.snapshot)

    # -------------------------------------------------- 3. transactions
    def test_transaction_is_created_only_from_a_verified_confirmation(self):
        request, _item = self._request_and_item()
        quote = self._quote(request, status=QuoteOfferStatus.ACCEPTED, with_split=True)
        quote.accepted_at = timezone.now()
        quote.save(update_fields=["accepted_at", "updated_at"])
        version = record_quote_version(quote_request=request, quote=quote)
        mark_quote_outcome(
            quote_request=request,
            quote=quote,
            outcome=QuoteVersionOutcome.ACCEPTED,
            reason="quote accepted",
        )

        payment = Payment.objects.create(
            quote=quote,
            payer=self.client_user,
            amount=quote.total,
            expected_amount=quote.total,
            status=Payment.STATUS_PENDING,
        )
        self.assertEqual(PriceTransaction.objects.filter(payment=payment).count(), 0)

        mark_payment_paid(
            payment,
            receipt_number="MPESA-123",
            received_amount=quote.total,
        )
        transaction = PriceTransaction.objects.get(payment=payment)
        version.refresh_from_db()
        self.assertEqual(version.outcome, QuoteVersionOutcome.ACCEPTED)
        self.assertEqual(transaction.price_paid, quote.total)
        self.assertEqual(transaction.confirmed_payment_at, payment.confirmed_at)
        self.assertEqual(transaction.quote_version_id, version.id)
        self.assertEqual(transaction.printer, self.shop_a)
        self.assertEqual(transaction.spec_fingerprint, version.spec_fingerprint)

    # ---------------------------------------------- 4. completion actuals
    def test_completion_actuals_store_real_figures_and_variance(self):
        request, _item = self._request_and_item()
        quote = self._quote(request, status=QuoteOfferStatus.ACCEPTED, with_split=True)
        version = record_quote_version(quote_request=request, quote=quote)
        mark_quote_outcome(
            quote_request=request,
            quote=quote,
            outcome=QuoteVersionOutcome.ACCEPTED,
            reason="quote accepted",
        )
        job = ManagedJob.objects.create(
            source_quote=quote,
            source_quote_request=request,
            assigned_shop=self.shop_a,
            title="Business cards",
            client_total=quote.total,
        )
        delivered_at = timezone.now()
        event = record_completion_actuals(
            managed_job=job,
            recorded_by=self.owner,
            actual_production_cost=Decimal("820.00"),
            actual_client_total=Decimal("1000.00"),
            billable_sheets=104,
            spoilage_sheets=4,
            stage_durations={"production": 6, "qc": 1},
            delivered_at=delivered_at,
            notes="Paper cost rose",
        )

        metadata = event.metadata
        version.refresh_from_db()
        self.assertEqual(metadata["billable_sheets"], 104)
        self.assertEqual(metadata["spoilage_sheets"], 4)
        self.assertEqual(metadata["stage_durations"], {"production": 6, "qc": 1})
        self.assertEqual(metadata["delivered_at"], delivered_at.isoformat())
        self.assertEqual(metadata["estimate"]["source"], "accepted_version")
        self.assertEqual(metadata["estimate"]["quote_version_id"], version.id)
        self.assertEqual(metadata["estimate"]["production_cost"], "700.00")
        self.assertEqual(metadata["estimate"]["client_total"], "1000.00")
        self.assertEqual(metadata["variance"]["production_cost"], "120.00")
        self.assertEqual(metadata["variance"]["client_total"], "0.00")
        self.assertEqual(metadata["spec_fingerprint"], version.spec_fingerprint)
        self.assertEqual(metadata["product"], "Business Cards")

    # -------------------------------------------------- 5. append-only
    def test_stored_history_has_no_update_or_delete_path(self):
        request, _item = self._request_and_item()
        quote = self._quote(request, with_split=True)
        version = record_quote_version(quote_request=request, quote=quote)

        with self.assertRaises(ValidationError):
            version.delete()
        with self.assertRaises(ValidationError):
            QuoteVersion.objects.filter(pk=version.pk).update(outcome=QuoteVersionOutcome.LOST)
        with self.assertRaises(ValidationError):
            QuoteVersion.objects.filter(pk=version.pk).delete()

        # Outcome is the only allowed transition, and it is still an update of
        # the same kept row.
        version.outcome = QuoteVersionOutcome.EXPIRED
        version.save(update_fields=["outcome", "updated_at"])
        version.refresh_from_db()
        self.assertEqual(version.outcome, QuoteVersionOutcome.EXPIRED)

        payment = Payment.objects.create(
            quote=quote,
            payer=self.client_user,
            amount=quote.total,
            expected_amount=quote.total,
            status=Payment.STATUS_PENDING,
        )
        version.refresh_from_db()
        version.outcome = QuoteVersionOutcome.OPEN
        version.save(update_fields=["outcome", "updated_at"])
        mark_payment_paid(payment, receipt_number="R-1", received_amount=quote.total)
        transaction = PriceTransaction.objects.get(payment=payment)
        with self.assertRaises(ValidationError):
            transaction.delete()
        with self.assertRaises(ValidationError):
            PriceTransaction.objects.filter(pk=transaction.pk).update(price_paid=Decimal("1.00"))
        with self.assertRaises(ValidationError):
            PriceTransaction.objects.filter(pk=transaction.pk).delete()

        job = ManagedJob.objects.get(source_quote=quote)
        event = record_completion_actuals(
            managed_job=job, recorded_by=self.owner, billable_sheets=100
        )
        with self.assertRaises(ValidationError):
            JobEvent.objects.filter(pk=event.pk).update(event="tampered")
        with self.assertRaises(ValidationError):
            JobEvent.objects.filter(pk=event.pk).delete()

        version_admin = admin.site._registry[QuoteVersion]
        transaction_admin = admin.site._registry[PriceTransaction]
        for model_admin in (version_admin, transaction_admin):
            self.assertFalse(model_admin.has_delete_permission(None))
            self.assertFalse(model_admin.has_add_permission(None))
            self.assertFalse(model_admin.has_change_permission(None))
            self.assertIsNone(model_admin.actions)

    # ------------------------------------- 6. accuracy per printer/product
    @override_settings(PRODUCTION_ANALYTICS_MIN_SAMPLE=1)
    def test_estimate_accuracy_is_computed_per_printer_and_per_product(self):
        def job_with_actuals(shop, product, paper, actual):
            request, _item = self._request_and_item(shop=shop, product=product, paper=paper)
            quote = self._quote(request, shop=shop, status=QuoteOfferStatus.ACCEPTED, with_split=True)
            version = record_quote_version(quote_request=request, quote=quote)
            mark_quote_outcome(
                quote_request=request,
                quote=quote,
                outcome=QuoteVersionOutcome.ACCEPTED,
                reason="quote accepted",
            )
            job = ManagedJob.objects.create(
                source_quote=quote,
                source_quote_request=request,
                assigned_shop=shop,
                title=str(product),
                client_total=quote.total,
            )
            record_completion_actuals(
                managed_job=job,
                recorded_by=self.owner,
                actual_production_cost=Decimal(actual),
            )
            return version

        job_with_actuals(self.shop_a, self.product, self.paper_a, "800.00")
        job_with_actuals(self.shop_a, self.product, self.paper_a, "780.00")
        job_with_actuals(self.shop_b, self.product, self.paper_b, "900.00")
        job_with_actuals(self.shop_b, self.product_b, self.paper_b, "950.00")

        printers = estimate_accuracy_by_printer()
        groups = {row["printer_id"]: row for row in printers["groups"]}
        self.assertEqual(groups[self.shop_a.id]["sample_size"], 2)
        self.assertTrue(groups[self.shop_a.id]["sufficient"])
        self.assertEqual(groups[self.shop_a.id]["value"]["total_estimate"], "1400.00")
        self.assertEqual(groups[self.shop_a.id]["value"]["total_actual"], "1580.00")
        self.assertEqual(groups[self.shop_a.id]["value"]["variance"], "180.00")
        self.assertEqual(groups[self.shop_b.id]["sample_size"], 2)

        products = estimate_accuracy_by_product()
        product_groups = {row["product"]: row for row in products["groups"]}
        self.assertEqual(product_groups["Business Cards"]["sample_size"], 3)
        self.assertEqual(product_groups["Flyers"]["sample_size"], 1)

    # ------------------------------------------- 7. public pooled ranges
    def test_public_range_returns_nothing_below_record_or_shop_thresholds(self):
        with override_settings(MIN_SAMPLE_FOR_PUBLIC_RANGE=3, MIN_SHOPS_FOR_PUBLIC_RANGE=2):
            first = self._priced_version(shop=self.shop_a, total="1000.00")
            fingerprint = first.spec_fingerprint

            self.assertIsNone(build_public_price_range(fingerprint))
            self._priced_version(shop=self.shop_a, total="1100.00")
            self.assertIsNone(build_public_price_range(fingerprint))
            self._priced_version(shop=self.shop_a, total="1200.00")
            # Three records but still one distinct shop: still nothing.
            self.assertIsNone(build_public_price_range(fingerprint))

            self._priced_version(shop=self.shop_b, total="1150.00")
            published = build_public_price_range(fingerprint)
            self.assertIsNotNone(published)
            self.assertEqual(published["sample_count"], 4)
            self.assertEqual(published["shop_count"], 2)

    def test_public_range_trims_outliers_by_one_rule_and_hides_identity(self):
        with override_settings(MIN_SAMPLE_FOR_PUBLIC_RANGE=5, MIN_SHOPS_FOR_PUBLIC_RANGE=3):
            first = self._priced_version(shop=self.shop_a, total="100.00")
            fingerprint = first.spec_fingerprint
            self._priced_version(shop=self.shop_b, total="110.00")
            self._priced_version(shop=self.shop_c, total="120.00")
            self._priced_version(shop=self.shop_a, total="130.00")
            self._priced_version(shop=self.shop_c, total="10000.00")

            published = build_public_price_range(fingerprint)
            self.assertIsNotNone(published)
            self.assertEqual(published["low"], "100.00")
            self.assertEqual(published["high"], "130.00")
            self.assertEqual(published["trimmed_count"], 4)
            self.assertEqual(published["shop_count"], 3)
            self.assertEqual(published["trimmed_shop_count"], 3)
            for forbidden in ("shop_id", "shop_name", "printer", "printer_id", "identity"):
                self.assertNotIn(forbidden, published)
            self.assertNotIn("Shop A", json.dumps(published))
            self.assertNotIn("Shop B", json.dumps(published))
            self.assertNotIn("Shop C", json.dumps(published))
            self.assertEqual(trim_outliers([Decimal("1"), Decimal("2")]), [Decimal("1"), Decimal("2")])

    def test_public_range_endpoint_returns_only_pooled_aggregate(self):
        url = reverse("public-price-range")
        missing = self.api.get(url)
        self.assertEqual(missing.status_code, 200)
        self.assertIsNone(missing.data["range"])
        self.assertNotIn("low", missing.data)
        self.assertNotIn("high", missing.data)

        with override_settings(MIN_SAMPLE_FOR_PUBLIC_RANGE=2, MIN_SHOPS_FOR_PUBLIC_RANGE=2):
            first = self._priced_version(shop=self.shop_a, total="1000.00")
            self._priced_version(shop=self.shop_b, total="1200.00")
            response = self.api.get(url, {"spec_fingerprint": first.spec_fingerprint})
            self.assertEqual(response.status_code, 200)
            payload = response.data["range"]
            self.assertIsNotNone(payload)
            self.assertEqual(payload["low"], "1000.00")
            self.assertEqual(payload["high"], "1200.00")
            self.assertEqual(payload["shop_count"], 2)
            for forbidden in ("shop_id", "shop_name", "printer", "printer_id", "identity"):
                self.assertNotIn(forbidden, payload)
            body = json.dumps(response.data)
            for shop in (self.shop_a, self.shop_b, self.shop_c):
                self.assertNotIn(shop.name, body)


class ShopFactory:
    """Tiny factory so the test module does not depend on factory_boy."""

    @staticmethod
    def create(owner, name, slug):
        from shops.models import Shop

        return Shop.objects.create(name=name, slug=slug, owner=owner)
