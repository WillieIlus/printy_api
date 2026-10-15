"""Law Part 4 (6.2-6.8) timeliness tests.

Covers every item of law 6.8:
  * due date set at acceptance; no production without one
  * every stage transition writes an event; events cannot be mutated
  * buyer-caused delay is not attributed to manager or printer
  * a manager change that moves the date produces "Date updated" + revision
  * on-time computed against the acknowledged date; revisions counted
  * public score hidden below MIN_JOBS_FOR_PUBLIC_SCORE
  * role payloads include only allowlisted timeliness fields
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from jobs.assignment_services import accept_assignment, mark_assignment_in_production
from jobs.choices import JobAssignmentStatus
from jobs.models import JobAssignment, JobEvent, ManagedJob
from jobs.timing import (
    add_working_hours,
    attribute_delay,
    compute_due_date,
    job_timing,
    on_time_rate,
    timing_fields,
)
from jobs.timing_config import (
    MIN_JOBS_FOR_PUBLIC_SCORE,
    SHOP_TIMEZONE,
    STAGE_ORDER,
)
from jobs.timing_services import (
    assert_due_date_for_production,
    record_date_revision,
    record_lateness,
    set_due_date_at_acceptance,
    sync_job_timing,
)
from quotes.acceptance import accept_quote_for_payment
from quotes.choices import QuoteOfferStatus, QuoteStatus
from quotes.models import (
    ProductionOption,
    Quote,
    QuoteDateRevision,
    QuoteRequest,
)
from quotes.overrides import acknowledge_price_update, record_override
from services.pricing.role_projection import (
    ROLE_ANONYMOUS,
    ROLE_BUYER,
    ROLE_MANAGER,
    ROLE_PRINTER,
    project_canonical,
)
from shops.models import Shop


def _eat(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=SHOP_TIMEZONE)


class WorkingTimeTests(TestCase):
    """6.2: working time uses Africa/Nairobi days, hours and holidays."""

    def test_next_working_instant_skips_weekend(self):
        saturday = _eat(2026, 1, 3, 10, 0)
        result = add_working_hours(saturday, 1).astimezone(SHOP_TIMEZONE)
        self.assertEqual((result.year, result.month, result.day), (2026, 1, 5))
        self.assertEqual((result.hour, result.minute), (9, 0))

    def test_working_hours_wrap_past_day_end(self):
        friday = _eat(2026, 1, 9, 17, 30)
        result = add_working_hours(friday, 1).astimezone(SHOP_TIMEZONE)
        self.assertEqual((result.month, result.day), (1, 12))
        self.assertEqual((result.hour, result.minute), (8, 30))

    def test_public_holiday_is_not_a_working_day(self):
        labour_day = _eat(2026, 5, 1, 8, 0)
        result = add_working_hours(labour_day, 1).astimezone(SHOP_TIMEZONE)
        self.assertEqual((result.month, result.day), (5, 4))
        self.assertEqual(result.hour, 9)

    def test_due_date_is_computed_in_utc(self):
        start = _eat(2026, 1, 5, 9, 0)
        due = compute_due_date(start)
        self.assertEqual(due.tzinfo, dt_timezone.utc)
        self.assertGreater(due, start)

    def test_stage_order_matches_law(self):
        self.assertEqual(
            STAGE_ORDER,
            ("quote_accepted", "artwork_approval", "production", "qc", "delivery"),
        )


class AcceptanceDueDateTests(TestCase):
    """6.2: the due date is set at acceptance from the config."""

    def setUp(self):
        self.owner = User.objects.create_user(email="p4-owner@example.com", password="pass12345")
        self.buyer = User.objects.create_user(email="p4-buyer@example.com", password="pass12345")
        self.shop = Shop.objects.create(owner=self.owner, name="Part4 Shop", slug="part4-shop", is_active=True)
        self.request = QuoteRequest.objects.create(
            shop=self.shop,
            created_by=self.buyer,
            customer_name="P4 Client",
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
        option = ProductionOption.objects.create(
            quote_request=self.request,
            shop=self.shop,
            production_cost=Decimal("700.00"),
            status=ProductionOption.SELECTED,
            created_by=self.owner,
        )
        self.quote.production_option = option
        self.quote.save(update_fields=["production_option"])

    def test_due_date_is_set_at_acceptance(self):
        self.assertIsNone(self.request.due_date)
        accepted, _payment = accept_quote_for_payment(quote=self.quote, accepted_by=self.buyer)
        self.request.refresh_from_db()
        self.assertIsNotNone(self.request.due_date)
        self.assertEqual(self.request.due_date.tzinfo, dt_timezone.utc)
        expected = compute_due_date(accepted.accepted_at)
        self.assertAlmostEqual(
            abs((self.request.due_date - expected).total_seconds()), 0, delta=2
        )

    def _make_job_without_due_date(self):
        job = ManagedJob.objects.create(
            title="No due date",
            source_quote_request=self.request,
            source_quote=self.quote,
            client=self.buyer,
            assigned_shop=self.shop,
            created_by=self.buyer,
            status="assigned",
        )
        assignment = JobAssignment.objects.create(
            managed_job=job,
            assigned_shop=self.shop,
            status=JobAssignmentStatus.ACCEPTED,
        )
        return job, assignment

    def test_job_cannot_enter_production_without_due_date(self):
        job, assignment = self._make_job_without_due_date()
        self.assertIsNone(job.due_date)
        with self.assertRaises(ValidationError):
            mark_assignment_in_production(assignment=assignment, actor=self.owner)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, JobAssignmentStatus.ACCEPTED)

    def test_job_enters_production_once_due_date_set(self):
        job, assignment = self._make_job_without_due_date()
        job.due_date = compute_due_date(timezone.now())
        job.save(update_fields=["due_date"])
        assert_due_date_for_production(job)
        mark_assignment_in_production(assignment=assignment, actor=self.owner)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, JobAssignmentStatus.IN_PRODUCTION)


class EventLogTests(TestCase):
    """6.3: append-only event log; no update or delete path."""

    def setUp(self):
        self.owner = User.objects.create_user(email="ev-owner@example.com", password="pass12345")
        self.buyer = User.objects.create_user(email="ev-buyer@example.com", password="pass12345")
        self.shop = Shop.objects.create(owner=self.owner, name="Event Shop", slug="event-shop", is_active=True)
        self.request = QuoteRequest.objects.create(
            shop=self.shop, created_by=self.buyer, customer_name="Ev", status=QuoteStatus.QUOTED
        )
        self.quote = Quote.objects.create(
            quote_request=self.request,
            shop=self.shop,
            created_by=self.owner,
            status=QuoteOfferStatus.SENT,
            total=Decimal("1000.00"),
            sent_at=timezone.now(),
        )
        self.job = ManagedJob.objects.create(
            title="Event job",
            source_quote_request=self.request,
            source_quote=self.quote,
            client=self.buyer,
            assigned_shop=self.shop,
            created_by=self.buyer,
            status="assigned",
            due_date=compute_due_date(timezone.now()),
        )
        self.assignment = JobAssignment.objects.create(
            managed_job=self.job,
            assigned_shop=self.shop,
            status=JobAssignmentStatus.PENDING,
        )

    def test_stage_transition_writes_event_and_stores_utc(self):
        accept_assignment(assignment=self.assignment, actor=self.owner)
        event = JobEvent.objects.filter(managed_job=self.job).order_by("-id").first()
        self.assertIsNotNone(event)
        self.assertEqual(event.occurred_at.tzinfo, dt_timezone.utc)
        self.assertTrue(event.stage)
        self.assertTrue(event.actor_role)

    def test_events_cannot_be_updated_or_deleted(self):
        accept_assignment(assignment=self.assignment, actor=self.owner)
        event = JobEvent.objects.filter(managed_job=self.job).first()
        event.event = "tampered"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            JobEvent.objects.filter(pk=event.pk).update(event="tampered")
        with self.assertRaises(ValidationError):
            JobEvent.objects.filter(pk=event.pk).delete()

    def test_correction_is_a_new_referencing_event(self):
        from jobs.timing_services import record_correction

        accept_assignment(assignment=self.assignment, actor=self.owner)
        original = JobEvent.objects.filter(managed_job=self.job).first()
        correction = record_correction(original=original, actor=self.owner, actor_role="manager", reason="typo")
        self.assertEqual(correction.event, JobEvent.CORRECTION)
        self.assertEqual(correction.references_id, original.id)
        self.assertEqual(JobEvent.objects.filter(managed_job=self.job).count(), 2)


class OwnershipAttributionTests(TestCase):
    """6.4: buyer-caused delay is never charged to manager or printer."""

    def _job(self, status="payment_confirmed", *, due=None):
        owner = User.objects.create_user(email="att-owner@example.com", password="pass12345")
        buyer = User.objects.create_user(email="att-buyer@example.com", password="pass12345")
        shop = Shop.objects.create(owner=owner, name="Att Shop", slug="att-shop", is_active=True)
        request = QuoteRequest.objects.create(shop=shop, created_by=buyer, customer_name="Att", status=QuoteStatus.QUOTED)
        quote = Quote.objects.create(
            quote_request=request, shop=shop, created_by=owner,
            status=QuoteOfferStatus.SENT, total=Decimal("1000.00"), sent_at=timezone.now(),
        )
        job = ManagedJob.objects.create(
            title="Att", source_quote_request=request, source_quote=quote,
            client=buyer, assigned_shop=shop, created_by=buyer, status=status,
            due_date=due or compute_due_date(timezone.now() - timedelta(days=1)),
        )
        return job

    def test_artwork_stage_delay_is_attributed_to_buyer_only(self):
        job = self._job(status="payment_confirmed")
        attribution = attribute_delay(managed_job=job)
        self.assertEqual(attribution["stage"], "artwork_approval")
        self.assertEqual(attribution["owner"], "buyer")
        self.assertTrue(attribution["buyer_caused"])
        self.assertFalse(attribution["counted_against_manager"])
        self.assertFalse(attribution["counted_against_printer"])

    def test_production_stage_delay_is_attributed_to_printer(self):
        job = self._job(status="in_production")
        attribution = attribute_delay(managed_job=job)
        self.assertEqual(attribution["owner"], "printer")
        self.assertTrue(attribution["counted_against_printer"])

    def test_record_lateness_attributes_to_current_stage_owner(self):
        job = self._job(status="payment_confirmed", due=timezone.now() - timedelta(hours=2))
        record_lateness(job, now=timezone.now())
        delay_event = JobEvent.objects.filter(managed_job=job, event=JobEvent.DELAY).first()
        self.assertIsNotNone(delay_event)
        self.assertEqual(delay_event.metadata["attributed_owner"], "buyer")
        self.assertFalse(delay_event.metadata["counted_against_manager"])
        self.assertFalse(delay_event.metadata["counted_against_printer"])


class DateRevisionTests(TestCase):
    """6.5 / 6.6: non-silent date changes, acknowledgement, warnings recorded."""

    def setUp(self):
        self.owner = User.objects.create_user(email="dr-owner@example.com", password="pass12345")
        self.buyer = User.objects.create_user(email="dr-buyer@example.com", password="pass12345")
        self.shop = Shop.objects.create(owner=self.owner, name="DR Shop", slug="dr-shop", is_active=True)
        self.request = QuoteRequest.objects.create(
            shop=self.shop, created_by=self.buyer, customer_name="DR", status=QuoteStatus.QUOTED
        )
        self.quote = Quote.objects.create(
            quote_request=self.request, shop=self.shop, created_by=self.owner,
            status=QuoteOfferStatus.SENT, total=Decimal("1000.00"), sent_at=timezone.now(),
        )
        self.job = ManagedJob.objects.create(
            title="DR", source_quote_request=self.request, source_quote=self.quote,
            client=self.buyer, assigned_shop=self.shop, created_by=self.buyer,
            status="in_production",
            due_date=compute_due_date(timezone.now()),
        )
        self.request.due_date = self.job.due_date
        self.request.save(update_fields=["due_date"])

    def test_manager_change_that_moves_date_flags_buyer_and_logs_revision(self):
        old = self.job.due_date
        new = old + timedelta(days=2)
        record_override(
            quote_request=self.request,
            field="paper_substitution",
            new_value={"label": "Gloss 150gsm"},
            user=self.owner,
            new_due_date=new,
        )
        self.request.refresh_from_db()
        self.job.refresh_from_db()
        self.assertTrue(self.request.date_updated)
        self.assertEqual(self.request.date_updated_old, old)
        self.assertEqual(self.request.date_updated_new, new)
        self.assertEqual(self.request.date_revisions.count(), 1)
        self.assertEqual(self.job.due_date_revision_count, 1)

    def test_buyer_acknowledgement_sets_new_due_date(self):
        from jobs.timing_services import acknowledge_date_update

        old = self.job.due_date
        new = old + timedelta(days=3)
        record_date_revision(
            quote_request=self.request, new_date=new, old_date=old,
            cause=QuoteDateRevision.CAUSE_MANAGER, reason="Press down", user=self.owner,
        )
        self.assertEqual(self.request.due_date, old)
        acknowledge_date_update(quote_request=self.request, user=self.buyer)
        self.request.refresh_from_db()
        self.job.refresh_from_db()
        self.assertFalse(self.request.date_updated)
        self.assertEqual(self.request.due_date, new)
        self.assertEqual(self.job.due_date, new)
        self.assertIsNotNone(self.job.due_date_acknowledged_at)
        self.assertEqual(self.request.date_acknowledged_by_id, self.buyer.id)

    def test_no_revision_when_date_unchanged(self):
        revision = record_date_revision(
            quote_request=self.request, new_date=self.job.due_date, user=self.owner,
        )
        self.assertIsNone(revision)
        self.assertEqual(self.request.date_revisions.count(), 0)

    def test_date_revisions_are_append_only(self):
        record_date_revision(
            quote_request=self.request, new_date=self.job.due_date + timedelta(days=1), user=self.owner,
        )
        revision = self.request.date_revisions.first()
        with self.assertRaises(ValidationError):
            revision.delete()
        with self.assertRaises(ValidationError):
            QuoteDateRevision.objects.filter(pk=revision.pk).update(reason="x")

    def test_acknowledged_date_drives_on_time_and_revisions_counted(self):
        from jobs.timing_services import acknowledge_date_update

        old = self.job.due_date
        new = old - timedelta(days=2)  # pulled the date in
        record_date_revision(quote_request=self.request, new_date=new, old_date=old, user=self.owner)
        acknowledge_date_update(quote_request=self.request, user=self.buyer)
        self.job.refresh_from_db()
        # Now record lateness against the acknowledged (new) date.
        record_lateness(self.job, now=new + timedelta(hours=1))
        self.job.refresh_from_db()
        self.assertTrue(self.job.is_late)
        self.assertGreater(self.job.lateness_minutes, 0)
        rate = on_time_rate(completed_jobs=[self.job])
        self.assertIsNone(rate)  # below threshold
        self.assertEqual(self.job.due_date_revision_count, 1)


class ReliabilityScoreTests(TestCase):
    """6.7: public score hidden below MIN_JOBS_FOR_PUBLIC_SCORE."""

    def _jobs(self, count, late_count):
        owner = User.objects.create_user(email="rel-owner@example.com", password="pass12345")
        shop = Shop.objects.create(owner=owner, name="Rel Shop", slug="rel-shop", is_active=True)
        jobs = []
        for i in range(count):
            jobs.append(
                ManagedJob.objects.create(
                    title=f"rel-{i}", assigned_shop=shop, created_by=owner,
                    status="completed", due_date=compute_due_date(timezone.now()),
                    is_late=i < late_count,
                )
            )
        return jobs

    def test_score_hidden_below_threshold(self):
        jobs = self._jobs(MIN_JOBS_FOR_PUBLIC_SCORE - 1, late_count=0)
        self.assertIsNone(on_time_rate(completed_jobs=jobs))

    def test_score_returned_at_or_above_threshold(self):
        jobs = self._jobs(MIN_JOBS_FOR_PUBLIC_SCORE, late_count=1)
        rate = on_time_rate(completed_jobs=jobs)
        self.assertIsNotNone(rate)
        self.assertEqual(rate, round((MIN_JOBS_FOR_PUBLIC_SCORE - 1) / MIN_JOBS_FOR_PUBLIC_SCORE * 100, 1))


class RoleTimingProjectionTests(TestCase):
    """6.8: role payloads include only allowlisted timeliness fields."""

    def setUp(self):
        self.owner = User.objects.create_user(email="rt-owner@example.com", password="pass12345")
        self.buyer = User.objects.create_user(email="rt-buyer@example.com", password="pass12345")
        self.shop = Shop.objects.create(owner=self.owner, name="RT Shop", slug="rt-shop", is_active=True)
        self.request = QuoteRequest.objects.create(
            shop=self.shop, created_by=self.buyer, customer_name="RT", status=QuoteStatus.QUOTED
        )
        self.quote = Quote.objects.create(
            quote_request=self.request, shop=self.shop, created_by=self.owner,
            status=QuoteOfferStatus.SENT, total=Decimal("1000.00"), sent_at=timezone.now(),
        )
        self.job = ManagedJob.objects.create(
            title="RT", source_quote_request=self.request, source_quote=self.quote,
            client=self.buyer, assigned_shop=self.shop, created_by=self.buyer,
            status="in_production",
            due_date=compute_due_date(timezone.now()),
        )
        sync_job_timing(self.job)

    def _projected(self, role):
        return project_canonical(timing_fields(self.job), role)

    def test_buyer_sees_ball_but_not_internal_timing(self):
        buyer = self._projected(ROLE_BUYER)
        for key in ("due_date", "current_stage", "stage_owner", "next_action", "time_remaining_minutes"):
            self.assertIn(key, buyer)
        for forbidden in ("stage_timestamps", "lateness_minutes", "revision_count", "on_time_rate"):
            self.assertNotIn(forbidden, buyer)

    def test_manager_sees_stage_slas_timestamps_and_revisions(self):
        manager = self._projected(ROLE_MANAGER)
        for key in ("stage_timestamps", "lateness_minutes", "revision_count", "stage_sla_hours"):
            self.assertIn(key, manager)

    def test_printer_sees_timestamps_but_not_reliability_internals(self):
        printer = self._projected(ROLE_PRINTER)
        self.assertIn("stage_timestamps", printer)
        self.assertIn("due_date", printer)
        self.assertNotIn("revision_count", printer)
        self.assertNotIn("lateness_minutes", printer)

    def test_anonymous_timing_only_has_public_score_key(self):
        anonymous = self._projected(ROLE_ANONYMOUS)
        self.assertNotIn("due_date", anonymous)
        self.assertNotIn("stage_timestamps", anonymous)

    def test_job_timing_reports_current_owner(self):
        timing = job_timing(managed_job=self.job)
        self.assertEqual(timing["current_stage"], "production")
        self.assertEqual(timing["stage_owner"], "printer")
        self.assertIsNotNone(timing["stage_due_at"])
