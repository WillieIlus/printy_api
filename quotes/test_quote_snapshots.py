"""Regression tests for stored quote snapshots / history (law 3.4 / 4 / 10.2).

Covers: immutable version snapshots incl. pricing-rule version + spec
fingerprint, revision preservation, calculator estimate vs formal quote,
accepted/rejected/expired outcomes, and history stability when pricing rules
change afterwards.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from pricing.models import PlatformFeePolicy
from quotes.acceptance import accept_quote_for_payment
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import (
    CalculatorDraft,
    ProductionOption,
    Quote,
    QuoteRequest,
    QuoteVersion,
    QuoteVersionOutcome,
)
from quotes.snapshots import (
    compute_spec_fingerprint,
    record_quote_version,
)
from shops.models import Shop


class QuoteSnapshotTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", password="pw12345678")
        self.client = User.objects.create_user(email="client@example.com", password="pw12345678")
        self.shop = Shop.objects.create(name="Snapshot Shop", slug="snapshot-shop", owner=self.owner)
        self.request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.client,
            customer_name="Client",
            status=QuoteStatus.QUOTED,
            request_snapshot={"matched_specs": [{"label": "A5 flyer"}]},
        )
        self.quote = Quote.objects.create(
            quote_request=self.request,
            shop=self.shop,
            created_by=self.owner,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1000.00"),
            quote_reference="Q-1",
            sent_at=timezone.now(),
        )

    # --- immutability + captured version metadata ----------------------
    def test_version_captures_rule_version_and_fingerprint(self):
        version = record_quote_version(
            quote_request=self.request, quote=self.quote, user=self.owner, reason="quote sent"
        )
        self.assertEqual(version.version_number, 1)
        self.assertEqual(version.outcome, QuoteVersionOutcome.OPEN)
        self.assertTrue(version.pricing_rule_version)
        self.assertEqual(version.spec_fingerprint, compute_spec_fingerprint(self.request))
        self.assertIn("pricing_rule_version", version.snapshot)
        self.assertEqual(version.total, Decimal("1000.00"))

    def test_version_snapshot_is_immutable_but_outcome_can_close(self):
        version = record_quote_version(quote_request=self.request, quote=self.quote)
        version.total = Decimal("9999.00")
        with self.assertRaises(ValidationError):
            version.save(update_fields=["total", "updated_at"])

        version.refresh_from_db()
        version.outcome = QuoteVersionOutcome.LOST
        version.save(update_fields=["outcome", "updated_at"])
        version.refresh_from_db()
        self.assertEqual(version.outcome, QuoteVersionOutcome.LOST)
        self.assertEqual(version.total, Decimal("1000.00"))

    # --- revision preserves the previous version -----------------------
    def test_revision_supersedes_but_keeps_previous_version(self):
        first = record_quote_version(
            quote_request=self.request, quote=self.quote, reason="quote sent"
        )
        self.quote.total = Decimal("1200.00")
        self.quote.save(update_fields=["total", "updated_at"])
        second = record_quote_version(
            quote_request=self.request, quote=self.quote, reason="quote revised"
        )

        first.refresh_from_db()
        self.assertEqual(second.version_number, 2)
        self.assertEqual(first.outcome, QuoteVersionOutcome.SUPERSEDED)
        self.assertEqual(first.total, Decimal("1000.00"))
        self.assertEqual(second.total, Decimal("1200.00"))
        self.assertEqual(QuoteVersion.objects.filter(quote_request=self.request).count(), 2)

    def test_specification_change_changes_fingerprint(self):
        before = compute_spec_fingerprint(self.request)
        self.request.request_snapshot = {"matched_specs": [{"label": "A4 flyer"}]}
        self.request.save(update_fields=["request_snapshot", "updated_at"])
        self.assertNotEqual(before, compute_spec_fingerprint(self.request))

    # --- history stable across pricing-rule changes --------------------
    def test_rule_version_is_pinned_even_after_policy_changes(self):
        version = record_quote_version(quote_request=self.request, quote=self.quote)
        pinned = version.pricing_rule_version

        policy = PlatformFeePolicy.objects.filter(is_active=True).first()
        if policy is None:
            policy = PlatformFeePolicy.objects.create(effective_from=timezone.now())
        policy.standard_production_fee_rate = Decimal("0.55")
        policy.save()

        version.refresh_from_db()
        self.assertEqual(version.pricing_rule_version, pinned)
        self.assertEqual(version.total, Decimal("1000.00"))

    # --- calculator estimate is separate from a formal quote -----------
    def test_calculator_estimate_does_not_create_quote_version(self):
        CalculatorDraft.objects.create(
            user=self.client,
            calculator_inputs_snapshot={"quantity": 100},
            pricing_snapshot={"grand_total": "1000.00"},
            draft_reference="D-1",
        )
        self.assertEqual(QuoteVersion.objects.filter(quote_request=self.request).count(), 0)
        record_quote_version(quote_request=self.request, quote=self.quote)
        self.assertEqual(QuoteVersion.objects.filter(quote_request=self.request).count(), 1)

    # --- terminal outcomes ---------------------------------------------
    def test_accept_marks_version_accepted_and_siblings_lost(self):
        other = Quote.objects.create(
            quote_request=self.request,
            shop=self.shop,
            created_by=self.owner,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1100.00"),
            sent_at=timezone.now(),
        )
        accepted_version = record_quote_version(quote_request=self.request, quote=self.quote)
        sibling_version = record_quote_version(quote_request=self.request, quote=other)

        option = ProductionOption.objects.create(
            quote_request=self.request,
            shop=self.shop,
            production_cost=Decimal("700.00"),
            status=ProductionOption.SELECTED,
            created_by=self.owner,
        )
        self.quote.production_option = option
        self.quote.save(update_fields=["production_option"])

        accept_quote_for_payment(quote=self.quote, accepted_by=self.client)

        accepted_version.refresh_from_db()
        sibling_version.refresh_from_db()
        self.assertEqual(accepted_version.outcome, QuoteVersionOutcome.ACCEPTED)
        self.assertEqual(sibling_version.outcome, QuoteVersionOutcome.LOST)
        self.assertEqual(accepted_version.total, Decimal("1000.00"))

    def test_expired_quote_records_expired_outcome(self):
        from quotes.guardrails import expire_quote

        self.quote.expires_at = timezone.now() - timedelta(hours=1)
        self.quote.save(update_fields=["expires_at", "updated_at"])
        record_quote_version(quote_request=self.request, quote=self.quote)

        self.assertTrue(expire_quote(quote=self.quote, notify_manager=False))
        latest = QuoteVersion.objects.filter(quote_request=self.request).first()
        self.assertEqual(latest.outcome, QuoteVersionOutcome.EXPIRED)
