"""Prompt 7: a delivery handover code gates ``completed``.

The printer adds a handover code (e.g. ``K7-4Q``) when marking the job
dispatched. The buyer taps "I received it" and enters the code; only a match
completes the job, and the buyer sees "Your job is complete." Nothing else can
complete a managed job except a platform-admin dispute resolution.
"""

from __future__ import annotations

from django.contrib import admin
from django.test import RequestFactory

from jobs.choices import JobAssignmentStatus, ManagedJobExceptionStatus, ManagedJobStatus
from jobs.handover import MAX_FAILED_ATTEMPTS, normalize_handover_code
from jobs.models import ManagedJob
from jobs.action_resolver import resolve_next_step
from jobs.test_stage1b_actions import Stage1BActionBase


class HandoverBase(Stage1BActionBase):
    def mark_delivered(self, job, *, user=None, **body):
        self.api.force_authenticate(user or self.printer)
        return self.api.post(f"/api/managed-jobs/{job.id}/delivery/mark-delivered/", body, format="json")

    def confirm(self, job, *, user=None, **body):
        self.api.force_authenticate(user or self.buyer)
        return self.api.post(f"/api/managed-jobs/{job.id}/delivery/confirm/", body, format="json")

    def ready_job(self):
        job = self.make_job(status=ManagedJobStatus.READY)
        assignment = self.make_assignment(job, status=JobAssignmentStatus.READY)
        return job, assignment

    def dispatched_job(self, code="K7-4Q"):
        job, assignment = self.ready_job()
        response = self.mark_delivered(job, handover_code=code)
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        return job, assignment


class MarkDispatchedNeedsHandoverCodeTests(HandoverBase):
    def test_mark_dispatched_without_code_is_refused(self):
        job, _ = self.ready_job()
        response = self.mark_delivered(job, note="Rider collected")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "handover_code_required")
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.READY)
        self.assertIsNone(job.delivered_at)

    def test_too_short_code_is_refused(self):
        job, _ = self.ready_job()
        response = self.mark_delivered(job, handover_code="K7")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "handover_code_invalid")
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.READY)

    def test_code_is_stored_normalised_and_never_returned(self):
        job, _ = self.ready_job()
        response = self.mark_delivered(job, handover_code=" k7-4q ")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], ManagedJobStatus.DELIVERED)
        self.assertNotIn("K74Q", response.content.decode())
        self.assertNotIn("handover_code", response.json())
        job.refresh_from_db()
        self.assertEqual(job.handover_code, "K74Q")
        self.assertEqual(job.handover_code_set_by, self.printer)
        self.assertIsNotNone(job.delivered_at)
        # Nobody else reads the code back through the job API either.
        for user, role in ((self.buyer, "client"), (self.manager, "partner"), (self.printer, "production")):
            self.api.force_authenticate(user)
            card = self.api.get(f"/api/managed-jobs/{job.id}/card/", HTTP_X_PRINTY_ACTIVE_ROLE=role)
            self.assertEqual(card.status_code, 200, card.content)
            self.assertNotIn("K74Q", card.content.decode())
            listing = self.api.get("/api/managed-jobs/", HTTP_X_PRINTY_ACTIVE_ROLE=role)
            self.assertNotIn("K74Q", listing.content.decode())

    def test_repeat_mark_dispatched_does_not_change_the_code(self):
        job, _ = self.dispatched_job("K7-4Q")
        response = self.mark_delivered(job, handover_code="ZZZZ-9")
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.assertEqual(job.handover_code, "K74Q")

    def test_printer_card_mark_dispatched_needs_the_code(self):
        job = self.make_job(status=ManagedJobStatus.FINISHING)
        assignment = self.make_assignment(job, status=JobAssignmentStatus.FINISHING)
        for stage, action in (("artwork_check", "completed"), ("printing", "started"), ("printing", "completed"),
                              ("quality_check", "completed")):
            self.complete_printer_stage(job, stage, action)
        step = resolve_next_step(job, "printer")
        self.assertEqual(step["next_action"]["key"], "mark_dispatched")
        self.assertTrue(step["next_action"]["params"].get("requires_handover_code"))

        refused = self.post_action(user=self.printer, role="production", job=job, code="mark_dispatched")
        self.assertEqual(refused.status_code, 400, refused.content)
        self.assertEqual(refused.json()["code"], "handover_code_required")
        job.refresh_from_db()
        self.assertNotEqual(job.status, ManagedJobStatus.DELIVERED)

        ok = self.post_action(
            user=self.printer, role="production", job=job, code="mark_dispatched", handover_code="K7-4Q"
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.DELIVERED)
        self.assertEqual(job.handover_code, "K74Q")
        assignment.refresh_from_db()


