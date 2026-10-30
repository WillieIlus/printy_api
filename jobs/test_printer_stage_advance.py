"""Prompt 4 — one-action printer stage advance, idempotency and undo."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from jobs.choices import (
    JobAssignmentStatus,
    ManagedJobPaymentStatus,
    ManagedJobStatus,
)
from jobs.models import JobAssignment, JobEvent, JobStatusEvent, ManagedJob
from quotes.models import QuoteRequest
from shops.models import Shop


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PrinterStageAdvanceTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.buyer = User.objects.create_user(
            email="asha@printy.test", password="pass12345", role=User.Role.CLIENT, name="Asha Buyer"
        )
        self.manager = User.objects.create_user(
            email="maya@printy.test",
            password="pass12345",
            role=User.Role.PARTNER,
            capability_overrides={"can_source_jobs": True, "can_manage_clients": True},
        )
        self.printer = User.objects.create_user(
            email="kito@printy.test", password="pass12345", role=User.Role.PRODUCTION, name="Kito Printer"
        )
        self.other_printer = User.objects.create_user(
            email="other@printy.test", password="pass12345", role=User.Role.PRODUCTION
        )
        self.shop = Shop.objects.create(
            owner=self.printer, name="Kito Press", slug="kito-press", is_active=True
        )
        self.other_shop = Shop.objects.create(
            owner=self.other_printer, name="Other Press", slug="other-press", is_active=True
        )

    # --- fixtures ---------------------------------------------------------
    def _job(self, *, unpaid=False, finishing=("Lamination",), status=ManagedJobStatus.PAYMENT_CONFIRMED):
        quote_request = QuoteRequest.objects.create(
            created_by=self.buyer,
            customer_name="Asha Buyer",
            customer_email="secret-buyer@printy.test",
            customer_phone="+254700111222",
            request_reference="QR-2026-9001",
            request_snapshot={"request_snapshot": {"finishings": [{"label": label} for label in finishing]}},
        )
        now = timezone.now()
        return ManagedJob.objects.create(
            title="Business cards",
            client=self.buyer,
            created_by=self.buyer,
            broker=self.manager,
            assigned_shop=self.shop,
            source_quote_request=quote_request,
            status=status,
            payment_status=ManagedJobPaymentStatus.PENDING if unpaid else ManagedJobPaymentStatus.CONFIRMED,
            client_total=Decimal("2500.00"),
            payment_confirmed_at=None if unpaid else now,
            accepted_at=now,
            due_date=now + timedelta(hours=80),
        )

    def _assignment(self, job, *, status=JobAssignmentStatus.PENDING, shop=None):
        return JobAssignment.objects.create(
            managed_job=job,
            assigned_shop=shop or self.shop,
            status=status,
            shop_payout=Decimal("2000.00"),
        )

    def _post(self, assignment, *, user=None, **body):
        self.api.force_authenticate(user or self.printer)
        return self.api.post(f"/api/job-assignments/{assignment.id}/advance/", body, format="json")

    def _undo(self, assignment, *, user=None):
        self.api.force_authenticate(user or self.printer)
        return self.api.post(f"/api/job-assignments/{assignment.id}/undo/", {}, format="json")

    def _label(self, response):
        return response.json()["printer_action"]["label"]

    def _advance(self, assignment, label):
        response = self._post(assignment, expected_stage=self._current_stage(assignment))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self._label(response), label)
        return response

    def _current_stage(self, assignment):
        # Read the server's current action directly from the projection.
        from jobs.printer_workflow import build_printer_progress

        assignment.refresh_from_db()
        return build_printer_progress(assignment)["next_action"]["stage_key"]

    # --- tests ------------------------------------------------------------
    def test_assigned_printer_advances_with_labels_from_the_api(self):
        job = self._job()
        assignment = self._assignment(job)

        # The card's label comes from the API before any tap.
        self.api.force_authenticate(self.printer)
        listing = self.api.get("/api/shop/assignments/")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["printer_action"]["label"], "Accept job")

        first = self._post(assignment)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(self._label(first), "Artwork checked")

        second = self._advance(assignment, "Start printing")
        self.assertEqual(second.json()["status"], JobAssignmentStatus.ACCEPTED)
        self.assertEqual(second.json()["printer_workflow"]["stages"][1]["state"], "current")

        self._advance(assignment, "Printing done")
        # The first finishing step is auto-started: the printer only taps "done".
        self._advance(assignment, "Lamination done")
        self._advance(assignment, "QC passed")
        self._advance(assignment, "Ready for dispatch")
        last = self._advance(assignment, "All steps complete")
        self.assertEqual(last.json()["printer_action"]["kind"], "none")

    def test_only_the_assigned_printer_can_advance(self):
        job = self._job()
        assignment = self._assignment(job)

        response = self._post(assignment, user=self.other_printer)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(JobEvent.objects.filter(managed_job=job).count(), 0)

    def test_unpaid_job_is_rejected_even_if_assigned(self):
        job = self._job(unpaid=True)
        assignment = self._assignment(job)

        response = self._post(assignment)

        self.assertEqual(response.status_code, 400)
        self.assertIn("not paid", response.json()["detail"])

    def test_out_of_order_expected_stage_is_blocked(self):
        job = self._job()
        assignment = self._assignment(job)

        response = self._post(assignment, expected_stage="quality_check")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "stage_moved")
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, JobAssignmentStatus.PENDING)

    def test_finishing_steps_not_on_the_job_are_skipped_and_never_shown(self):
        job = self._job(finishing=())
        assignment = self._assignment(job)
        self._post(assignment)
        self._advance(assignment, "Start printing")
        self._advance(assignment, "Printing done")
        response = self._advance(assignment, "QC passed")

        keys = [stage["key"] for stage in response.json()["printer_workflow"]["stages"]]
        self.assertFalse([key for key in keys if key.startswith("finishing:")])
        self.assertNotIn("Cutting", response.json()["printer_workflow"]["stages"][-1]["label"])

    def test_double_tap_advances_exactly_once(self):
        job = self._job()
        assignment = self._assignment(job)
        self._post(assignment)
        expected = self._current_stage(assignment)

        first = self._post(assignment, expected_stage=expected)
        second = self._post(assignment, expected_stage=expected)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self._label(second), "Start printing")
        completed = JobEvent.objects.filter(
            managed_job=job,
            event=JobEvent.STAGE_COMPLETED,
            metadata__printer_stage="artwork_check",
        )
        self.assertEqual(completed.count(), 1)

    def test_double_tap_without_a_body_also_advances_once(self):
        job = self._job()
        assignment = self._assignment(job)
        self._post(assignment)  # accept
        self._post(assignment)  # artwork completed at the same instant
        self._post(assignment)  # a double tap must be a no-op

        completed = JobEvent.objects.filter(
            managed_job=job,
            event=JobEvent.STAGE_COMPLETED,
            metadata__printer_stage="artwork_check",
        )
        self.assertEqual(completed.count(), 1)

    def test_undo_works_within_two_minutes_and_keeps_both_timestamps(self):
        job = self._job()
        assignment = self._assignment(job)
        self._post(assignment)
        self._advance(assignment, "Start printing")

        response = self._undo(assignment)

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self._label(response), "Artwork checked")
        originals = JobEvent.objects.filter(
            managed_job=job,
            event=JobEvent.STAGE_COMPLETED,
            metadata__printer_stage="artwork_check",
        )
        self.assertEqual(originals.count(), 1)
        corrections = JobEvent.objects.filter(
            managed_job=job,
            event=JobEvent.CORRECTION,
            metadata__revert=True,
            references_id=originals.first().id,
        )
        self.assertEqual(corrections.count(), 1)

    def test_undo_is_refused_after_two_minutes(self):
        job = self._job()
        assignment = self._assignment(job)
        self._post(assignment)
        self._advance(assignment, "Start printing")

        later = timezone.now() + timedelta(minutes=3)
        with mock.patch("jobs.printer_workflow.timezone.now", return_value=later):
            response = self._undo(assignment)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "undo_expired")
        self.assertEqual(
            JobEvent.objects.filter(managed_job=job, event=JobEvent.CORRECTION).count(), 0
        )

    def test_client_supplied_timestamp_is_ignored(self):
        job = self._job()
        assignment = self._assignment(job)
        before = timezone.now()
        response = self._post(
            assignment,
            expected_stage="accept",
            client_time="2099-01-01T00:00:00Z",
            request_id="retry-1",
        )

        self.assertEqual(response.status_code, 200, response.content)
        # accept is handled by the assignment service, so inspect the first
        # stage event written by this workflow.
        artwork = self._post(
            assignment,
            expected_stage="artwork_check",
            client_time="2099-01-01T00:00:00Z",
            request_id="retry-2",
        )
        self.assertEqual(artwork.status_code, 200, artwork.content)
        event = JobEvent.objects.filter(
            managed_job=job, metadata__printer_stage="artwork_check"
        ).first()
        self.assertIsNotNone(event)
        self.assertLess(event.occurred_at, before + timedelta(seconds=30))
        self.assertTrue(event.metadata["client_timestamp_ignored"])

    def test_late_stage_records_the_printer_as_delay_owner(self):
        job = self._job()
        assignment = self._assignment(job)
        self._post(assignment)
        self._advance(assignment, "Start printing")

        started = timezone.now() - timedelta(hours=4)
        with mock.patch("jobs.printer_workflow.timezone.now", return_value=started):
            self._advance(assignment, "Printing done")
        self._advance(assignment, "Lamination done")

        delay = JobEvent.objects.filter(
            managed_job=job, event=JobEvent.DELAY, metadata__printer_stage="printing"
        ).first()
        self.assertIsNotNone(delay)
        self.assertEqual(delay.metadata["attributed_owner"], "printer")
        self.assertGreater(delay.metadata["over_budget_minutes"], 0)

    def test_response_contains_no_buyer_identity_or_money(self):
        job = self._job()
        assignment = self._assignment(job)
        response = self._post(assignment)

        content = response.content.decode()
        self.assertNotIn("secret-buyer@printy.test", content)
        self.assertNotIn("Asha Buyer", content)
        self.assertNotIn("2500", content)
        self.assertNotIn("margin", content.lower())

    def test_each_advance_emits_a_status_event_for_notifications(self):
        job = self._job()
        assignment = self._assignment(job)
        self._post(assignment)
        self._advance(assignment, "Start printing")

        self.assertTrue(
            JobStatusEvent.objects.filter(
                managed_job=job, event_type="printer_stage_advanced"
            ).exists()
        )

    def test_buyer_tracker_reflects_the_advance_immediately(self):
        job = self._job()
        assignment = self._assignment(job)
        self._post(assignment)
        self._advance(assignment, "Start printing")
        self._advance(assignment, "Printing done")

        self.api.force_authenticate(self.buyer)
        tracked = self.api.get("/api/dashboard/client/tracked-jobs/")
        row = tracked.json()["results"][0]
        self.assertEqual(row["current_stage"], "Printing")

        self.api.force_authenticate(self.printer)
        self._advance(assignment, "Lamination done")
        self.api.force_authenticate(self.buyer)
        row = self.api.get("/api/dashboard/client/tracked-jobs/").json()["results"][0]
        self.assertEqual(row["current_stage"], "Finishing")
