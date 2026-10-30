"""Prompt 6 — speed tiers, odd-hours windows, estimates and rush protection."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from jobs.completion_actuals import COMPLETION_ACTUALS_EVENT
from jobs.models import JobEvent, ManagedJob
from jobs.timing_services import record_lateness, set_due_date_at_acceptance
from pricing.models import (
    EstimatePolicy,
    PublicHoliday,
    ShopSpeedTierOptIn,
    ShopTimeWindowOptIn,
    SpeedTier,
    TimeWindowRule,
)
from pricing.services.platform_fee_policy import calculate_financial_split
from quotes.choices import QuoteOfferStatus
from quotes.models import ProductionOption, Quote, QuoteRequest
from services.pricing.speed_tiers import (
    apply_speed_selection,
    build_speed_options,
    buyer_speed_payload,
    due_date_for_request,
    estimate_for_spec,
    select_speed_option,
)
from shops.models import Shop

EAT = ZoneInfo("Africa/Nairobi")


def monday(hour: int = 9, *, day: date | None = None) -> datetime:
    day = day or date(2026, 11, 2)  # Monday
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=EAT)


class SpeedTierTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="speed-printer@printy.test", password="pass12345", role=User.Role.PRODUCTION
        )
        self.shop = Shop.objects.create(
            owner=self.owner, name="Speed Press", slug="speed-press", is_active=True
        )
        for key in ("faster", "fastest"):
            ShopSpeedTierOptIn.objects.create(
                shop=self.shop, tier=SpeedTier.objects.get(key=key), is_active=True
            )
        for rule in TimeWindowRule.objects.filter(multiplier__gt=1):
            ShopTimeWindowOptIn.objects.create(shop=self.shop, rule=rule, is_active=True)

    def _option(self, key: str, *, start: datetime, base: str = "1000.00", category: str = "flyer"):
        options = build_speed_options(
            shop=self.shop,
            product_category=category,
            base_production_cost=Decimal(base),
            start_at=start,
        )
        option = select_speed_option(options=options, tier_key=key)
        self.assertIsNotNone(option, [item["tier"]["key"] for item in options])
        return option

    def test_each_tier_price_is_the_higher_of_speed_and_window(self):
        start = monday(9)
        standard = self._option("standard", start=start)
        faster = self._option("faster", start=start)
        fastest = self._option("fastest", start=start)

        self.assertEqual(standard["multiplier"], "1.00")
        self.assertEqual(standard["production_total"], "1000.00")
        # Faster lands at 21:00, which is the evening window (1.5x), so the
        # higher-of rule keeps 1.5x — it must NOT become 2.25x.
        self.assertEqual(faster["multiplier"], "1.50")
        self.assertEqual(faster["production_total"], "1500.00")
        self.assertEqual(faster["applied_rule"], "speed_tier")
        self.assertEqual(fastest["multiplier"], "2.00")
        self.assertEqual(fastest["production_total"], "2000.00")

    def test_sunday_work_uses_the_two_hundred_percent_window(self):
        option = self._option("faster", start=monday(10, day=date(2026, 11, 1)))

        self.assertEqual(option["time_window"]["key"], "sunday")
        self.assertEqual(option["multiplier"], "2.00")
        self.assertEqual(option["production_total"], "2000.00")
        # The 1.5x speed tier is not stacked on top of the 2x Sunday rule.
        self.assertEqual(option["applied_rule"], "time_window")
        self.assertIn("Sunday", option["surcharge_line"])

    def test_public_holiday_uses_the_configured_holiday_table(self):
        PublicHoliday.objects.create(date=date(2026, 11, 2), name="Test Holiday")
        option = self._option("fastest", start=monday(9))

        self.assertEqual(option["time_window"]["key"], "public_holiday")
        self.assertEqual(option["multiplier"], "2.00")
        self.assertEqual(option["production_total"], "2000.00")

    def test_tier_is_hidden_when_the_printer_has_not_opted_in(self):
        other = Shop.objects.create(owner=self.owner, name="No Rush Press", slug="no-rush", is_active=True)
        options = build_speed_options(
            shop=other,
            product_category="flyer",
            base_production_cost=Decimal("1000.00"),
            start_at=monday(9),
        )

        self.assertEqual([item["tier"]["key"] for item in options], ["standard"])

    def test_tier_is_hidden_when_the_required_window_is_not_accepted(self):
        self.shop.time_window_optins.filter(rule__key="evening").update(is_active=False)
        options = build_speed_options(
            shop=self.shop,
            product_category="flyer",
            base_production_cost=Decimal("1000.00"),
            start_at=monday(9),
        )

        self.assertNotIn("faster", [item["tier"]["key"] for item in options])

    def test_printer_payout_includes_the_full_uplift(self):
        option = self._option("fastest", start=monday(9))
        split = calculate_financial_split(
            production_cost=Decimal(option["production_total"]),
            manager_markup=Decimal("500.00"),
        )

        self.assertEqual(Decimal(option["production_total"]), Decimal("2000.00"))
        self.assertGreaterEqual(split.shop_payout, Decimal("2000.00"))
        self.assertEqual(split.client_total, Decimal("2500.00"))
        self.assertGreaterEqual(split.printy_fee, Decimal("0.00"))

    def test_manager_margin_and_fee_are_computed_on_the_uplifted_base(self):
        base_split = calculate_financial_split(
            production_cost=Decimal("1000.00"), manager_markup=Decimal("500.00")
        )
        rush_split = calculate_financial_split(
            production_cost=Decimal("2000.00"), manager_markup=Decimal("500.00")
        )

        self.assertEqual(base_split.client_total, Decimal("1500.00"))
        self.assertEqual(rush_split.client_total, Decimal("2500.00"))
        self.assertNotEqual(base_split.printy_fee, rush_split.printy_fee)
        self.assertGreaterEqual(rush_split.shop_payout, Decimal("2000.00"))

    def test_buyer_payload_has_no_printer_identity_or_breakdown(self):
        options = build_speed_options(
            shop=self.shop,
            product_category="flyer",
            base_production_cost=Decimal("1000.00"),
            start_at=monday(9),
        )
        payload = buyer_speed_payload(options=options)
        serialized = str(payload).lower()

        self.assertNotIn("speed press", serialized)
        self.assertNotIn("base_production_cost", payload)
        self.assertNotIn("shop", payload)
        self.assertEqual(len(payload["options"]), 3)
        self.assertTrue(all("price" in item for item in payload["options"]))
        self.assertTrue(all("ready_by" in item for item in payload["options"]))

    def test_selection_freezes_the_terms_on_the_request(self):
        request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.owner,
            customer_name="Speed Buyer",
            request_reference="QR-SPEED-1",
        )
        option = self._option("fastest", start=monday(9))

        snapshot = apply_speed_selection(quote_request=request, option=option)
        request.refresh_from_db()

        self.assertEqual(request.speed_tier, "fastest")
        self.assertEqual(request.speed_multiplier, Decimal("2.00"))
        self.assertEqual(request.production_uplift, Decimal("1000.00"))
        self.assertEqual(request.rush_premium, Decimal("1000.00"))
        self.assertEqual(request.speed_snapshot["ready_by"], option["ready_by"])
        self.assertTrue(snapshot["locked"] is False)

    def test_due_date_uses_the_selected_ready_time_and_locks(self):
        request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.owner,
            customer_name="Locked Buyer",
            request_reference="QR-SPEED-2",
            due_date=monday(9) + timedelta(days=5),
        )
        option = self._option("fastest", start=monday(9))
        apply_speed_selection(quote_request=request, option=option)
        quote = Quote.objects.create(
            quote_request=request,
            shop=self.shop,
            status=QuoteOfferStatus.ACCEPTED,
            accepted_at=timezone.now(),
            total=Decimal("3000.00"),
        )

        start = monday(9)
        due = set_due_date_at_acceptance(quote=quote, actor=self.owner)

        request.refresh_from_db()
        self.assertEqual(due, datetime.fromisoformat(option["ready_by"]))
        self.assertEqual(request.due_date, due)
        self.assertTrue(request.speed_snapshot["locked"])
        # Running it again does not move the locked date.
        again = set_due_date_at_acceptance(quote=quote, actor=self.owner)
        self.assertEqual(again, due)
        self.assertNotEqual(start, due)

    def test_configured_estimate_source_is_measured_or_declared(self):
        policy = EstimatePolicy.objects.filter(is_active=True).first()
        policy.minimum_measured_jobs = 2
        policy.save(update_fields=["minimum_measured_jobs"])
        for index in range(2):
            job = ManagedJob.objects.create(
                title=f"Flyer job {index}",
                assigned_shop=self.shop,
                status="completed",
            )
            JobEvent.objects.create(
                managed_job=job,
                stage="completion",
                event=COMPLETION_ACTUALS_EVENT,
                source=JobEvent.SOURCE_SYSTEM,
                metadata={
                    "products": ["flyer"],
                    "product": "flyer",
                    "stage_durations": {"printing": 240, "finishing": 120},
                },
            )

        measured = estimate_for_spec(product_category="flyer", shop=self.shop)
        self.assertEqual(measured["source"], "measured")
        self.assertEqual(measured["hours"], 6.0)
        self.assertEqual(measured["jobs_counted"], 2)

        policy.minimum_measured_jobs = 5
        policy.save(update_fields=["minimum_measured_jobs"])
        declared = estimate_for_spec(product_category="flyer", shop=self.shop, declared_hours=24)
        self.assertEqual(declared["source"], "declared")
        self.assertEqual(declared["hours"], 24)
        self.assertEqual(declared["label"], "24h")

    def test_late_rush_job_refunds_or_holds_the_premium_as_separate_lines(self):
        job = ManagedJob.objects.create(
            title="Rush flyer",
            assigned_shop=self.shop,
            status="in_production",
            current_stage="production",
            due_date=timezone.now() - timedelta(hours=1),
            speed_tier="fastest",
            rush_premium=Decimal("1000.00"),
            rush_premium_status="pending",
        )

        record_lateness(job)

        job.refresh_from_db()
        self.assertEqual(job.rush_premium_status, "refunded")
        settlement = JobEvent.objects.get(managed_job=job, event="rush_premium_settlement")
        penalty = JobEvent.objects.get(managed_job=job, event="rush_premium_penalty")
        self.assertEqual(settlement.metadata["premium"], "1000.00")
        self.assertEqual(settlement.metadata["status"], "refunded")
        self.assertEqual(penalty.metadata["penalty_rate"], "0.10")
        self.assertEqual(settlement.metadata["attributed_owner"], "printer")

    def test_buyer_caused_lateness_does_not_refund_the_premium(self):
        job = ManagedJob.objects.create(
            title="Rush booklet",
            assigned_shop=self.shop,
            status="payment_confirmed",
            current_stage="artwork_approval",
            due_date=timezone.now() - timedelta(hours=1),
            speed_tier="faster",
            rush_premium=Decimal("500.00"),
            rush_premium_status="pending",
        )

        record_lateness(job)

        job.refresh_from_db()
        self.assertEqual(job.rush_premium_status, "not_refunded")
        settlement = JobEvent.objects.get(managed_job=job, event="rush_premium_settlement")
        self.assertTrue(settlement.metadata["buyer_caused"])

    def test_existing_quotes_without_a_speed_choice_are_unchanged(self):
        request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.owner,
            customer_name="Legacy Buyer",
            request_reference="QR-LEGACY",
        )
        fallback = monday(9) + timedelta(days=2)

        self.assertEqual(due_date_for_request(quote_request=request, fallback=fallback), fallback)
        self.assertEqual(request.speed_multiplier, Decimal("1.00"))
        self.assertEqual(request.production_uplift, Decimal("0.00"))

    def test_quote_acceptance_locks_price_and_date_together(self):
        from quotes.acceptance import accept_quote

        request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.owner,
            customer_name="Accept Buyer",
            request_reference="QR-ACCEPT",
            request_snapshot={"user_id": self.owner.id},
        )
        option = self._option("fastest", start=monday(9))
        apply_speed_selection(quote_request=request, option=option)
        production = ProductionOption.objects.create(
            quote_request=request, shop=self.shop, production_cost=Decimal("2000.00")
        )
        quote = Quote.objects.create(
            quote_request=request,
            shop=self.shop,
            production_option=production,
            status=QuoteOfferStatus.SENT,
            sent_at=timezone.now(),
            total=Decimal("2500.00"),
        )

        with mock.patch("quotes.acceptance.create_payment_for_quote", return_value=object()):
            accepted, _payment = accept_quote(quote=quote, accepted_by=self.owner)

        accepted.refresh_from_db()
        request.refresh_from_db()
        self.assertIsNotNone(accepted.pricing_locked_at)
        self.assertEqual(accepted.status, QuoteOfferStatus.ACCEPTED)
        self.assertEqual(request.due_date, datetime.fromisoformat(option["ready_by"]))
        self.assertTrue(request.speed_snapshot["locked"])