class BuyerConfirmsWithHandoverCodeTests(HandoverBase):
    def test_matching_code_completes_the_job(self):
        job, assignment = self.dispatched_job("K7-4Q")
        response = self.confirm(job, handover_code="k7 4q")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], ManagedJobStatus.COMPLETED)
        self.assertEqual(response.json()["message"], "Your job is complete.")
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.COMPLETED)
        self.assertEqual(job.completion_method, ManagedJob.COMPLETION_BUYER_HANDOVER)
        self.assertEqual(job.handover_confirmed_by, self.buyer)
        self.assertIsNotNone(job.handover_confirmed_at)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, JobAssignmentStatus.COMPLETED)

    def test_missing_code_is_refused(self):
        job, _ = self.dispatched_job()
        response = self.confirm(job)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "handover_code_required")
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.DELIVERED)

    def test_wrong_code_is_refused_and_counted(self):
        job, _ = self.dispatched_job()
        response = self.confirm(job, handover_code="K7-4R")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "handover_code_mismatch")
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.DELIVERED)
        self.assertIsNone(job.completed_at)
        self.assertEqual(job.handover_failed_attempts, 1)

    def test_code_locks_after_too_many_wrong_attempts(self):
        job, _ = self.dispatched_job()
        for _attempt in range(MAX_FAILED_ATTEMPTS):
            self.assertEqual(self.confirm(job, handover_code="WRONG1").status_code, 400)
        response = self.confirm(job, handover_code="K7-4Q")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["code"], "handover_locked")
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.DELIVERED)

    def test_buyer_cannot_complete_a_job_that_was_never_dispatched(self):
        job, _ = self.ready_job()
        response = self.confirm(job, handover_code="K7-4Q")
        self.assertEqual(response.status_code, 400, response.content)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.READY)
        self.assertIsNone(job.completed_at)

    def test_ops_also_needs_the_code(self):
        job, _ = self.dispatched_job()
        refused = self.confirm(job, user=self.admin)
        self.assertEqual(refused.status_code, 400, refused.content)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.DELIVERED)
        ok = self.confirm(job, user=self.admin, handover_code="K74Q")
        self.assertEqual(ok.status_code, 200, ok.content)

    def test_printer_and_manager_cannot_confirm_even_with_the_code(self):
        job, _ = self.dispatched_job()
        self.assertEqual(self.confirm(job, user=self.printer, handover_code="K7-4Q").status_code, 403)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.DELIVERED)

    def test_legacy_delivered_job_without_code_is_marked_buyer_no_code(self):
        job = self.make_job(status=ManagedJobStatus.DELIVERED)
        self.make_assignment(job, status=JobAssignmentStatus.READY)
        response = self.confirm(job)
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.COMPLETED)
        self.assertEqual(job.completion_method, ManagedJob.COMPLETION_BUYER_NO_CODE)

    def test_card_i_received_it_with_code(self):
        job, _ = self.dispatched_job("K7-4Q")
        step = resolve_next_step(job, "buyer")
        self.assertEqual(step["next_action"]["key"], "confirm_delivery")
        self.assertEqual(step["next_action"]["label"], "I received it")
        self.assertTrue(step["next_action"]["params"].get("requires_handover_code"))

        wrong = self.post_action(
            user=self.buyer, role="client", job=job, code="confirm_delivery", handover_code="AAAA"
        )
        self.assertEqual(wrong.status_code, 400, wrong.content)
        self.assertEqual(wrong.json()["code"], "handover_code_mismatch")
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.DELIVERED)

        ok = self.post_action(
            user=self.buyer, role="client", job=job, code="confirm_delivery", handover_code="K7-4Q"
        )
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["message"], "Your job is complete.")
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.COMPLETED)
        self.assertEqual(resolve_next_step(job, "buyer")["headline"], "Your job is complete.")


class NoOtherPathToCompletedTests(HandoverBase):
    def test_shop_mark_completed_no_longer_completes_the_job(self):
        job, assignment = self.ready_job()
        self.api.force_authenticate(self.printer)
        response = self.api.post(f"/api/job-assignments/{assignment.id}/mark-completed/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.READY)
        self.assertIsNone(job.completed_at)
        assignment.refresh_from_db()
        self.assertEqual(assignment.status, JobAssignmentStatus.READY)

    def test_shop_ready_queue_no_longer_offers_mark_completed(self):
        _job, assignment = self.ready_job()
        self.api.force_authenticate(self.printer)
        listing = self.api.get("/api/shop/assignments/")
        self.assertEqual(listing.status_code, 200, listing.content)
        row = next(item for item in listing.json() if item["id"] == assignment.id)
        self.assertIn("mark_delivered", row["next_allowed_actions"])
        self.assertNotIn("mark_completed", row["next_allowed_actions"])

    def test_shop_mark_completed_on_delivered_job_still_refused(self):
        job, assignment = self.dispatched_job()
        self.api.force_authenticate(self.printer)
        response = self.api.post(f"/api/job-assignments/{assignment.id}/mark-completed/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.DELIVERED)

    def test_django_admin_cannot_edit_status_or_see_the_code(self):
        model_admin = admin.site._registry[ManagedJob]
        request = RequestFactory().get("/admin/")
        request.user = self.admin
        readonly = model_admin.get_readonly_fields(request)
        for field in ("status", "completed_at", "delivered_at", "completion_method", "handover_confirmed_at"):
            self.assertIn(field, readonly)
        form_fields = model_admin.get_form(request)().fields
        self.assertNotIn("status", form_fields)
        self.assertNotIn("handover_code", form_fields)

    def test_admin_dispute_resolution_records_admin_completion(self):
        job, _ = self.dispatched_job()
        job.status = ManagedJobStatus.DISPUTED
        job.dispute_open = True
        job.exception_status = ManagedJobExceptionStatus.DISPUTE_OPEN
        job.save(update_fields=["status", "dispute_open", "exception_status"])
        self.api.force_authenticate(self.admin)
        response = self.api.post(
            f"/api/admin/disputes/{job.id}/resolve/",
            {"resolution": "dismiss", "reason": "Buyer lost the code; delivery confirmed by phone."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        job.refresh_from_db()
        self.assertEqual(job.status, ManagedJobStatus.COMPLETED)
        self.assertEqual(job.completion_method, ManagedJob.COMPLETION_ADMIN_DISPUTE)
        self.assertIsNotNone(job.completed_at)


class NormaliseTests(HandoverBase):
    def test_normalise(self):
        self.assertEqual(normalize_handover_code(" k7-4q "), "K74Q")
        self.assertEqual(normalize_handover_code(None), "")
