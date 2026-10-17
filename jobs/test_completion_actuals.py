"""Tests for recorded job completion actuals (law 3.4 / 6.3).

Actuals must be recorded separately from the accepted estimate and never
fabricated; the estimate the buyer accepted is preserved untouched.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import User
from jobs.completion_actuals import (
    COMPLETION_ACTUALS_EVENT,
    completion_actuals_for,
    estimate_for,
    record_completion_actuals,
)
from jobs.models import JobEvent, ManagedJob
from pricing.services.platform_fee_policy import create_quote_financial_split
from quotes.choices import QuoteStatus
from quotes.models import Quote, QuoteRequest
from shops.models import Shop


class CompletionActualsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
        self.client = User.objects.create_user(email="client@example.com", password="pw12345678")
        self.shop = Shop.objects.create(name="Actuals Shop", slug="actuals-shop", owner=self.owner)
        self.request = QuoteRequest.objects.create(
            shop=self.shop, created_by=self.client, customer_name="Client", status=QuoteStatus.CLOSED
        )
        self.quote = Quote.objects.create(
            quote_request=self.request,
            shop=self.shop,
            created_by=self.owner,
            status="accepted",
            total=Decimal("1000.00"),
        )
        create_quote_financial_split(
            quote=self.quote, production_cost=Decimal("700.00"), manager_markup=Decimal("300.00")
        )
        self.job = ManagedJob.objects.create(
            source_quote=self.quote,
            source_quote_request=self.request,
            title="Business cards",
            client_total=Decimal("1000.00"),
        )

    def test_estimate_read_from_accepted_split(self):
        estimate = estimate_for(self.job)
        self.assertEqual(estimate["production_cost"], "700.00")
        self.assertEqual(estimate["client_total"], "1000.00")

    def test_records_actuals_with_variance(self):
        event = record_completion_actuals(
            managed_job=self.job,
            recorded_by=self.owner,
            actual_production_cost=Decimal("820.00"),
            actual_client_total=Decimal("1000.00"),
            billable_sheets=104,
            spoilage_sheets=4,
            notes="Paper cost rose",
        )
        self.assertEqual(event.event, COMPLETION_ACTUALS_EVENT)
        metadata = event.metadata
        self.assertEqual(metadata["actual_production_cost"], "820.00")
        self.assertEqual(metadata["estimate"]["production_cost"], "700.00")
        self.assertEqual(metadata["variance"]["production_cost"], "120.00")
        self.assertEqual(metadata["variance"]["client_total"], "0.00")
        self.assertEqual(metadata["billable_sheets"], 104)

    def test_never_fabricates_without_real_figures(self):
        with self.assertRaises(ValidationError):
            record_completion_actuals(managed_job=self.job, recorded_by=self.owner)

    def test_actuals_are_append_only_and_readable(self):
        record_completion_actuals(
            managed_job=self.job, recorded_by=self.owner, actual_production_cost="710.00"
        )
        record_completion_actuals(
            managed_job=self.job, recorded_by=self.owner, actual_production_cost="700.00"
        )
        self.assertEqual(len(completion_actuals_for(self.job)), 2)

        event = JobEvent.objects.filter(managed_job=self.job, event=COMPLETION_ACTUALS_EVENT).first()
        with self.assertRaises(ValidationError):
            JobEvent.objects.filter(pk=event.pk).update(event="tampered")
        with self.assertRaises(ValidationError):
            JobEvent.objects.filter(pk=event.pk).delete()

    def test_accepted_estimate_snapshot_is_untouched_by_actuals(self):
        self.quote.refresh_from_db()
        split = self.quote.financial_split
        self.assertEqual(split.production_cost, Decimal("700.00"))

        record_completion_actuals(
            managed_job=self.job, recorded_by=self.owner, actual_production_cost=Decimal("900.00")
        )
        split.refresh_from_db()
        self.assertEqual(split.production_cost, Decimal("700.00"))
